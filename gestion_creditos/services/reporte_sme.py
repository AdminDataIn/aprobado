"""Internal, as-of portfolio review. No provider calls or credit mutations."""
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Prefetch, Q

from gestion_creditos.models import (
    Credito, CuotaAmortizacion, DetalleContablePago, HistorialEstado, HistorialPago, ReestructuracionCredito,
)
from gestion_creditos.services.accounting import get_platform_disbursed_creditos_queryset
from gestion_creditos.services.libranza_rules import calcular_primera_fecha_pago_libranza, sumar_meses_con_dia_ancla


VERSION_CIERRE = 'sme-conciliacion-interna-v3'
ZONA = ZoneInfo('America/Bogota')
CERO = Decimal('0.00')
MEDIO_CENTAVO = Decimal('0.005')
CENTAVO = Decimal('0.01')
ESTADOS_OPERATIVOS = frozenset(('ACTIVO', 'EN_MORA', 'PAGADO'))
PRODUCTOS = (
    ('', 'Todos los productos'),
    ('LIBRANZA', 'Libranza'),
    ('PRESTADORES', 'Prestadores'),
    ('ADELANTO_NOMINA', 'Adelantos de nómina'),
    ('EMPRENDIMIENTO', 'Emprendimiento'),
)
MOTIVOS_BLOQUEO_OFICIAL = (
    'FORMATO_OFICIAL_NO_ACREDITADO', 'AUTORIZACION_DE_REPORTE_NO_CONCILIADA', 'RECLAMOS_NO_CONCILIADOS',
)


class FuenteIncompleta(Exception):
    """Stable reason codes; never expose values or customer details."""


def validar_parametros(corte, producto):
    if type(corte) is not date or corte >= datetime.now(ZONA).date():
        raise ValueError('FECHA_DE_CIERRE_INVALIDA')
    if producto not in dict(PRODUCTOS):
        raise ValueError('PRODUCTO_NO_VALIDO')


def fin_exclusivo(corte):
    return datetime.combine(corte + timedelta(days=1), time.min, tzinfo=ZONA)


@contextmanager
def cierre_solo_lectura():
    # No read on the credit tables before establishing a consistent snapshot.
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise RuntimeError('SME_REQUIERE_POSTGRESQL_Y_TRANSACCION_PROPIA')
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL statement_timeout = '15s'")
        yield


def _producto(credito):
    if credito.linea == 'LIBRANZA' and credito.sme_prestador:
        return 'PRESTADORES'
    return credito.linea


def _obligaciones(corte, producto):
    limite = fin_exclusivo(corte)
    estados = HistorialEstado.objects.filter(
        credito_id=OuterRef('pk'), estado_nuevo__in=ESTADOS_OPERATIVOS, fecha__lt=limite,
    )
    confirmadas = get_platform_disbursed_creditos_queryset(Credito.objects.filter(pk=OuterRef('pk')))
    aplicados = HistorialPago.objects.filter(
        credito_id=OuterRef('pk'), fecha_aplicacion__lt=limite, estado=HistorialPago.EstadoPago.EXITOSO,
    )
    qs = Credito.objects.annotate(
        sme_indicio=Exists(estados), sme_pago_historico=Exists(aplicados), sme_core=Exists(confirmadas),
        sme_prestador=Exists(Credito.objects.filter(pk=OuterRef('pk'), detalle_libranza__es_prestador_servicios=True)),
        sme_prueba=Exists(Credito.objects.filter(pk=OuterRef('pk')).filter(
            Q(detalle_libranza__empresa__nombre__iexact='PRUEBAS DATAIN')
            | Q(detalle_adelanto_nomina__vinculo_laboral__empresa__nombre__iexact='PRUEBAS DATAIN')
        )),
        sme_plan_modificado=Exists(ReestructuracionCredito.objects.filter(credito_id=OuterRef('pk'))),
    ).filter(
        Q(fecha_desembolso__lt=limite) | Q(fecha_desembolso__isnull=True, fecha_solicitud__lt=limite)
        & (Q(sme_indicio=True) | Q(sme_pago_historico=True) | Q(estado__in=ESTADOS_OPERATIVOS))
    )
    if producto == 'PRESTADORES':
        qs = qs.filter(linea='LIBRANZA', sme_prestador=True)
    elif producto == 'LIBRANZA':
        qs = qs.filter(linea=producto, sme_prestador=False)
    elif producto:
        qs = qs.filter(linea=producto)
    return qs.only(
        'id', 'numero_credito', 'linea', 'monto_aprobado', 'fecha_desembolso', 'total_a_pagar', 'plazo', 'valor_cuota',
        'fecha_primera_cuota_forzada', 'estado', 'comision', 'iva_comision',
    ).prefetch_related(
        Prefetch('historial_estados', to_attr='sme_estados', queryset=HistorialEstado.objects.only(
            'credito_id', 'fecha', 'estado_nuevo', 'comprobante_pago', 'clave_idempotencia',
            'motivo', 'usuario_modificacion_id',
        ).order_by('fecha', 'pk')),
        Prefetch('tabla_amortizacion', to_attr='sme_cuotas', queryset=CuotaAmortizacion.objects.only(
            'credito_id', 'numero_cuota', 'fecha_vencimiento', 'capital_a_pagar', 'interes_a_pagar',
            'valor_cuota', 'monto_pagado', 'fecha_pago', 'pagada', 'saldo_capital_pendiente',
        ).order_by('numero_cuota')),
        Prefetch('historial_pagos', to_attr='sme_pagos', queryset=HistorialPago.objects.only(
            'credito_id', 'fecha_aplicacion', 'monto', 'estado',
        ).order_by('fecha_aplicacion', 'pk').prefetch_related(
            Prefetch('detalles_contables', to_attr='sme_imputaciones', queryset=DetalleContablePago.objects.only(
                'pago_id', 'credito_id', 'cuota_id', 'monto_total_aplicado', 'capital_aplicado', 'interes_aplicado',
                'fecha_aplicacion', 'metodologia_calculo',
            )),
        )),
    ).order_by('pk')


def _exigir(ok, motivo):
    if not ok:
        raise FuenteIncompleta(motivo)


def _plan(credito):
    _exigir(not credito.sme_plan_modificado, 'PLAN_REESTRUCTURADO_REQUIERE_CONCILIACION_VERSIONADA')
    cuotas = credito.sme_cuotas
    _exigir(
        bool(cuotas) and [c.numero_cuota for c in cuotas] == list(range(1, len(cuotas) + 1))
        and credito.plazo == len(cuotas) and credito.total_a_pagar is not None and credito.monto_aprobado is not None,
        'PLAN_ORIGINAL_INCOMPLETO',
    )
    capital_plan = sum(c.capital_a_pagar for c in cuotas)
    total_plan = sum(c.valor_cuota for c in cuotas)
    diferencia_total = total_plan - credito.total_a_pagar
    libranza_legacy = (
        credito.linea == 'LIBRANZA' and not credito.sme_prestador
        and credito.comision is not None and credito.iva_comision is not None
        and all(c.valor_cuota == credito.valor_cuota for c in cuotas)
    )
    capital_financiado = (
        credito.monto_aprobado + credito.comision + credito.iva_comision
        if libranza_legacy else capital_plan
    )
    diferencia_capital = capital_plan - capital_financiado
    # Activación legacy persiste un total calculado antes de redondear cada
    # cuota a centavos. Conciliar las dos magnitudes, nunca corregir los datos.
    max_total = (len(cuotas) + 1) * MEDIO_CENTAVO
    max_capital = (len(cuotas) + 3) * MEDIO_CENTAVO
    _exigir(
        capital_financiado >= credito.monto_aprobado > CERO and (
            diferencia_total == CERO and diferencia_capital == CERO or
            libranza_legacy and abs(diferencia_total) <= max_total and abs(diferencia_capital) <= max_capital
        ),
        'PLAN_NO_CONCILIA_CON_CONDICIONES',
    )
    if credito.linea == 'LIBRANZA':
        # Reuse the Core anchor to detect unversioned date changes or legacy ambiguity.
        primera = calcular_primera_fecha_pago_libranza(
            fecha_aprobacion=credito.fecha_desembolso.astimezone(ZONA).date(),
            fecha_forzada=credito.fecha_primera_cuota_forzada,
        )
        _exigir(all(c.fecha_vencimiento == sumar_meses_con_dia_ancla(primera, i, primera.day)
                    for i, c in enumerate(cuotas)), 'CALENDARIO_HISTORICO_NO_ACREDITADO')
    saldo_previo = capital_financiado
    tolerancia = CENTAVO if libranza_legacy else CERO
    for c in cuotas:
        _exigir(c.capital_a_pagar >= CERO and c.interes_a_pagar >= CERO and c.valor_cuota > CERO
                and abs(c.valor_cuota - c.capital_a_pagar - c.interes_a_pagar) <= tolerancia
                and abs(saldo_previo - c.capital_a_pagar - c.saldo_capital_pendiente) <= tolerancia,
                'DESGLOSE_DEL_PLAN_NO_CONCILIA')
        saldo_previo = c.saldo_capital_pendiente
    _exigir(saldo_previo == CERO, 'CAPITAL_RESIDUAL_EN_ULTIMA_CUOTA')
    return capital_financiado, capital_plan, diferencia_total, diferencia_capital


def _cierres_auditados(credito, por_cuota, por_pago, recibido, limite):
    cierres, cierres_al_corte = set(), set()
    for evento in credito.sme_estados:
        if not (evento.clave_idempotencia or '').startswith('reconciliacion-redondeo:cuota:'):
            continue
        try:
            datos = json.loads(evento.motivo or '{}')
            cuota, pago = por_cuota[datos['cuota_id']], por_pago[datos['pago_id']]
            diferencia = Decimal(datos['diferencia'])
            valido = (
                evento.clave_idempotencia == f'reconciliacion-redondeo:cuota:{cuota.pk}'
                and datos['evento'] == 'RECONCILIACION_REDONDEO' and datos['resultado'] == 'RECONCILIADA'
                and datos['credito_id'] == credito.pk and datos['numero_cuota'] == cuota.numero_cuota
                and evento.usuario_modificacion_id is not None and datos['actor_id'] == evento.usuario_modificacion_id
                and pago.estado == HistorialPago.EstadoPago.EXITOSO and pago.fecha_aplicacion <= evento.fecha
                and cuota.fecha_pago == pago.fecha_aplicacion and cuota.pagada
                and any(d.cuota_id == cuota.pk for d in pago.sme_imputaciones)
                and recibido[cuota.pk] == Decimal(datos['monto_pagado_real'])
                and CERO < diferencia <= Decimal(datos['tolerancia'])
                and cuota.valor_cuota - recibido[cuota.pk] == diferencia
            )
        except (KeyError, ArithmeticError, TypeError, ValueError):
            continue
        if valido:
            cierres.add(cuota.pk)
            if evento.fecha < limite:
                cierres_al_corte.add(cuota.pk)
    return cierres, cierres_al_corte


def _imputaciones(credito, limite):
    por_cuota = {c.pk: c for c in credito.sme_cuotas}
    por_pago = {p.pk: p for p in credito.sme_pagos}
    al_corte = defaultdict(lambda: [CERO, CERO, CERO])
    total_actual_contabilizado = defaultdict(lambda: CERO)
    redondeos = Counter()
    redondeos_al_corte = Counter()
    efectivos_con_residuo_posterior = 0
    ultimo = None
    posteriores = False
    for pago in credito.sme_pagos:
        antes = pago.fecha_aplicacion < limite
        if pago.estado != HistorialPago.EstadoPago.EXITOSO:
            _exigir(not pago.sme_imputaciones, 'REVERSO_O_CAMBIO_DE_ESTADO_SIN_TRAZA')
            continue
        posteriores |= not antes
        if not antes and not pago.sme_imputaciones:
            # No payment after closing contributes to the historic balance.
            continue
        _exigir(pago.monto > CERO, 'REVERSO_O_MOVIMIENTO_NO_ACREDITADO')
        _exigir(pago.sme_imputaciones, 'PAGO_EFECTIVO_SIN_DETALLE_CONTABLE')
        total_aplicado = sum(d.monto_total_aplicado for d in pago.sme_imputaciones)
        if total_aplicado != pago.monto:
            _exigir(not antes and CERO < total_aplicado < pago.monto, 'MONTO_PAGO_NO_CONCILIA_CON_IMPUTACIONES')
            efectivos_con_residuo_posterior += 1
        for detalle in pago.sme_imputaciones:
            _exigir(detalle.credito_id == credito.pk and detalle.cuota_id in por_cuota
                    and detalle.fecha_aplicacion == pago.fecha_aplicacion, 'IMPUTACION_NO_CORRELACIONADA')
            _exigir(detalle.capital_aplicado >= CERO and detalle.interes_aplicado >= CERO,
                    'DESGLOSE_O_REVERSO_NO_CONCILIADO')
            brecha = detalle.monto_total_aplicado - detalle.capital_aplicado - detalle.interes_aplicado
            if brecha:
                cuota = por_cuota[detalle.cuota_id]
                # The Core caps capital and interest independently after the legacy plan is rounded.
                _exigir(credito.linea == 'LIBRANZA' and not credito.sme_prestador
                        and detalle.metodologia_calculo == 'CUOTA_INTERES_PRIMERO'
                        and brecha == cuota.valor_cuota - cuota.capital_a_pagar - cuota.interes_a_pagar == CENTAVO,
                        'DESGLOSE_O_REVERSO_NO_CONCILIADO')
                redondeos[detalle.cuota_id] += 1
                if antes:
                    redondeos_al_corte[detalle.cuota_id] += 1
            if antes:
                al_corte[detalle.cuota_id][0] += detalle.capital_aplicado
                al_corte[detalle.cuota_id][1] += detalle.interes_aplicado
                al_corte[detalle.cuota_id][2] += detalle.monto_total_aplicado
                ultimo = pago.fecha_aplicacion.astimezone(ZONA).date()
            total_actual_contabilizado[detalle.cuota_id] += detalle.monto_total_aplicado
    cierres, cierres_al_corte = _cierres_auditados(credito, por_cuota, por_pago, total_actual_contabilizado, limite)
    for cuota in credito.sme_cuotas:
        observado = cuota.monto_pagado
        aplicado = total_actual_contabilizado[cuota.pk]
        _exigir(aplicado <= cuota.valor_cuota, 'IMPUTACION_EXCEDE_VALOR_CUOTA')
        _exigir(not redondeos[cuota.pk] or (redondeos[cuota.pk] == 1 and aplicado == cuota.valor_cuota),
                'DESGLOSE_O_REVERSO_NO_CONCILIADO')
        _exigir(not redondeos_al_corte[cuota.pk] or (al_corte[cuota.pk][2] == cuota.valor_cuota),
                'DESGLOSE_O_REVERSO_NO_CONCILIADO')
        _exigir((observado if observado is not None else CERO) == aplicado
                and cuota.pagada == (aplicado == cuota.valor_cuota or cuota.pk in cierres),
                'CUOTA_CON_MOVIMIENTO_SIN_SOPORTE_CONTABLE')
    return al_corte, ultimo, posteriores, cierres_al_corte, sum(redondeos_al_corte.values()), efectivos_con_residuo_posterior


def _desembolso_acreditado(credito, historial):
    estados_transferencia = [e.fecha for e in historial if e.estado_nuevo == 'PENDIENTE_TRANSFERENCIA']
    activaciones = [e for e in historial if e.estado_nuevo == 'ACTIVO' and e.comprobante_pago]
    return bool(credito.fecha_desembolso and credito.sme_core and activaciones and estados_transferencia
                and min(estados_transferencia) <= credito.fecha_desembolso <= activaciones[0].fecha)


def _conciliar(credito, corte, historial, desembolso):
    limite = fin_exclusivo(corte)
    _exigir(credito.fecha_desembolso is not None, 'SIN_FECHA_DE_DESEMBOLSO_ACREDITADA')
    _exigir(desembolso, 'SIN_TRAZA_DE_DESEMBOLSO_AL_CORTE')
    estado = historial[-1].estado_nuevo
    _exigir(estado in ESTADOS_OPERATIVOS, 'NOVEDAD_HISTORICA_REQUIERE_CONCILIACION')
    capital, capital_plan, diferencia_total, diferencia_capital = _plan(credito)
    imputadas, ultimo, posteriores, cierres, redondeos, residuales_posteriores = _imputaciones(credito, limite)
    capital_pendiente, saldo_total, saldo_vencido, pagadas = capital_plan, CERO, CERO, 0
    vencimientos = []
    for cuota in credito.sme_cuotas:
        aplicado_capital, aplicado_interes, efectivo = imputadas[cuota.pk]
        _exigir(aplicado_capital <= cuota.capital_a_pagar and aplicado_interes <= cuota.interes_a_pagar,
                'IMPUTACION_EXCEDE_COMPONENTE')
        capital_pendiente -= aplicado_capital
        # An operational closing does not convert the documented residual into received cash.
        resto = cuota.valor_cuota - efectivo
        saldo_total += resto
        if resto == CERO:
            pagadas += 1
        elif cuota.pk in cierres:
            raise FuenteIncompleta('CIERRE_OPERATIVO_AUDITADO_CON_RESIDUO_FINANCIERO')
        elif cuota.fecha_vencimiento < corte:
            saldo_vencido += resto
            vencimientos.append(cuota.fecha_vencimiento)
    _exigir((estado == 'PAGADO') == (saldo_total == CERO), 'ESTADO_DE_CIERRE_NO_CONCILIA')
    cuotas_del_mes = [q.fecha_vencimiento for q in credito.sme_cuotas
                     if (q.fecha_vencimiento.year, q.fecha_vencimiento.month) == (corte.year, corte.month)]
    posteriores = posteriores or any(e.fecha >= limite and e.estado_nuevo in ('PAGADO', 'ANULADO')
                                      for e in credito.sme_estados)
    return {
        'categoria': 'PAGADA' if estado == 'PAGADO' else ('VENCIDA' if vencimientos else 'AL_DIA'),
        'monto_aprobado': credito.monto_aprobado, 'cuota_pactada': credito.valor_cuota,
        'capital_financiado': capital, 'capital_pendiente': capital_pendiente,
        'saldo_pendiente': saldo_total, 'saldo_vencido': saldo_vencido,
        'diferencia_plan_vs_total_aprobado': diferencia_total,
        'diferencia_capital_plan_vs_financiado': diferencia_capital,
        'cuotas_con_centavo_de_redondeo_contable': redondeos,
        'pagos_posteriores_con_residuo_no_imputado': residuales_posteriores,
        'cuotas_pactadas': len(credito.sme_cuotas), 'cuotas_pagadas': pagadas,
        'cuotas_pendientes': len(credito.sme_cuotas) - pagadas, 'cuotas_vencidas': len(vencimientos),
        'dias_mora': (corte - min(vencimientos)).days if vencimientos else 0,
        'ultima_aplicacion': ultimo, 'fecha_vencimiento_final': credito.sme_cuotas[-1].fecha_vencimiento,
        'fecha_cuota_mes_corte': cuotas_del_mes[0] if len(cuotas_del_mes) == 1 else None,
        'novedad_posterior': posteriores,
    }


def motivos_reporte(datos):
    """Keep remittance eligibility distinct from the genuine financial state."""
    motivos = list(MOTIVOS_BLOQUEO_OFICIAL)
    if datos['categoria'] == 'VENCIDA':
        motivos.append('COMUNICACION_PREVIA_Y_CUANTIA_NO_CONCILIADAS')
    if datos['categoria'] == 'PAGADA':
        motivos.append('TRATAMIENTO_DE_OBLIGACIONES_CERRADAS_NO_ACREDITADO')
    if datos['novedad_posterior']:
        motivos.append('NOVEDAD_POSTERIOR_REQUIERE_REVISION_ANTES_DE_REMITIR')
    return motivos


def _resumen(filas):
    categorias = Counter(r['categoria'] for r in filas)
    conciliadas = [r for r in filas if r['categoria'] != 'NO_CONCILIADA']
    return {
        'obligaciones_inventariadas': len(filas),
        'desembolsos_acreditados': sum(r['desembolso_acreditado'] for r in filas),
        'conciliadas': len(conciliadas), 'incidencias_financieras': categorias['NO_CONCILIADA'],
        'reportables': 0, 'con_excepciones': len(filas),
        'al_dia': categorias['AL_DIA'], 'vencidas': categorias['VENCIDA'], 'pagadas': categorias['PAGADA'],
        'capital_pendiente_conciliado': sum((r['capital_pendiente'] for r in conciliadas), CERO) if conciliadas else None,
        'saldo_vencido_conciliado': sum((r['saldo_vencido'] for r in conciliadas), CERO) if conciliadas else None,
    }


def construir_cierre(corte, producto='', *, ids_revision=None):
    validar_parametros(corte, producto)
    filas, excluidas = [], 0
    prueba_ids = set(getattr(settings, 'SME_CREDITOS_DE_PRUEBA_ACREDITADOS', ()))
    limite = fin_exclusivo(corte)
    with cierre_solo_lectura():
        obligaciones = _obligaciones(corte, producto)
        if ids_revision is not None:
            obligaciones = obligaciones.filter(pk__in=ids_revision)
        for credito in obligaciones.iterator(chunk_size=100):
            if credito.sme_prueba or credito.pk in prueba_ids:
                excluidas += 1
                continue
            historial = [e for e in credito.sme_estados if e.fecha < limite]
            desembolso = _desembolso_acreditado(credito, historial)
            fila = {'credito_id_interno': credito.pk, 'numero_obligacion_interno': credito.numero_credito,
                    'producto': _producto(credito),
                    'desembolso_acreditado': desembolso,
                    'fecha_desembolso_registrada': (credito.fecha_desembolso.astimezone(ZONA).date()
                                                    if credito.fecha_desembolso else None)}
            try:
                fila.update(_conciliar(credito, corte, historial, desembolso))
            except FuenteIncompleta as exc:
                fila.update(categoria='NO_CONCILIADA', incidencia_financiera=str(exc),
                            impedimentos_reporte=['CIERRE_FINANCIERO_NO_CONCILIADO', *MOTIVOS_BLOQUEO_OFICIAL])
            else:
                fila['impedimentos_reporte'] = motivos_reporte(fila)
            filas.append(fila)
    return {
        'version': VERSION_CIERRE, 'corte': corte, 'zona': ZONA.key, 'producto': producto,
        'tipo': 'CONCILIACION_INTERNA_NO_REMITIR', 'total': _resumen(filas),
        'por_producto': {p: _resumen([r for r in filas if r['producto'] == p])
                         for p in sorted({r['producto'] for r in filas})},
        'excluidas_pruebas_identificadas': excluidas, 'filas': filas,
    }

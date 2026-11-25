"""Review a prior October SME snapshot, using PostgreSQL read-only sources."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import sys
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Q

from gestion_creditos.models import (
    Credito, CuotaAmortizacion, DetalleContablePago, HistorialEstado, HistorialPago,
    LotePagoEmpresa, Pagare, PagoBREB, PagoBREBDetalle, ReestructuracionCredito, WompiIntent,
)


ZONA = ZoneInfo('America/Bogota')
CORTE = date(2026, 10, 31)
LIMITE = datetime.combine(CORTE + timedelta(days=1), time.min, tzinfo=ZONA)
CERO = Decimal('0')


def agrupar(query, field, *columns):
    grupos = defaultdict(list)
    for fila in query.order_by('pk').values(field, *columns).iterator():
        grupos[fila[field]].append(fila)
    return grupos


def clasificar_desembolso(credito, estados):
    fecha = credito['fecha_desembolso']
    if fecha is not None and fecha >= LIMITE:
        return 'DESEMBOLSO_POSTERIOR_AL_CORTE'
    anteriores = [e for e in estados if e['fecha'] < LIMITE]
    operativos = [e for e in anteriores if e['estado_nuevo'] in ('ACTIVO', 'EN_MORA', 'PAGADO')]
    if not operativos:
        return 'SIN_ACTIVACION_HISTORICA_ACREDITADA'
    if not fecha:
        return 'FECHA_DE_DESEMBOLSO_NO_ACREDITADA'
    activaciones = [e for e in operativos if e['estado_nuevo'] == 'ACTIVO' and e['comprobante_pago']]
    if not activaciones:
        return 'SIN_COMPROBANTE_DE_ACTIVACION_AL_CORTE'
    if not any(e['estado_nuevo'] == 'PENDIENTE_TRANSFERENCIA' and e['fecha'] <= fecha for e in anteriores):
        return 'SIN_TRANSICION_PREVIA_DE_TRANSFERENCIA'
    return 'CRONOLOGIA_DE_DESEMBOLSO_NO_CONCILIADA'


def diferencias_pago(pagos, detalles, cuotas):
    incidencias = Counter()
    por_cuota = Counter()
    residuales = []
    cierres = []
    for pago in pagos:
        ds = detalles[pago['id']]
        antes = pago['fecha_aplicacion'] < LIMITE
        if pago['estado'] != 'EXITOSO':
            if ds:
                incidencias['ESTADO_NO_EXITOSO_CON_ASIENTOS'] += 1
            continue
        if antes and not ds:
            incidencias['PAGO_EXITOSO_SIN_ASIENTOS_AL_CORTE'] += 1
        diferencia = pago['monto'] - sum((d['monto_total_aplicado'] for d in ds), CERO)
        if ds and diferencia:
            incidencias['PAGO_MAYOR_QUE_ASIENTOS' if diferencia > CERO else 'ASIENTOS_MAYORES_QUE_PAGO'] += 1
            residuales.append({'pago_id': pago['id'], 'delta_pago_menos_asientos': diferencia,
                               'al_corte': antes, 'origen': pago['origen_registro']})
        for d in ds:
            por_cuota[d['cuota_id']] += d['monto_total_aplicado']
            componentes = d['capital_aplicado'] + d['interes_aplicado']
            if d['capital_aplicado'] < CERO or d['interes_aplicado'] < CERO or d['monto_total_aplicado'] < CERO:
                incidencias['COMPONENTE_CONTABLE_NEGATIVO'] += 1
            elif componentes != d['monto_total_aplicado']:
                delta = abs(componentes - d['monto_total_aplicado'])
                incidencias['DESGLOSE_DIFIERE_UN_CENTAVO' if delta == Decimal('0.01')
                            else 'DESGLOSE_DIFIERE_MAS_DE_UN_CENTAVO'] += 1
            if d['fecha_aplicacion'] != pago['fecha_aplicacion']:
                incidencias['FECHA_DE_ASIENTO_DIFERENTE'] += 1
            if d['cuota_id'] is None:
                incidencias['ASIENTO_SIN_CUOTA'] += 1
    for cuota in cuotas:
        aplicado = por_cuota[cuota['id']]
        registrado = cuota['monto_pagado'] or CERO
        if aplicado != registrado:
            incidencias['CUOTA_MAYOR_QUE_ASIENTOS' if registrado > aplicado else 'ASIENTOS_MAYORES_QUE_CUOTA'] += 1
        if cuota['pagada'] and registrado == aplicado and aplicado < cuota['valor_cuota']:
            incidencias['CUOTA_CERRADA_CON_RESIDUO'] += 1
            cierres.append({'cuota_id': cuota['id'], 'residuo': cuota['valor_cuota'] - aplicado})
    return dict(incidencias), residuales, cierres


def consulta(filas):
    ids = {f['credito_id_interno'] for f in filas}
    foco = {f['credito_id_interno'] for f in filas if f['incidencia'] or f['categoria'] == 'VENCIDA'}
    creditos = {c['id']: c for c in Credito.objects.filter(id__in=ids).values(
        'id', 'estado', 'fecha_desembolso', 'monto_aprobado', 'detalle_libranza__empresa_id',
    )}
    if ids != set(creditos):
        raise RuntimeError('SNAPSHOT_NO_COINCIDE_CON_UNIVERSO_PREVIO')
    estados = agrupar(HistorialEstado.objects.filter(credito_id__in=foco), 'credito_id',
                      'fecha', 'estado_nuevo', 'comprobante_pago', 'clave_idempotencia')
    pagos = agrupar(HistorialPago.objects.filter(credito_id__in=foco), 'credito_id',
                    'id', 'monto', 'estado', 'fecha_pago', 'fecha_aplicacion', 'origen_registro',
                    'lote_pago_id', 'empresa_origen_id', 'wompi_intento_id', 'comprobante',
                    'detalle_breb__pago_breb_id')
    notas_redondeo = set(HistorialPago.objects.filter(credito_id__in=foco,
        notas__contains='cerrada por tolerancia de redondeo').values_list('credito_id', flat=True))
    detalles = agrupar(DetalleContablePago.objects.filter(pago__credito_id__in=foco), 'pago_id',
                       'cuota_id', 'fecha_aplicacion', 'monto_total_aplicado', 'capital_aplicado', 'interes_aplicado')
    cuotas = agrupar(CuotaAmortizacion.objects.filter(credito_id__in=foco), 'credito_id',
                     'id', 'monto_pagado', 'pagada', 'valor_cuota', 'fecha_pago', 'fecha_vencimiento',
                     'fecha_ultimo_aviso_usuario_mora')
    planes = agrupar(ReestructuracionCredito.objects.filter(credito_id__in=foco), 'credito_id',
                      'fecha_reestructuracion', 'tipo_abono', 'pago_relacionado_id')
    wompi = agrupar(WompiIntent.objects.filter(credito_id__in=foco, status__in=('APPROVED', 'PENDING')),
                      'credito_id', 'id', 'status', 'created_at', 'updated_at')
    breb = agrupar(PagoBREBDetalle.objects.filter(credito_id__in=foco), 'credito_id', 'historial_pago_id',
                   'pago_breb_id', 'pago_breb__creado_en', 'pago_breb__revisado_en',
                   'pago_breb__fecha_pago_reportada', 'pago_breb__estado')
    for b in PagoBREB.objects.filter(credito_id__in=foco, detalles__isnull=True).values(
        'pk', 'credito_id', 'creado_en', 'revisado_en', 'fecha_pago_reportada', 'estado',
    ):
        breb[b['credito_id']].append(dict(historial_pago_id=None, pago_breb_id=b['pk'],
            pago_breb__creado_en=b['creado_en'], pago_breb__revisado_en=b['revisado_en'],
            pago_breb__fecha_pago_reportada=b['fecha_pago_reportada'], pago_breb__estado=b['estado']))
    lotes = {l['id']: l for l in LotePagoEmpresa.objects.filter(empresa_id__in={
        creditos[i]['detalle_libranza__empresa_id'] for i in foco if creditos[i]['detalle_libranza__empresa_id']
    }).values('id', 'empresa_id', 'estado', 'creado_en', 'comprobante')}
    pagares = dict(Pagare.objects.filter(credito_id__in=ids, estado='SIGNED', fecha_firma__lt=LIMITE)
                   .values_list('credito_id', 'version_plantilla'))
    soporte_v1 = Pagare.objects.filter(credito_id__in=pagares).exclude(
        Q(archivo_pdf_firmado='') | Q(archivo_pdf_firmado__isnull=True)).count()

    casos = []
    resumen = Counter()
    for f in filas:
        cid = f['credito_id_interno']
        if cid not in foco:
            continue
        c = creditos[cid]
        ps = pagos[cid]
        anteriores = [p for p in ps if p['fecha_aplicacion'] < LIMITE and p['estado'] == 'EXITOSO']
        posteriores = [p for p in ps if p['fecha_aplicacion'] >= LIMITE and p['estado'] == 'EXITOSO']
        linked = {p['wompi_intento_id'] for p in ps if p['estado'] == 'EXITOSO'}
        b = [p for p in breb[cid] if p['pago_breb__estado'] in ('APROBADO', 'PENDIENTE_VERIFICACION')
             and not p['historial_pago_id'] and not any(q['detalle_breb__pago_breb_id'] == p['pago_breb_id']
                                                       and q['estado'] == 'EXITOSO' for q in ps)]
        inc, delta, cierres = diferencias_pago(ps, detalles, cuotas[cid])
        causa = f['incidencia']
        if causa == 'SIN_TRAZA_DE_DESEMBOLSO_AL_CORTE':
            causa = clasificar_desembolso(c, estados[cid])
        if causa:
            resumen[causa] += 1
        pagador = [p for p in anteriores if p['lote_pago_id'] or p['empresa_origen_id']]
        tercero_giro = [p for p in pagador if lotes.get(p['lote_pago_id'], {}).get('comprobante')]
        borradores_empresa = [l['id'] for l in lotes.values()
                             if l['empresa_id'] == c['detalle_libranza__empresa_id']
                             and l['estado'] != 'PROCESADO' and l['creado_en'] < LIMITE]
        pendientes = [p for p in ps if p['estado'] == 'PENDIENTE']
        wompi_sin_aplicar = [w for w in wompi[cid] if w['status'] == 'APPROVED' and w['id'] not in linked]
        casos.append({
            **f, 'causa': causa, 'estado_core_actual': c['estado'], 'indicadores_contables': inc,
            'diferencias_pago': delta, 'cierres_con_residuo': cierres,
            'reconciliacion_redondeo_auditada': sum(bool(e['clave_idempotencia'] and
                e['clave_idempotencia'].startswith('reconciliacion-redondeo:cuota:')) for e in estados[cid]),
            'nota_redondeo_en_pago': cid in notas_redondeo,
            'reestructuraciones': [{**p, 'anterior_al_corte': p['fecha_reestructuracion'] < LIMITE}
                                   for p in planes[cid]],
            'pagos_aplicados_al_corte': len(anteriores), 'pagos_aplicados_posteriores': len(posteriores),
            'aplicaciones_backdate_registradas_despues': sum(p['fecha_pago'] >= LIMITE for p in anteriores),
            'pago_parcial_al_corte': any(q['fecha_vencimiento'] <= CORTE and
                CERO < sum(d['monto_total_aplicado'] for p in anteriores for d in detalles[p['id']]
                           if d['cuota_id'] == q['id']) < q['valor_cuota'] for q in cuotas[cid]),
            'pagos_ligados_a_pagador_al_corte': len(pagador),
            'pagos_con_lote_y_referencia_de_comprobante': len(tercero_giro),
            'lotes_no_finalizados_empresa_no_atribuidos_a_obligacion': borradores_empresa,
            'pagos_pendientes_registrados_al_corte': sum(p['fecha_pago'] < LIMITE for p in pendientes),
            'pagos_pendientes_actuales': len(pendientes), 'breb_sin_aplicacion_actual': len(b),
            'breb_reportado_sin_aplicacion_actual_y_recibido_al_corte': sum(
                p['pago_breb__creado_en'] < LIMITE for p in b),
            'wompi_aprobado_sin_aplicacion_actual': len(wompi_sin_aplicar),
            'marcas_operativas_aviso_al_corte': sum(bool(q['fecha_ultimo_aviso_usuario_mora'] and
                q['fecha_ultimo_aviso_usuario_mora'] < LIMITE) for q in cuotas[cid]),
            'marcas_operativas_aviso_actuales': sum(bool(q['fecha_ultimo_aviso_usuario_mora']) for q in cuotas[cid]),
        })
    return {
        'corte': CORTE, 'casos': casos, 'causas_primarias': dict(resumen),
        'firmas_pagares_al_corte_por_version': dict(Counter(pagares.values())),
        'pagares_firmados_con_archivo_referenciado': soporte_v1,
        'vencidas_monto_original_hasta_15pct_smlmv_2026_provisional': sum(
            f['categoria'] == 'VENCIDA' and creditos[f['credito_id_interno']]['monto_aprobado'] is not None
            and creditos[f['credito_id_interno']]['monto_aprobado'] <= Decimal('262635.75') for f in filas),
        'lotes_en_empresas_del_foco': dict(Counter(l['estado'] for l in lotes.values())),
    }


def ejecutar(filas):
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise RuntimeError('REQUIERE_POSTGRESQL_Y_TRANSACCION_PROPIA')
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL statement_timeout = '15s'")
        return consulta(filas)


if __name__ == '__main__':
    print(json.dumps(ejecutar(json.load(sys.stdin)), cls=DjangoJSONEncoder))

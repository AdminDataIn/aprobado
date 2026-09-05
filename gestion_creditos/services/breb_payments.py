import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import (
    ConfiguracionPagoBREB,
    Credito,
    CuotaAmortizacion,
    HistorialPago,
    PagoBREB,
    PagoBREBDetalle,
    calcular_hash_archivo,
)


CENTAVO = Decimal('0.01')
ESTADOS_CREDITO_REPORTABLES = {
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
}


def obtener_configuracion_breb_activa():
    return ConfiguracionPagoBREB.objects.filter(activo=True).first()


def _perfil_pagador(usuario):
    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return None
    return getattr(usuario, 'perfil_pagador', None)


def _exigir_pagador_empresa(*, usuario, empresa):
    perfil = _perfil_pagador(usuario)
    if not perfil or not perfil.es_pagador or perfil.empresa_id != empresa.pk:
        raise PermissionDenied('Solo el pagador de la empresa puede reportar pagos BRE-B.')
    return perfil


def usuario_puede_revisar_pago_breb(*, usuario, pago_breb):
    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return False
    if _perfil_pagador(usuario) is not None:
        return False
    return bool(usuario.is_staff and usuario.has_perm('gestion_creditos.review_pagobreb'))


def usuario_puede_consultar_pago_breb(*, usuario, pago_breb):
    if usuario_puede_revisar_pago_breb(usuario=usuario, pago_breb=pago_breb):
        return True
    perfil = _perfil_pagador(usuario)
    return bool(
        perfil
        and perfil.es_pagador
        and perfil.empresa_id == pago_breb.empresa_id
        and pago_breb.usuario_id == usuario.id
    )


def _exigir_permiso_revision(*, usuario, pago_breb):
    if not usuario_puede_revisar_pago_breb(usuario=usuario, pago_breb=pago_breb):
        raise PermissionDenied('La revision BRE-B es exclusiva del equipo interno autorizado.')
    if usuario.id == pago_breb.usuario_id:
        raise PermissionDenied('No puedes aprobar tu propio reporte de pago.')


def _normalizar_obligaciones(obligaciones):
    resultado = []
    for item in obligaciones or []:
        if isinstance(item, dict):
            credito = item.get('credito')
            credito_id = item.get('credito_id') or getattr(credito, 'pk', None)
            cuota = item.get('cuota')
            cuota_id = item.get('cuota_id') or getattr(cuota, 'pk', None)
            valor = item.get('valor_reportado', item.get('monto'))
        else:
            credito_id = getattr(item, 'pk', item)
            cuota_id = None
            valor = None
        try:
            credito_id = int(credito_id)
            cuota_id = int(cuota_id) if cuota_id is not None else None
        except (TypeError, ValueError):
            raise ValidationError('La seleccion de obligaciones no es valida.')
        resultado.append({
            'credito_id': credito_id,
            'cuota_id': cuota_id,
            'valor_reportado': valor,
        })
    if not resultado:
        raise ValidationError('Debes seleccionar al menos una obligacion.')
    return resultado


def _decimal_positivo(valor, *, mensaje):
    try:
        monto = Decimal(str(valor)).quantize(CENTAVO)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(mensaje)
    if monto <= Decimal('0.00'):
        raise ValidationError(mensaje)
    return monto


def _resolver_obligaciones_bajo_lock(*, empresa, obligaciones):
    normalizadas = _normalizar_obligaciones(obligaciones)
    credito_ids = sorted({item['credito_id'] for item in normalizadas})
    creditos = Credito.objects.select_for_update(of=('self',)).filter(
        pk__in=credito_ids,
    ).select_related(
        'detalle_libranza__empresa',
        'detalle_adelanto_nomina__vinculo_laboral__empresa',
        'usuario',
    ).order_by('pk')
    creditos_map = {credito.pk: credito for credito in creditos}
    if len(creditos_map) != len(credito_ids):
        raise ValidationError('Una o mas obligaciones seleccionadas no existen.')

    resultado = []
    cuotas_vistas = set()
    acumulado_por_credito = {}
    for item in normalizadas:
        credito = creditos_map[item['credito_id']]
        if credito.empresa_relacionada != empresa:
            raise PermissionDenied('Una obligacion seleccionada no pertenece a tu empresa.')
        if credito.linea != Credito.LineaCredito.LIBRANZA:
            raise ValidationError('BRE-B agrupado solo esta disponible para obligaciones de Libranza.')
        if credito.estado not in ESTADOS_CREDITO_REPORTABLES:
            raise ValidationError(
                f'El credito {credito.numero_credito} no admite pagos BRE-B en su estado actual.'
            )

        cuotas = CuotaAmortizacion.objects.select_for_update().filter(
            credito=credito,
            pagada=False,
        ).order_by('numero_cuota', 'pk')
        cuota = cuotas.filter(pk=item['cuota_id']).first() if item['cuota_id'] else cuotas.first()
        if cuota is None:
            raise ValidationError(f'El credito {credito.numero_credito} no tiene una cuota pendiente.')
        if cuota.pk in cuotas_vistas:
            raise ValidationError('La misma cuota no puede incluirse dos veces en un reporte BRE-B.')
        cuotas_vistas.add(cuota.pk)

        pendiente_cuota = max(
            Decimal('0.00'),
            (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00')),
        ).quantize(CENTAVO)
        saldo_credito = max(Decimal('0.00'), credito.saldo_pendiente or Decimal('0.00')).quantize(CENTAVO)
        maximo_aplicable = min(pendiente_cuota, saldo_credito)
        if maximo_aplicable <= Decimal('0.00'):
            raise ValidationError(f'La cuota del credito {credito.numero_credito} ya no tiene saldo aplicable.')

        monto = maximo_aplicable
        if item['valor_reportado'] not in (None, ''):
            monto = _decimal_positivo(
                item['valor_reportado'],
                mensaje=f'El valor del credito {credito.numero_credito} no es valido.',
            )
            if monto > maximo_aplicable:
                raise ValidationError(
                    f'El valor del credito {credito.numero_credito} supera la obligacion pendiente.'
                )

        acumulado = acumulado_por_credito.get(credito.pk, Decimal('0.00')) + monto
        if acumulado > saldo_credito:
            raise ValidationError(
                f'La suma reportada para {credito.numero_credito} supera su saldo pendiente.'
            )
        acumulado_por_credito[credito.pk] = acumulado

        resultado.append({
            'credito': credito,
            'cuota': cuota,
            'valor_reportado': monto,
        })
    return resultado


def _fingerprint_reporte(
    *,
    empresa,
    usuario,
    fecha_pago_reportada,
    hash_comprobante,
    obligaciones,
    referencia_reportada,
):
    contenido = {
        'empresa': empresa.pk,
        'usuario': usuario.pk,
        'fecha': fecha_pago_reportada.isoformat(),
        'comprobante': hash_comprobante,
        'referencia': referencia_reportada,
        'obligaciones': sorted(
            (item['cuota'].pk, format(item['valor_reportado'], '.2f'))
            for item in obligaciones
        ),
    }
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def _fingerprint_reporte_legacy(
    *, empresa, usuario, fecha_pago_reportada, hash_comprobante, obligaciones
):
    contenido = {
        'empresa': empresa.pk,
        'usuario': usuario.pk,
        'fecha': fecha_pago_reportada.isoformat(),
        'comprobante': hash_comprobante,
        'obligaciones': sorted(
            (item['cuota'].pk, format(item['valor_reportado'], '.2f'))
            for item in obligaciones
        ),
    }
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


@transaction.atomic
def reportar_pago_breb(
    *,
    empresa,
    usuario,
    configuracion,
    obligaciones,
    fecha_pago_reportada,
    comprobante,
    referencia_reportada='',
    notas='',
):
    _exigir_pagador_empresa(usuario=usuario, empresa=empresa)
    if fecha_pago_reportada > timezone.localdate():
        raise ValidationError('La fecha reportada no puede estar en el futuro.')

    configuracion = ConfiguracionPagoBREB.objects.select_for_update().get(pk=configuracion.pk)
    if not configuracion.activo:
        raise ValidationError('El pago por BRE-B no esta disponible en este momento.')

    detalles = _resolver_obligaciones_bajo_lock(empresa=empresa, obligaciones=obligaciones)
    valor_total = sum((item['valor_reportado'] for item in detalles), Decimal('0.00')).quantize(CENTAVO)
    if configuracion.monto_minimo is not None and valor_total < configuracion.monto_minimo:
        raise ValidationError(f'El valor minimo para reportar es ${configuracion.monto_minimo:,.2f}.')

    hash_comprobante = calcular_hash_archivo(comprobante)
    referencia_normalizada = (referencia_reportada or '').strip()
    fingerprint = _fingerprint_reporte(
        empresa=empresa,
        usuario=usuario,
        fecha_pago_reportada=fecha_pago_reportada,
        hash_comprobante=hash_comprobante,
        obligaciones=detalles,
        referencia_reportada=referencia_normalizada,
    )
    fingerprint_legacy = _fingerprint_reporte_legacy(
        empresa=empresa,
        usuario=usuario,
        fecha_pago_reportada=fecha_pago_reportada,
        hash_comprobante=hash_comprobante,
        obligaciones=detalles,
    )
    existente = PagoBREB.objects.filter(
        Q(fingerprint_reporte=fingerprint)
        | Q(
            fingerprint_reporte=fingerprint_legacy,
            referencia_reportada=referencia_normalizada,
        )
    ).first()
    if existente:
        return existente

    pago = PagoBREB(
        credito=None,
        usuario=usuario,
        empresa=empresa,
        configuracion=configuracion,
        valor_reportado=valor_total,
        fecha_pago_reportada=fecha_pago_reportada,
        referencia_reportada=referencia_normalizada,
        notas=(notas or '').strip(),
        comprobante=comprobante,
        hash_comprobante=hash_comprobante,
        fingerprint_reporte=fingerprint,
    )
    pago.full_clean()
    pago.save()
    PagoBREBDetalle.objects.bulk_create([
        PagoBREBDetalle(
            pago_breb=pago,
            credito=item['credito'],
            cuota=item['cuota'],
            numero_cuota_snapshot=item['cuota'].numero_cuota,
            fecha_vencimiento_snapshot=item['cuota'].fecha_vencimiento,
            valor_cuota_snapshot=item['cuota'].valor_cuota,
            valor_reportado=item['valor_reportado'],
        )
        for item in detalles
    ])
    return pago


def _monto_aprobado_detalle(detalle, valores_aprobados):
    if not valores_aprobados:
        return detalle.valor_reportado
    valor = valores_aprobados.get(detalle.pk, valores_aprobados.get(str(detalle.pk)))
    if valor in (None, ''):
        raise ValidationError('Debes indicar el valor aprobado de cada obligacion.')
    return _decimal_positivo(valor, mensaje='El valor aprobado debe ser mayor a cero.')


@transaction.atomic
def aprobar_pago_breb(*, pago_breb, usuario, valores_aprobados=None, valor_aprobado=None):
    pago = PagoBREB.objects.select_for_update(of=('self',)).get(pk=pago_breb.pk)
    _exigir_permiso_revision(usuario=usuario, pago_breb=pago)

    detalles = list(
        PagoBREBDetalle.objects.select_for_update()
        .filter(pago_breb=pago)
        .order_by('credito_id', 'numero_cuota_snapshot', 'pk')
    )
    if pago.estado == PagoBREB.Estado.APROBADO:
        completo = bool(detalles) and all(detalle.historial_pago_id for detalle in detalles)
        if completo or (not detalles and pago.historial_pago_id):
            return pago, False
        raise ValidationError('El reporte figura aprobado pero su aplicacion financiera esta incompleta.')
    if pago.estado != PagoBREB.Estado.PENDIENTE_VERIFICACION:
        raise ValidationError('Este reporte ya fue revisado y no admite una nueva aprobacion.')
    if not detalles:
        raise ValidationError('El reporte BRE-B no contiene obligaciones para aplicar.')

    credito_ids = sorted({detalle.credito_id for detalle in detalles})
    creditos = {
        credito.pk: credito
        for credito in Credito.objects.select_for_update().filter(pk__in=credito_ids).order_by('pk')
    }
    cuotas_ids = sorted({detalle.cuota_id for detalle in detalles if detalle.cuota_id})
    cuotas = {
        cuota.pk: cuota
        for cuota in CuotaAmortizacion.objects.select_for_update().filter(pk__in=cuotas_ids).order_by('pk')
    }

    if valor_aprobado is not None and len(detalles) == 1 and not valores_aprobados:
        valores_aprobados = {detalles[0].pk: valor_aprobado}

    ahora = timezone.now()
    total_aprobado = Decimal('0.00')
    for detalle in detalles:
        credito = creditos.get(detalle.credito_id)
        cuota = cuotas.get(detalle.cuota_id)
        if credito:
            credito.refresh_from_db()
        if not credito or credito.empresa_relacionada != pago.empresa:
            raise ValidationError('Una obligacion del reporte ya no pertenece a la empresa informada.')
        if credito.estado not in ESTADOS_CREDITO_REPORTABLES:
            raise ValidationError(f'El credito {credito.numero_credito} ya no admite la aplicacion del pago.')
        if cuota is None and detalle.cuota_id is None:
            cuota = (
                CuotaAmortizacion.objects.select_for_update()
                .filter(credito=credito, pagada=False)
                .order_by('numero_cuota', 'pk')
                .first()
            )
        if not cuota or cuota.credito_id != credito.pk or cuota.pagada:
            raise ValidationError(f'La cuota reportada del credito {credito.numero_credito} ya no esta pendiente.')

        primera_cuota_pendiente = (
            CuotaAmortizacion.objects.select_for_update()
            .filter(credito=credito, pagada=False)
            .order_by('numero_cuota', 'pk')
            .first()
        )
        if not primera_cuota_pendiente or primera_cuota_pendiente.pk != cuota.pk:
            raise ValidationError(
                f'La cuota reportada del credito {credito.numero_credito} ya no es la obligacion exigible.'
            )

        pendiente = min(
            max(Decimal('0.00'), (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00'))),
            max(Decimal('0.00'), credito.saldo_pendiente or Decimal('0.00')),
        ).quantize(CENTAVO)
        monto = _monto_aprobado_detalle(detalle, valores_aprobados)
        if monto > pendiente:
            raise ValidationError(
                f'El valor aprobado para {credito.numero_credito} supera su obligacion pendiente.'
            )

        referencia = f'BREB-{pago.clave_idempotencia.hex}-{detalle.pk}'
        historial, _ = credit_services.registrar_pago_credito(
            credito=credito,
            monto=monto,
            referencia_pago=referencia,
            metodo_pago=HistorialPago.MetodoPago.BREB,
            origen_registro=HistorialPago.OrigenRegistro.REPORTE_BREB,
            estado=HistorialPago.EstadoPago.EXITOSO,
            usuario=usuario,
            empresa=pago.empresa,
            fecha_aplicacion=ahora,
            notas=(
                f'Pago BRE-B agrupado verificado. Reporte #{pago.pk}, detalle #{detalle.pk}. '
                f'Valor reportado: ${detalle.valor_reportado:,.2f}.'
            ),
        )
        if historial.credito_id != credito.pk or historial.monto != monto:
            raise ValidationError('La referencia idempotente BRE-B corresponde a otro movimiento.')
        detalle.valor_aprobado = monto
        detalle.historial_pago = historial
        detalle.aplicado_en = ahora
        campos_detalle = ['valor_aprobado', 'historial_pago', 'aplicado_en']
        if detalle.cuota_id is None:
            detalle.cuota = cuota
            detalle.numero_cuota_snapshot = cuota.numero_cuota
            detalle.fecha_vencimiento_snapshot = cuota.fecha_vencimiento
            detalle.valor_cuota_snapshot = cuota.valor_cuota
            campos_detalle.extend([
                'cuota', 'numero_cuota_snapshot', 'fecha_vencimiento_snapshot',
                'valor_cuota_snapshot',
            ])
        detalle.save(update_fields=campos_detalle)
        total_aprobado += monto

    pago.valor_aprobado = total_aprobado.quantize(CENTAVO)
    pago.estado = PagoBREB.Estado.APROBADO
    pago.revisado_por = usuario
    pago.revisado_en = ahora
    pago.motivo_rechazo = ''
    pago.save(update_fields=[
        'valor_aprobado',
        'estado',
        'revisado_por',
        'revisado_en',
        'motivo_rechazo',
    ])
    return pago, True


@transaction.atomic
def rechazar_pago_breb(*, pago_breb, usuario, motivo):
    pago = PagoBREB.objects.select_for_update(of=('self',)).get(pk=pago_breb.pk)
    _exigir_permiso_revision(usuario=usuario, pago_breb=pago)
    if pago.estado != PagoBREB.Estado.PENDIENTE_VERIFICACION:
        raise ValidationError('Este reporte ya fue revisado.')

    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError('Debes indicar el motivo del rechazo.')

    pago.estado = PagoBREB.Estado.RECHAZADO
    pago.revisado_por = usuario
    pago.revisado_en = timezone.now()
    pago.motivo_rechazo = motivo
    pago.save(update_fields=['estado', 'revisado_por', 'revisado_en', 'motivo_rechazo'])
    return pago

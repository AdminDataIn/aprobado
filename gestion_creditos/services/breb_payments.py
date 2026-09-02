from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import (
    ConfiguracionPagoBREB,
    Credito,
    HistorialPago,
    PagoBREB,
    calcular_hash_archivo,
)


ESTADOS_CREDITO_REPORTABLES = {
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
}


def obtener_configuracion_breb_activa():
    return ConfiguracionPagoBREB.objects.filter(activo=True).first()


def _exigir_propiedad_credito(*, credito, usuario):
    if not usuario or not usuario.is_authenticated or credito.usuario_id != usuario.id:
        raise PermissionDenied('No puedes reportar pagos para este crédito.')


def usuario_puede_revisar_pago_breb(*, usuario, pago_breb):
    if not usuario or not usuario.is_authenticated:
        return False
    perfil_pagador = getattr(usuario, 'perfil_pagador', None)
    if not usuario.has_perm('gestion_creditos.review_pagobreb'):
        return False
    if not usuario.is_staff and not perfil_pagador:
        return False
    if perfil_pagador and pago_breb.empresa_id != perfil_pagador.empresa_id:
        return False
    return True


def _exigir_permiso_revision(*, usuario, pago_breb):
    if (
        not usuario_puede_revisar_pago_breb(usuario=usuario, pago_breb=pago_breb)
    ):
        raise PermissionDenied('No tienes permiso para revisar pagos BRE-B.')
    if usuario.id == pago_breb.usuario_id:
        raise PermissionDenied('No puedes aprobar tu propio reporte de pago.')


def calcular_monto_sugerido(credito):
    cuota = credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
    if cuota:
        return max(
            Decimal('0.00'),
            (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00')),
        )
    return max(Decimal('0.00'), credito.saldo_pendiente or Decimal('0.00'))


@transaction.atomic
def reportar_pago_breb(
    *,
    credito,
    usuario,
    configuracion,
    valor_reportado,
    fecha_pago_reportada,
    comprobante,
    referencia_reportada='',
):
    credito = Credito.objects.select_for_update().get(pk=credito.pk)
    _exigir_propiedad_credito(credito=credito, usuario=usuario)
    if credito.estado not in ESTADOS_CREDITO_REPORTABLES:
        raise ValidationError('Este crédito no admite reportes de pago BRE-B en su estado actual.')

    configuracion = ConfiguracionPagoBREB.objects.select_for_update().get(pk=configuracion.pk)
    if not configuracion.activo:
        raise ValidationError('El pago por BRE-B no está disponible en este momento.')

    hash_comprobante = calcular_hash_archivo(comprobante)
    existente = PagoBREB.objects.filter(
        credito=credito,
        usuario=usuario,
        valor_reportado=valor_reportado,
        fecha_pago_reportada=fecha_pago_reportada,
        hash_comprobante=hash_comprobante,
        estado__in=(
            PagoBREB.Estado.PENDIENTE_VERIFICACION,
            PagoBREB.Estado.APROBADO,
        ),
    ).first()
    if existente:
        return existente

    pago = PagoBREB(
        credito=credito,
        usuario=usuario,
        empresa=credito.empresa_relacionada,
        configuracion=configuracion,
        valor_reportado=valor_reportado,
        fecha_pago_reportada=fecha_pago_reportada,
        referencia_reportada=(referencia_reportada or '').strip(),
        comprobante=comprobante,
        hash_comprobante=hash_comprobante,
    )
    pago.full_clean()
    pago.save()
    return pago


@transaction.atomic
def aprobar_pago_breb(*, pago_breb, usuario, valor_aprobado):
    pago = PagoBREB.objects.select_for_update(of=('self',)).select_related(
        'credito', 'empresa', 'usuario'
    ).get(pk=pago_breb.pk)
    _exigir_permiso_revision(usuario=usuario, pago_breb=pago)

    if pago.estado == PagoBREB.Estado.APROBADO and pago.historial_pago_id:
        return pago, False
    if pago.estado != PagoBREB.Estado.PENDIENTE_VERIFICACION:
        raise ValidationError('Este reporte ya fue revisado y no admite una nueva aprobación.')

    monto = Decimal(valor_aprobado).quantize(Decimal('0.01'))
    if monto <= Decimal('0.00'):
        raise ValidationError('El valor aprobado debe ser mayor a cero.')

    referencia = f'BREB-{pago.clave_idempotencia.hex}'
    historial, _ = credit_services.registrar_pago_credito(
        credito=pago.credito,
        monto=monto,
        referencia_pago=referencia,
        metodo_pago=HistorialPago.MetodoPago.BREB,
        origen_registro=HistorialPago.OrigenRegistro.REPORTE_BREB,
        estado=HistorialPago.EstadoPago.EXITOSO,
        usuario=usuario,
        empresa=pago.empresa,
        fecha_aplicacion=timezone.now(),
        notas=(
            f'Pago BRE-B verificado. Reporte #{pago.pk}. '
            f'Valor reportado: ${pago.valor_reportado:,.2f}.'
        ),
    )
    pago.valor_aprobado = monto
    pago.historial_pago = historial
    pago.estado = PagoBREB.Estado.APROBADO
    pago.revisado_por = usuario
    pago.revisado_en = timezone.now()
    pago.motivo_rechazo = ''
    pago.save(update_fields=[
        'valor_aprobado',
        'historial_pago',
        'estado',
        'revisado_por',
        'revisado_en',
        'motivo_rechazo',
    ])
    return pago, True


@transaction.atomic
def rechazar_pago_breb(*, pago_breb, usuario, motivo):
    pago = PagoBREB.objects.select_for_update(of=('self',)).select_related(
        'empresa', 'usuario'
    ).get(pk=pago_breb.pk)
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

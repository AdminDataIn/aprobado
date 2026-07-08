import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from gestion_creditos.models import Credito, HistorialPago


def build_obligaciones_pendientes_empresa(empresa, limit=30):
    creditos = (
        Credito.objects.filter(
            Q(linea=Credito.LineaCredito.LIBRANZA, detalle_libranza__empresa=empresa)
            | Q(linea=Credito.LineaCredito.ADELANTO_NOMINA, detalle_adelanto_nomina__vinculo_laboral__empresa=empresa),
            estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA],
        )
        .select_related('detalle_libranza', 'detalle_adelanto_nomina__vinculo_laboral__empresa', 'usuario')
        .order_by('fecha_proximo_pago', 'numero_credito')
    )

    obligaciones = []
    for credito in creditos:
        cuota = credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
        if not cuota:
            continue
        monto_sugerido = (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00'))
        if monto_sugerido <= Decimal('0.00'):
            continue
        obligaciones.append({
            'credito_id': credito.id,
            'credito': credito,
            'numero_credito': credito.numero_credito,
            'cliente_nombre': credito.nombre_cliente,
            'cliente_documento': credito.cliente_documento,
            'fecha_vencimiento': cuota.fecha_vencimiento,
            'numero_cuota': cuota.numero_cuota,
            'monto_sugerido': monto_sugerido.quantize(Decimal('0.01')),
            'saldo_pendiente': credito.saldo_pendiente or Decimal('0.00'),
        })
        if len(obligaciones) >= limit:
            break
    return obligaciones


def aplicar_pago_obligaciones_seleccionadas(
    *,
    empresa,
    actor,
    obligaciones,
    metodo_pago,
    nota,
    comprobante=None,
):
    from gestion_creditos import credit_services

    fecha_aplicacion = timezone.now()
    firma_pago = _construir_firma_pago_obligaciones(
        empresa=empresa,
        obligaciones=obligaciones,
        fecha=fecha_aplicacion,
    )
    if _existe_pago_obligaciones_duplicado(empresa=empresa, firma_pago=firma_pago, fecha=fecha_aplicacion):
        raise ValidationError(
            'Ya existe un pago registrado hoy para las mismas obligaciones y montos. '
            'Revisa el historial antes de intentarlo nuevamente.'
        )

    pagos_aplicados = []
    proof_bytes = None
    proof_name = None
    if comprobante:
        proof_bytes = comprobante.read()
        proof_name = getattr(comprobante, 'name', 'comprobante')

    for item in obligaciones:
        credito = item['credito']
        referencia = (
            f"PAGADOR-{empresa.id}-{fecha_aplicacion:%Y%m%d}-{firma_pago}-{credito.id}-{fecha_aplicacion:%H%M%S%f}"
        )
        proof_copy = None
        if proof_bytes is not None:
            proof_copy = ContentFile(proof_bytes, name=proof_name)

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=item['monto'],
            referencia_pago=referencia,
            metodo_pago=metodo_pago,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
            usuario=actor,
            empresa=empresa,
            comprobante=proof_copy,
            notas=nota,
            fecha_aplicacion=fecha_aplicacion,
        )
        if created:
            pagos_aplicados.append(pago)

    return pagos_aplicados


def _construir_firma_pago_obligaciones(*, empresa, obligaciones, fecha):
    partes = []
    for item in obligaciones:
        credito = item['credito']
        monto = Decimal(item['monto']).quantize(Decimal('0.01'))
        partes.append(f'{credito.id}:{monto}')
    base = f'{empresa.id}|{fecha:%Y-%m-%d}|{";".join(sorted(partes))}'
    return hashlib.sha256(base.encode('utf-8')).hexdigest()[:16]


def _existe_pago_obligaciones_duplicado(*, empresa, firma_pago, fecha):
    inicio_dia = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    fin_dia = inicio_dia + timedelta(days=1)
    return HistorialPago.objects.filter(
        empresa_origen=empresa,
        origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
        estado=HistorialPago.EstadoPago.EXITOSO,
        fecha_aplicacion__gte=inicio_dia,
        fecha_aplicacion__lt=fin_dia,
        referencia_pago__contains=firma_pago,
    ).exists()

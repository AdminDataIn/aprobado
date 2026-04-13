import uuid
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import Credito, CuotaAmortizacion, HistorialEstado


@transaction.atomic
def saldar_credito_formalmente(credito, *, actor=None, motivo='', fecha_operacion=None):
    """
    Cierra formalmente un crédito dejándolo en estado PAGADO sin residuos.
    """
    if credito.estado == Credito.EstadoCredito.PAGADO:
        return credito

    fecha_operacion = fecha_operacion or timezone.now()
    if isinstance(fecha_operacion, datetime):
        fecha_pago = fecha_operacion
    else:
        fecha_pago = datetime.combine(fecha_operacion, datetime.min.time(), tzinfo=timezone.get_current_timezone())

    credito = (
        Credito.objects.select_for_update()
        .prefetch_related('tabla_amortizacion')
        .get(pk=credito.pk)
    )
    estado_anterior = credito.estado
    cuotas_pendientes = list(credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota'))

    for cuota in cuotas_pendientes:
        cuota.pagada = True
        cuota.fecha_pago = fecha_pago
        cuota.monto_pagado = cuota.valor_cuota or Decimal('0.00')

    if cuotas_pendientes:
        CuotaAmortizacion.objects.bulk_update(
            cuotas_pendientes,
            ['pagada', 'fecha_pago', 'monto_pagado'],
        )

    credito.saldo_pendiente = Decimal('0.00')
    credito.capital_pendiente = Decimal('0.00')
    credito.fecha_proximo_pago = None
    credito.estado = Credito.EstadoCredito.PAGADO
    credito.save(update_fields=['saldo_pendiente', 'capital_pendiente', 'fecha_proximo_pago', 'estado'])

    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=estado_anterior,
        estado_nuevo=Credito.EstadoCredito.PAGADO,
        usuario_modificacion=actor,
        motivo=(motivo or 'Crédito saldado por cierre administrativo controlado.').strip(),
    )
    return credito


def build_saldo_formal_reference(credito):
    return f"SALDO-{credito.numero_credito}-{uuid.uuid4().hex[:8].upper()}"

from decimal import Decimal

from django.db.models import Sum

from gestion_creditos.models import Credito, HistorialPago


def obtener_resumen_pagos_credito(credito, historial_pagos=None):
    """
    Construye un resumen consistente del estado de pagos de un credito.

    La fuente de verdad preferida es la tabla de amortizacion. Esto permite
    reflejar correctamente creditos especiales legacy cuyos pagos historicos se
    cargaron marcando cuotas como pagadas, sin crear registros en HistorialPago.
    """
    cuotas = list(credito.tabla_amortizacion.all().order_by('numero_cuota'))
    plazo_total = credito.plazo or len(cuotas) or 0

    if cuotas:
        cuotas_pagadas = sum(1 for cuota in cuotas if cuota.pagada)
        cuotas_restantes = max(len(cuotas) - cuotas_pagadas, 0)

        total_pagado = Decimal('0.00')
        saldo_pendiente = Decimal('0.00')
        for cuota in cuotas:
            ya_pagado = cuota.monto_pagado or Decimal('0.00')
            if cuota.pagada and cuota.monto_pagado is None:
                ya_pagado = cuota.valor_cuota or Decimal('0.00')

            total_pagado += ya_pagado

            restante_cuota = (cuota.valor_cuota or Decimal('0.00')) - ya_pagado
            if restante_cuota > 0:
                saldo_pendiente += restante_cuota

        capital_pendiente = sum(
            (cuota.capital_a_pagar or Decimal('0.00'))
            for cuota in cuotas
            if not cuota.pagada
        )

        proxima_cuota = next((cuota for cuota in cuotas if not cuota.pagada), None)

        return {
            'cuotas_pagadas': cuotas_pagadas,
            'cuotas_restantes': cuotas_restantes,
            'total_pagado': total_pagado,
            'saldo_pendiente': saldo_pendiente,
            'capital_pendiente': capital_pendiente,
            'fecha_proximo_pago': proxima_cuota.fecha_vencimiento if proxima_cuota else None,
            'plazo_total': plazo_total,
            'fuente': 'tabla_amortizacion',
        }

    if historial_pagos is None:
        historial_pagos = HistorialPago.objects.filter(
            credito=credito,
            estado=HistorialPago.EstadoPago.EXITOSO,
        )

    total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    cuotas_pagadas = historial_pagos.count()
    cuotas_restantes = max(plazo_total - cuotas_pagadas, 0)

    return {
        'cuotas_pagadas': cuotas_pagadas,
        'cuotas_restantes': cuotas_restantes,
        'total_pagado': total_pagado,
        'saldo_pendiente': credito.saldo_pendiente or Decimal('0.00'),
        'capital_pendiente': credito.capital_pendiente or Decimal('0.00'),
        'fecha_proximo_pago': credito.fecha_proximo_pago,
        'plazo_total': plazo_total,
        'fuente': 'historial_pagos',
    }


def recalcular_credito_desde_tabla_amortizacion(credito, persist=False):
    """
    Recalcula saldos y proxima fecha de pago desde la tabla de amortizacion.

    Util para creditos legacy o especiales donde la tabla ya representa la
    historia real y se necesita alinear los campos persistidos del credito.
    """
    resumen = obtener_resumen_pagos_credito(credito)

    if persist:
        credito.saldo_pendiente = resumen['saldo_pendiente']
        credito.capital_pendiente = resumen['capital_pendiente']
        credito.fecha_proximo_pago = resumen['fecha_proximo_pago']
        credito.estado = (
            Credito.EstadoCredito.PAGADO
            if resumen['cuotas_restantes'] == 0
            else Credito.EstadoCredito.ACTIVO
        )
        credito.save(update_fields=['saldo_pendiente', 'capital_pendiente', 'fecha_proximo_pago', 'estado'])

    return resumen

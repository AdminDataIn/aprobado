from decimal import Decimal, ROUND_HALF_UP


def calcular_interes_prorrateado(valor_interes_mensual, dias_cobrados, dias_base=30):
    """
    Calcula interés proporcional por días sobre el interés mensual ya previsto.
    """
    dias_base = max(int(dias_base or 30), 1)
    dias_cobrados = max(min(int(dias_cobrados or 0), dias_base), 0)
    valor_interes_mensual = Decimal(str(valor_interes_mensual or '0'))
    return (valor_interes_mensual * Decimal(dias_cobrados) / Decimal(dias_base)).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )


def simular_interes_prorrateado_cuota(*, interes_mensual, dia_pago, dia_corte=30):
    """
    Base técnica para pagos anticipados: cobra interés proporcional hasta el día del pago.
    """
    dia_pago = max(int(dia_pago or 0), 0)
    dia_corte = max(int(dia_corte or 30), 1)
    dias_cobrados = min(dia_pago, dia_corte)
    return {
        'dias_cobrados': dias_cobrados,
        'dias_base': dia_corte,
        'interes_prorrateado': calcular_interes_prorrateado(interes_mensual, dias_cobrados, dia_corte),
    }

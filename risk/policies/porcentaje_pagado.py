from decimal import Decimal, ROUND_HALF_UP


DOS_DECIMALES = Decimal('0.01')


def a_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def a_dinero(value) -> Decimal | None:
    value = a_decimal(value)
    if value is None:
        return None
    return value.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def calcular_porcentaje_pagado(credito) -> Decimal | None:
    monto_aprobado = a_decimal(getattr(credito, 'monto_aprobado', None))
    capital_pendiente = a_decimal(getattr(credito, 'capital_pendiente', None))
    if monto_aprobado is None or monto_aprobado <= 0 or capital_pendiente is None:
        return None

    monto_pagado = max(Decimal('0.00'), monto_aprobado - capital_pendiente)
    porcentaje = (monto_pagado / monto_aprobado) * Decimal('100')
    return porcentaje.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def calcular_saldo_pendiente(credito) -> Decimal:
    saldo = a_dinero(getattr(credito, 'saldo_pendiente', None))
    if saldo is not None:
        return max(Decimal('0.00'), saldo)

    capital = a_dinero(getattr(credito, 'capital_pendiente', None))
    return max(Decimal('0.00'), capital or Decimal('0.00'))

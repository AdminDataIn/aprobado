from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


TWOPLACES = Decimal('0.01')

REASON_CAPACITY_AVAILABLE = 'capacidad_disponible'
REASON_INSUFFICIENT_CAPACITY = 'capacidad_insuficiente'
REASON_PROJECTED_INSTALLMENT_EXCEEDS_LIMIT = 'cuota_proyectada_supera_limite'
REASON_INSUFFICIENT_INCOME_DATA = 'datos_ingreso_insuficientes'


def to_decimal(value, fallback='0') -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(fallback))


def round_money(value) -> Decimal:
    return to_decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def obtener_porcentaje_capacidad_libranza() -> Decimal:
    value = getattr(
        settings,
        'LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE',
        getattr(settings, 'ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE', '25'),
    )
    return to_decimal(value, '25')


def calcular_capacidad_maxima(ingreso_base, porcentaje=None) -> Decimal:
    ingreso_base = round_money(ingreso_base)
    porcentaje = to_decimal(porcentaje if porcentaje is not None else obtener_porcentaje_capacidad_libranza(), '25')
    return round_money((ingreso_base * porcentaje) / Decimal('100'))


def calcular_porcentaje_comprometido(*, ingreso_base, descuentos_actuales, cuota_actual_libranza, cuota_proyectada):
    ingreso_base = round_money(ingreso_base)
    if ingreso_base <= 0:
        return None

    comprometido = (
        round_money(descuentos_actuales)
        + round_money(cuota_actual_libranza)
        + round_money(cuota_proyectada)
    )
    return ((comprometido / ingreso_base) * Decimal('100')).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

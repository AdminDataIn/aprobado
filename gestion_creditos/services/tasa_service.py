from decimal import Decimal, InvalidOperation

from django.conf import settings

from gestion_creditos.models import Credito


def _to_decimal(value, fallback):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(fallback))


def obtener_tasa_credito(linea_credito):
    """
    Retorna la tasa mensual parametrizada para la linea de credito.
    """
    if linea_credito == Credito.LineaCredito.LIBRANZA:
        return _to_decimal(getattr(settings, 'LIBRANZA_TASA_MENSUAL', '1.9'), '1.9')

    if linea_credito == Credito.LineaCredito.EMPRENDIMIENTO:
        return _to_decimal(getattr(settings, 'EMPRENDIMIENTO_TASA_MENSUAL', '3.5'), '3.5')

    return Decimal('0.00')


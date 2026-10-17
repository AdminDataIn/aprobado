"""Versioned income semantics, scoped to the Prestadores risk policy."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


def politica_ingreso_default():
    return {
        'version': 'ingreso-evaluable-v1',
        'descuento_contractual_porcentaje': '25.00',
        'semantica_midecisor': 'NO_CONFIRMADA',
    }


def validar_politica_ingreso(datos):
    base = politica_ingreso_default()
    if not isinstance(datos, dict) or set(datos) != set(base):
        raise ValidationError('Configuracion de ingreso evaluable incompleta.')
    try:
        descuento = Decimal(str(datos['descuento_contractual_porcentaje']))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('Descuento contractual invalido.') from exc
    if (datos['version'] != base['version'] or not descuento.is_finite()
            or not 0 <= descuento < 100
            or descuento != descuento.quantize(Decimal('.01'))
            or not isinstance(datos['semantica_midecisor'], str)
            or datos['semantica_midecisor'] not in {'NO_CONFIRMADA', 'NETO_MENSUAL_ESTIMADO'}):
        raise ValidationError('La politica de ingreso evaluable no es interpretable.')
    return {**datos, 'descuento_contractual_porcentaje': format(descuento, '.2f')}

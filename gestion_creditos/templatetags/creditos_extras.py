"""
Template tags y filtros personalizados para la app gestion_creditos
"""
from django import template
from decimal import Decimal

register = template.Library()


@register.filter(name='sum_attr')
def sum_attr(queryset, attr_name):
    """
    Suma un atributo específico de todos los objetos en un queryset o lista.

    Uso en template:
        {{ tabla_amortizacion|sum_attr:"capital_a_pagar" }}

    Args:
        queryset: QuerySet o lista de objetos
        attr_name: Nombre del atributo a sumar

    Returns:
        Decimal: Suma total del atributo
    """
    total = Decimal('0.00')
    for obj in queryset:
        value = getattr(obj, attr_name, 0)
        if value:
            total += Decimal(str(value))
    return total

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


@register.filter(name='sum_monto_abonado')
def sum_monto_abonado(reestructuraciones):
    """
    Suma el monto_abonado de todas las reestructuraciones.

    Uso en template:
        {{ reestructuraciones|sum_monto_abonado }}

    Args:
        reestructuraciones: QuerySet o lista de ReestructuracionCredito

    Returns:
        Decimal: Suma total de montos abonados
    """
    return sum_attr(reestructuraciones, 'monto_abonado')


@register.filter(name='sum_ahorro_intereses')
def sum_ahorro_intereses(reestructuraciones):
    """
    Suma el ahorro_intereses de todas las reestructuraciones.

    Uso en template:
        {{ reestructuraciones|sum_ahorro_intereses }}

    Args:
        reestructuraciones: QuerySet o lista de ReestructuracionCredito

    Returns:
        Decimal: Suma total de ahorros en intereses
    """
    return sum_attr(reestructuraciones, 'ahorro_intereses')

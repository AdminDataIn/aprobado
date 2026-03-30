from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.conf import settings


TWOPLACES = Decimal('0.01')


def _to_decimal(value, fallback='0'):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(fallback))


def _round_money(value):
    return _to_decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _round_down_money(value):
    return _to_decimal(value).quantize(TWOPLACES, rounding=ROUND_DOWN)


def obtener_porcentaje_capacidad_descuento():
    return _to_decimal(getattr(settings, 'ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE', '25'), '25')


def calcular_capacidad_descuento(
    salario=Decimal('0.00'),
    auxilio_transporte=Decimal('0.00'),
    descuentos=Decimal('0.00'),
    monto_solicitado=None,
):
    salario = _to_decimal(salario)
    auxilio_transporte = _to_decimal(auxilio_transporte)
    descuentos = _to_decimal(descuentos)
    monto_solicitado = _to_decimal(monto_solicitado or '0')

    ingreso_base = _round_money(salario + auxilio_transporte)
    ingreso_neto = _round_money(max(Decimal('0.00'), ingreso_base - descuentos))
    porcentaje = obtener_porcentaje_capacidad_descuento()
    capacidad_disponible = _round_money((ingreso_neto * porcentaje) / Decimal('100'))

    decision_preliminar = 'SIN_DATOS'
    if ingreso_neto > 0:
        decision_preliminar = 'APLICA' if monto_solicitado <= capacidad_disponible else 'NO_APLICA'

    return {
        'ingreso_base': ingreso_base,
        'descuentos_considerados': descuentos,
        'ingreso_neto': ingreso_neto,
        'capacidad_disponible': capacidad_disponible,
        'porcentaje_aplicado': porcentaje,
        'monto_solicitado': monto_solicitado,
        'decision_preliminar': decision_preliminar,
    }


def simular_adelanto_nomina(
    salario=Decimal('0.00'),
    auxilio_transporte=Decimal('0.00'),
    descuentos=Decimal('0.00'),
    dias_adelanto=5,
    tasa_mensual=Decimal('1.9'),
    porcentaje_comision=Decimal('10'),
):
    capacidad = calcular_capacidad_descuento(
        salario=salario,
        auxilio_transporte=auxilio_transporte,
        descuentos=descuentos,
    )
    ingreso_neto = capacidad['ingreso_neto']
    valor_diario = _round_down_money((ingreso_neto / Decimal('30')) if ingreso_neto else Decimal('0.00'))
    adelanto_teorico = _round_money(valor_diario * Decimal(str(dias_adelanto)))
    monto_bruto = min(adelanto_teorico, capacidad['capacidad_disponible']) if adelanto_teorico else Decimal('0.00')

    porcentaje_comision = _to_decimal(porcentaje_comision, '10')
    tasa_mensual = _to_decimal(tasa_mensual, '1.9')
    comision = _round_money((monto_bruto * porcentaje_comision) / Decimal('100'))
    iva_comision = _round_money(comision * Decimal('0.19'))
    interes = _round_money((monto_bruto * tasa_mensual) / Decimal('100'))
    neto_a_recibir = _round_money(max(Decimal('0.00'), monto_bruto - comision - iva_comision))
    descuento_nomina_estimado = _round_money(monto_bruto + comision + iva_comision + interes)

    return {
        **capacidad,
        'dias_adelanto': int(dias_adelanto),
        'valor_diario_estimado': valor_diario,
        'monto_bruto_adelanto': monto_bruto,
        'porcentaje_comision': porcentaje_comision,
        'comision': comision,
        'iva_comision': iva_comision,
        'tasa_mensual': tasa_mensual,
        'interes': interes,
        'neto_a_recibir': neto_a_recibir,
        'descuento_nomina_estimado': descuento_nomina_estimado,
        'puede_solicitar': monto_bruto > 0 and capacidad['decision_preliminar'] != 'NO_APLICA',
    }

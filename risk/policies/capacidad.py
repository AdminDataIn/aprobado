from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from risk.policies.porcentaje_pagado import a_decimal, a_dinero


MOTIVO_CAPACIDAD_INSUFICIENTE = 'capacidad_insuficiente'
MOTIVO_DATOS_CAPACIDAD_INSUFICIENTES = 'datos_capacidad_insuficientes'


@dataclass(frozen=True)
class ResultadoCapacidad:
    aprobado: bool
    motivo: str | None = None
    ingreso_mensual: Decimal | None = None
    cuota_actual: Decimal = Decimal('0.00')
    cuota_proyectada: Decimal = Decimal('0.00')
    capacidad_maxima: Decimal | None = None
    porcentaje_comprometido: Decimal | None = None
    capacidad_residual: Decimal | None = None


def evaluar_capacidad(
    *,
    ingreso_mensual,
    cuota_actual=Decimal('0.00'),
    cuota_proyectada=Decimal('0.00'),
    porcentaje_maximo=Decimal('50'),
    requerir_datos=False,
) -> ResultadoCapacidad:
    ingreso_mensual = a_dinero(ingreso_mensual)
    cuota_actual = a_dinero(cuota_actual) or Decimal('0.00')
    cuota_proyectada = a_dinero(cuota_proyectada) or Decimal('0.00')
    porcentaje_maximo = a_decimal(porcentaje_maximo) or Decimal('50')

    if ingreso_mensual is None or ingreso_mensual <= 0:
        return ResultadoCapacidad(
            aprobado=not requerir_datos,
            motivo=MOTIVO_DATOS_CAPACIDAD_INSUFICIENTES if requerir_datos else None,
            cuota_actual=cuota_actual,
            cuota_proyectada=cuota_proyectada,
        )

    capacidad_maxima = ((ingreso_mensual * porcentaje_maximo) / Decimal('100')).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    total_cuotas = cuota_actual + cuota_proyectada
    porcentaje_comprometido = ((total_cuotas / ingreso_mensual) * Decimal('100')).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    capacidad_residual = (capacidad_maxima - total_cuotas).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )

    if total_cuotas > capacidad_maxima:
        return ResultadoCapacidad(
            aprobado=False,
            motivo=MOTIVO_CAPACIDAD_INSUFICIENTE,
            ingreso_mensual=ingreso_mensual,
            cuota_actual=cuota_actual,
            cuota_proyectada=cuota_proyectada,
            capacidad_maxima=capacidad_maxima,
            porcentaje_comprometido=porcentaje_comprometido,
            capacidad_residual=capacidad_residual,
        )

    return ResultadoCapacidad(
        aprobado=True,
        ingreso_mensual=ingreso_mensual,
        cuota_actual=cuota_actual,
        cuota_proyectada=cuota_proyectada,
        capacidad_maxima=capacidad_maxima,
        porcentaje_comprometido=porcentaje_comprometido,
        capacidad_residual=capacidad_residual,
    )

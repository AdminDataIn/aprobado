"""Payroll deduction capacity service.

Owns deterministic payroll-capacity calculations for libranza and adelanto de
nomina. This module does not read models, write database rows, or call external
providers.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from django.conf import settings

from libranza.policies.capacity import (
    REASON_CAPACITY_AVAILABLE,
    REASON_INSUFFICIENT_CAPACITY,
    REASON_INSUFFICIENT_INCOME_DATA,
    REASON_PROJECTED_INSTALLMENT_EXCEEDS_LIMIT,
    calcular_capacidad_maxima,
    calcular_porcentaje_comprometido,
    obtener_porcentaje_capacidad_libranza,
)
from libranza.selectors import obtener_credito_libranza_vigente


TWOPLACES = Decimal('0.01')

__all__ = [
    'TWOPLACES',
    'PayrollCapacityInput',
    'PayrollCapacityResult',
    'PayrollPaymentCapacityService',
    'LibranzaCapacityInput',
    'LibranzaCapacityResult',
    'LibranzaPaymentCapacityService',
    'calcular_capacidad_descuento',
    'evaluar_capacidad_descuento_libranza',
    'obtener_porcentaje_capacidad_descuento',
    'simular_adelanto_nomina',
]


@dataclass(frozen=True)
class PayrollCapacityInput:
    base_salary: Decimal
    transport_allowance: Decimal = Decimal("0.00")
    fixed_discounts: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class PayrollCapacityResult:
    capacity: Decimal
    net_income: Decimal
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LibranzaCapacityInput:
    ingreso_base: Decimal
    descuentos_actuales: Decimal = Decimal('0.00')
    cuota_actual_libranza: Decimal = Decimal('0.00')
    cuota_proyectada: Decimal = Decimal('0.00')
    porcentaje_capacidad: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LibranzaCapacityResult:
    ingreso_base: Decimal
    descuentos_actuales: Decimal
    cuota_actual_libranza: Decimal
    cuota_proyectada: Decimal
    capacidad_maxima: Decimal
    capacidad_disponible: Decimal
    cuota_maxima_permitida: Decimal
    porcentaje_comprometido: Decimal | None
    eligible: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elegible(self) -> bool:
        return self.eligible

    @property
    def motivo(self) -> str:
        return self.reason

    def as_dict(self) -> dict:
        return {
            'ingreso_base': self.ingreso_base,
            'descuentos_actuales': self.descuentos_actuales,
            'cuota_actual_libranza': self.cuota_actual_libranza,
            'cuota_proyectada': self.cuota_proyectada,
            'capacidad_maxima': self.capacidad_maxima,
            'capacidad_disponible': self.capacidad_disponible,
            'cuota_maxima_permitida': self.cuota_maxima_permitida,
            'porcentaje_comprometido': self.porcentaje_comprometido,
            'eligible': self.eligible,
            'reason': self.reason,
            'metadata': self.metadata,
        }


class PayrollPaymentCapacityService:
    """Interface placeholder for payroll deduction capacity."""

    def calculate(self, data: PayrollCapacityInput) -> PayrollCapacityResult:
        result = calcular_capacidad_descuento(
            salario=data.base_salary,
            auxilio_transporte=data.transport_allowance,
            descuentos=data.fixed_discounts,
        )
        return PayrollCapacityResult(
            capacity=result['capacidad_disponible'],
            net_income=result['ingreso_neto'],
        )


class LibranzaPaymentCapacityService:
    """Read-only service for payroll-loan discount capacity."""

    def evaluate(self, data: LibranzaCapacityInput) -> LibranzaCapacityResult:
        return evaluar_capacidad_descuento_libranza(data)

    def evaluate_for_customer(
        self,
        *,
        cliente_id=None,
        document_number='',
        ingreso_base=Decimal('0.00'),
        descuentos_actuales=Decimal('0.00'),
        cuota_proyectada=Decimal('0.00'),
    ) -> LibranzaCapacityResult:
        credito = obtener_credito_libranza_vigente(
            cliente_id=cliente_id,
            document_number=document_number,
        )
        cuota_actual = Decimal('0.00')
        credito_id = None
        if credito:
            cuota_actual = getattr(credito, 'valor_cuota', None) or Decimal('0.00')
            credito_id = credito.id

        result = evaluar_capacidad_descuento_libranza(
            LibranzaCapacityInput(
                ingreso_base=ingreso_base,
                descuentos_actuales=descuentos_actuales,
                cuota_actual_libranza=cuota_actual,
                cuota_proyectada=cuota_proyectada,
                metadata={'current_credit_id': credito_id},
            )
        )
        return result


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


def evaluar_capacidad_descuento_libranza(data: LibranzaCapacityInput | dict) -> LibranzaCapacityResult:
    if isinstance(data, dict):
        data = LibranzaCapacityInput(**data)

    ingreso_base = _round_money(data.ingreso_base)
    descuentos_actuales = _round_money(data.descuentos_actuales)
    cuota_actual_libranza = _round_money(data.cuota_actual_libranza)
    cuota_proyectada = _round_money(data.cuota_proyectada)
    porcentaje = (
        _to_decimal(data.porcentaje_capacidad)
        if data.porcentaje_capacidad is not None
        else obtener_porcentaje_capacidad_libranza()
    )

    if ingreso_base <= Decimal('0.00'):
        return LibranzaCapacityResult(
            ingreso_base=ingreso_base,
            descuentos_actuales=descuentos_actuales,
            cuota_actual_libranza=cuota_actual_libranza,
            cuota_proyectada=cuota_proyectada,
            capacidad_maxima=Decimal('0.00'),
            capacidad_disponible=Decimal('0.00'),
            cuota_maxima_permitida=Decimal('0.00'),
            porcentaje_comprometido=None,
            eligible=False,
            reason=REASON_INSUFFICIENT_INCOME_DATA,
            metadata={**data.metadata, 'capacity_percentage': porcentaje},
        )

    capacidad_maxima = calcular_capacidad_maxima(ingreso_base, porcentaje)
    cuota_maxima_permitida = _round_money(
        max(Decimal('0.00'), capacidad_maxima - descuentos_actuales - cuota_actual_libranza)
    )
    capacidad_disponible = _round_money(cuota_maxima_permitida - cuota_proyectada)
    porcentaje_comprometido = calcular_porcentaje_comprometido(
        ingreso_base=ingreso_base,
        descuentos_actuales=descuentos_actuales,
        cuota_actual_libranza=cuota_actual_libranza,
        cuota_proyectada=cuota_proyectada,
    )

    reason = REASON_CAPACITY_AVAILABLE
    eligible = True
    if cuota_proyectada > capacidad_maxima:
        reason = REASON_PROJECTED_INSTALLMENT_EXCEEDS_LIMIT
        eligible = False
    elif capacidad_disponible < Decimal('0.00'):
        reason = REASON_INSUFFICIENT_CAPACITY
        eligible = False

    return LibranzaCapacityResult(
        ingreso_base=ingreso_base,
        descuentos_actuales=descuentos_actuales,
        cuota_actual_libranza=cuota_actual_libranza,
        cuota_proyectada=cuota_proyectada,
        capacidad_maxima=capacidad_maxima,
        capacidad_disponible=capacidad_disponible,
        cuota_maxima_permitida=cuota_maxima_permitida,
        porcentaje_comprometido=porcentaje_comprometido,
        eligible=eligible,
        reason=reason,
        metadata={**data.metadata, 'capacity_percentage': porcentaje},
    )


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

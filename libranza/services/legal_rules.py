"""Libranza legal-rules boundary.

Owns deterministic payroll-loan rules that can be evaluated without database
writes or provider side effects. ORM-heavy compatibility rules remain in
``gestion_creditos.services.libranza_rules`` until they can be extracted safely.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from libranza.services.payment_capacity import LibranzaCapacityInput, evaluar_capacidad_descuento_libranza


LIBRANZA_MONTO_MINIMO_SOLICITUD = 100000
LIBRANZA_MONTO_MINIMO_PUBLICO = 500000
LIBRANZA_MONTO_MAXIMO = 3000000
REASON_LEGAL_RULES_PASSED = 'reglas_libranza_cumplidas'
REASON_AMOUNT_BELOW_MINIMUM = 'monto_menor_al_minimo'
REASON_AMOUNT_ABOVE_MAXIMUM = 'monto_supera_maximo'

__all__ = [
    "LIBRANZA_MONTO_MINIMO_SOLICITUD",
    "LIBRANZA_MONTO_MINIMO_PUBLICO",
    "LIBRANZA_MONTO_MAXIMO",
    "REASON_AMOUNT_ABOVE_MAXIMUM",
    "REASON_AMOUNT_BELOW_MINIMUM",
    "REASON_LEGAL_RULES_PASSED",
    "LibranzaLegalInput",
    "LibranzaLegalDecision",
    "LibranzaLegalRulesService",
    "calcular_primera_fecha_pago_libranza",
    "evaluar_reglas_base_libranza",
    "obtener_fecha_primera_cuota_credito",
    "obtener_plazo_credito_aplicado",
    "obtener_tasa_credito_aplicada",
    "sumar_meses_con_dia_ancla",
]


@dataclass(frozen=True)
class LibranzaLegalInput:
    monto_solicitado: Decimal
    ingreso_base: Decimal
    descuentos_actuales: Decimal = Decimal('0.00')
    cuota_actual_libranza: Decimal = Decimal('0.00')
    cuota_proyectada: Decimal = Decimal('0.00')
    monto_minimo: Decimal = Decimal(str(LIBRANZA_MONTO_MINIMO_SOLICITUD))
    monto_maximo: Decimal = Decimal(str(LIBRANZA_MONTO_MAXIMO))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LibranzaLegalDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    capacity: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return self.allowed

    @property
    def reason(self) -> str:
        return self.reasons[0] if self.reasons else REASON_LEGAL_RULES_PASSED

    def as_dict(self) -> dict:
        return {
            'eligible': self.allowed,
            'reason': self.reason,
            'reasons': self.reasons,
            'capacity': self.capacity,
            'metadata': self.metadata,
        }


class LibranzaLegalRulesService:
    """Read-only service for base legal/product rules."""

    def evaluate(self, data: LibranzaLegalInput) -> LibranzaLegalDecision:
        return evaluar_reglas_base_libranza(data)


def evaluar_reglas_base_libranza(data: LibranzaLegalInput | dict) -> LibranzaLegalDecision:
    if isinstance(data, dict):
        data = LibranzaLegalInput(**data)

    monto_solicitado = Decimal(str(data.monto_solicitado))
    monto_minimo = Decimal(str(data.monto_minimo))
    monto_maximo = Decimal(str(data.monto_maximo))
    reasons = []

    if monto_solicitado < monto_minimo:
        reasons.append(REASON_AMOUNT_BELOW_MINIMUM)
    if monto_solicitado > monto_maximo:
        reasons.append(REASON_AMOUNT_ABOVE_MAXIMUM)

    capacity = evaluar_capacidad_descuento_libranza(
        LibranzaCapacityInput(
            ingreso_base=data.ingreso_base,
            descuentos_actuales=data.descuentos_actuales,
            cuota_actual_libranza=data.cuota_actual_libranza,
            cuota_proyectada=data.cuota_proyectada,
            metadata=data.metadata,
        )
    )
    if not capacity.eligible:
        reasons.append(capacity.reason)

    return LibranzaLegalDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
        capacity=capacity.as_dict(),
        metadata={
            **data.metadata,
            'minimum_amount': monto_minimo,
            'maximum_amount': monto_maximo,
        },
    )


def calcular_primera_fecha_pago_libranza(fecha_aprobacion=None, fecha_forzada=None):
    if fecha_forzada:
        return _to_date(fecha_forzada)

    fecha_base = _to_date(fecha_aprobacion) if fecha_aprobacion else timezone.localdate()
    if fecha_base.day <= 14:
        return (fecha_base + relativedelta(months=1)).replace(day=1)
    return (fecha_base + relativedelta(months=2)).replace(day=1)


def obtener_plazo_credito_aplicado(credito):
    return int(credito.plazo_forzado or credito.plazo or credito.plazo_solicitado or 0)


def obtener_tasa_credito_aplicada(credito, tasa_default):
    return credito.tasa_forzada if credito.tasa_forzada is not None else (credito.tasa_interes or tasa_default)


def obtener_fecha_primera_cuota_credito(credito, fecha_aprobacion=None):
    return calcular_primera_fecha_pago_libranza(
        fecha_aprobacion=fecha_aprobacion,
        fecha_forzada=credito.fecha_primera_cuota_forzada,
    )


def sumar_meses_con_dia_ancla(fecha_base, meses=1, dia_ancla=None):
    fecha_cursor = _to_date(fecha_base) + relativedelta(months=meses, day=1)
    dia_objetivo = dia_ancla or _to_date(fecha_base).day
    ultimo_dia = monthrange(fecha_cursor.year, fecha_cursor.month)[1]
    return fecha_cursor.replace(day=min(dia_objetivo, ultimo_dia))


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return timezone.localdate()

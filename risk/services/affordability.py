"""Limite de servicio para capacidad de pago general.

Este modulo sera dueno de calculos de capacidad de pago que no dependan de
reglas especificas de descuento por nomina.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class EntradaCapacidadPago:
    ingreso_mensual: Decimal
    obligaciones_mensuales: Decimal = Decimal("0.00")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoCapacidadPago:
    aprobado: bool
    capacidad_disponible: Decimal
    motivos: tuple[str, ...] = ()


class ServicioCapacidadPago:
    """Interfaz pendiente para evaluacion de capacidad de pago."""

    def evaluar(self, datos: EntradaCapacidadPago) -> ResultadoCapacidadPago:
        raise NotImplementedError("La capacidad de pago aun no esta implementada en risk.")


# Alias de compatibilidad con PR inicial.
AffordabilityInput = EntradaCapacidadPago
AffordabilityResult = ResultadoCapacidadPago


class AffordabilityService(ServicioCapacidadPago):
    def evaluate(self, data: AffordabilityInput) -> AffordabilityResult:
        return self.evaluar(data)

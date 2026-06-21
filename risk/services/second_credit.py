"""Servicio de elegibilidad para segundo credito.

Este modulo es dueno de la regla de minimo pagado para evaluar si un cliente
puede solicitar un segundo credito. Es solo lectura y aun no esta conectado a
flujos productivos.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings

from risk.policies.capacidad import evaluar_capacidad
from risk.policies.elegibilidad import evaluar_creditos_activos, evaluar_minimo_pagado
from risk.policies.mora import MOTIVO_MORA_ACTIVA_RELEVANTE, evaluar_mora_relevante
from risk.policies.porcentaje_pagado import a_dinero, calcular_porcentaje_pagado
from risk.selectors import (
    listar_creditos_vigentes_cliente,
    obtener_ultimo_credito_para_revision_segundo_credito,
)


PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO = Decimal('40')
MOTIVO_SIN_CREDITO_PREVIO = 'sin_credito_previo'
MOTIVO_MINIMO_PAGADO_CUMPLIDO = 'minimo_pagado_cumplido'
MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO = 'minimo_pagado_no_cumplido'
MOTIVO_DATOS_DE_PAGO_INSUFICIENTES = 'datos_de_pago_insuficientes'
MOTIVO_SEGUNDO_CREDITO_APROBADO = 'segundo_credito_aprobado'

DOS_DECIMALES = Decimal('0.01')

__all__ = [
    'DOS_DECIMALES',
    'MOTIVO_DATOS_DE_PAGO_INSUFICIENTES',
    'MOTIVO_MINIMO_PAGADO_CUMPLIDO',
    'MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO',
    'MOTIVO_MORA_ACTIVA_RELEVANTE',
    'MOTIVO_SEGUNDO_CREDITO_APROBADO',
    'MOTIVO_SIN_CREDITO_PREVIO',
    'PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO',
    'REASON_INSUFFICIENT_PAYMENT_DATA',
    'REASON_MINIMUM_PAID_NOT_REACHED',
    'REASON_MINIMUM_PAID_REACHED',
    'REASON_NO_PREVIOUS_CREDIT',
    'REQUIRED_PAID_PERCENTAGE',
    'SecondCreditEligibility',
    'SecondCreditService',
    'ElegibilidadSegundoCredito',
    'ServicioSegundoCredito',
    'calculate_paid_percentage',
    'calcular_porcentaje_pagado',
    'evaluate_second_credit_eligibility',
    'evaluar_elegibilidad_segundo_credito',
]


@dataclass(frozen=True)
class ElegibilidadSegundoCredito:
    elegible: bool
    motivo: str
    porcentaje_pagado: Decimal | None = None
    porcentaje_requerido: Decimal = PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO
    credito_bloqueante_id: int | None = None
    cuota_actual: Decimal = Decimal('0.00')
    cuota_proyectada: Decimal = Decimal('0.00')
    porcentaje_comprometido: Decimal | None = None
    capacidad_maxima: Decimal | None = None
    capacidad_residual: Decimal | None = None
    creditos_activos_actuales: int = 0
    maximo_creditos_activos_simultaneos: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def motivos(self) -> tuple[str, ...]:
        return (self.motivo,) if self.motivo else ()

    def como_dict(self) -> dict:
        return {
            'eligible': self.elegible,
            'reason': self.motivo,
            'paid_percentage': self.porcentaje_pagado,
            'required_percentage': self.porcentaje_requerido,
            'blocking_credit_id': self.credito_bloqueante_id,
            'current_installment': self.cuota_actual,
            'projected_installment': self.cuota_proyectada,
            'committed_percentage': self.porcentaje_comprometido,
            'maximum_capacity': self.capacidad_maxima,
            'residual_capacity': self.capacidad_residual,
            'current_active_credits': self.creditos_activos_actuales,
            'maximum_simultaneous_active_credits': self.maximo_creditos_activos_simultaneos,
            'metadata': self.metadata,
        }

    # Compatibilidad con el nombre usado en PR 5.
    @property
    def eligible(self) -> bool:
        return self.elegible

    @property
    def reason(self) -> str:
        return self.motivo

    @property
    def paid_percentage(self) -> Decimal | None:
        return self.porcentaje_pagado

    @property
    def required_percentage(self) -> Decimal:
        return self.porcentaje_requerido

    @property
    def blocking_credit_id(self) -> int | None:
        return self.credito_bloqueante_id

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.motivos

    def as_dict(self) -> dict:
        return self.como_dict()


class ServicioSegundoCredito:
    """Servicio read-only para decisiones de segundo credito."""

    def evaluar(
        self,
        *,
        cliente_id: int,
        linea_credito: str | None = None,
        ingreso_mensual=None,
        cuota_proyectada=None,
    ) -> dict:
        return evaluar_elegibilidad_segundo_credito(
            cliente_id=cliente_id,
            linea_credito=linea_credito,
            ingreso_mensual=ingreso_mensual,
            cuota_proyectada=cuota_proyectada,
        )

    # Compatibilidad con PR 5.
    def evaluate(
        self,
        *,
        customer_id: int,
        product_type: str | None = None,
        monthly_income=None,
        projected_installment=None,
    ) -> dict:
        return self.evaluar(
            cliente_id=customer_id,
            linea_credito=product_type,
            ingreso_mensual=monthly_income,
            cuota_proyectada=projected_installment,
        )


def evaluar_elegibilidad_segundo_credito(
    *,
    cliente_id,
    linea_credito=None,
    porcentaje_requerido=PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO,
    ingreso_mensual=None,
    cuota_proyectada=None,
    porcentaje_maximo_capacidad=None,
    maximo_creditos_activos_simultaneos=None,
) -> dict:
    creditos_vigentes = list(
        listar_creditos_vigentes_cliente(
            cliente_id=cliente_id,
            linea_credito=linea_credito,
        )
    )
    credito = obtener_ultimo_credito_para_revision_segundo_credito(
        cliente_id=cliente_id,
        linea_credito=linea_credito,
    )
    porcentaje_requerido = Decimal(str(porcentaje_requerido))
    porcentaje_maximo_capacidad = Decimal(str(
        porcentaje_maximo_capacidad
        if porcentaje_maximo_capacidad is not None
        else getattr(settings, 'RISK_MAX_DEBT_BURDEN_PERCENTAGE', '50')
    ))
    maximo_creditos_activos_simultaneos = int(
        maximo_creditos_activos_simultaneos
        if maximo_creditos_activos_simultaneos is not None
        else getattr(settings, 'RISK_MAX_SIMULTANEOUS_ACTIVE_CREDITS', 2)
    )

    cuota_actual = sum(
        (a_dinero(getattr(credito_actual, 'valor_cuota', None)) or Decimal('0.00'))
        for credito_actual in creditos_vigentes
    )
    cuota_proyectada = a_dinero(cuota_proyectada) or Decimal('0.00')
    capacidad = evaluar_capacidad(
        ingreso_mensual=ingreso_mensual,
        cuota_actual=cuota_actual,
        cuota_proyectada=cuota_proyectada,
        porcentaje_maximo=porcentaje_maximo_capacidad,
    )

    base_payload = {
        'cuota_actual': capacidad.cuota_actual,
        'cuota_proyectada': capacidad.cuota_proyectada,
        'porcentaje_comprometido': capacidad.porcentaje_comprometido,
        'capacidad_maxima': capacidad.capacidad_maxima,
        'capacidad_residual': capacidad.capacidad_residual,
        'creditos_activos_actuales': len(creditos_vigentes),
        'maximo_creditos_activos_simultaneos': maximo_creditos_activos_simultaneos,
        'metadata': {
            'policy_version': 'risk.second_credit.v1',
            'max_debt_burden_percentage': porcentaje_maximo_capacidad,
        },
    }

    if not credito:
        return ElegibilidadSegundoCredito(
            elegible=True,
            motivo=MOTIVO_SIN_CREDITO_PREVIO,
            porcentaje_requerido=porcentaje_requerido,
            **base_payload,
        ).como_dict()

    mora = evaluar_mora_relevante(creditos_vigentes)
    if not mora.aprobado:
        return ElegibilidadSegundoCredito(
            elegible=False,
            motivo=mora.motivo,
            porcentaje_pagado=calcular_porcentaje_pagado(credito),
            porcentaje_requerido=porcentaje_requerido,
            credito_bloqueante_id=mora.credito_bloqueante_id,
            **base_payload,
        ).como_dict()

    porcentaje_pagado = calcular_porcentaje_pagado(credito)
    minimo_pagado = evaluar_minimo_pagado(porcentaje_pagado, porcentaje_requerido)
    if not minimo_pagado.aprobado:
        return ElegibilidadSegundoCredito(
            elegible=False,
            motivo=minimo_pagado.motivo,
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            credito_bloqueante_id=credito.id,
            **base_payload,
        ).como_dict()

    creditos_activos = evaluar_creditos_activos(
        creditos_activos_actuales=len(creditos_vigentes),
        maximo_creditos_activos_simultaneos=maximo_creditos_activos_simultaneos,
    )
    if not creditos_activos.aprobado:
        return ElegibilidadSegundoCredito(
            elegible=False,
            motivo=creditos_activos.motivo,
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            credito_bloqueante_id=credito.id,
            **base_payload,
        ).como_dict()

    if not capacidad.aprobado:
        return ElegibilidadSegundoCredito(
            elegible=False,
            motivo=capacidad.motivo,
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            credito_bloqueante_id=credito.id,
            **base_payload,
        ).como_dict()

    return ElegibilidadSegundoCredito(
        elegible=True,
        motivo=MOTIVO_MINIMO_PAGADO_CUMPLIDO,
        porcentaje_pagado=porcentaje_pagado,
        porcentaje_requerido=porcentaje_requerido,
        credito_bloqueante_id=None,
        **base_payload,
    ).como_dict()


def _a_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


# Alias de compatibilidad con PR 5.
REQUIRED_PAID_PERCENTAGE = PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO
REASON_NO_PREVIOUS_CREDIT = MOTIVO_SIN_CREDITO_PREVIO
REASON_MINIMUM_PAID_REACHED = MOTIVO_MINIMO_PAGADO_CUMPLIDO
REASON_MINIMUM_PAID_NOT_REACHED = MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO
REASON_INSUFFICIENT_PAYMENT_DATA = MOTIVO_DATOS_DE_PAGO_INSUFICIENTES
TWOPLACES = DOS_DECIMALES
SecondCreditEligibility = ElegibilidadSegundoCredito
SecondCreditService = ServicioSegundoCredito


def evaluate_second_credit_eligibility(
    *,
    customer_id,
    product_type=None,
    required_percentage=REQUIRED_PAID_PERCENTAGE,
    monthly_income=None,
    projected_installment=None,
) -> dict:
    return evaluar_elegibilidad_segundo_credito(
        cliente_id=customer_id,
        linea_credito=product_type,
        porcentaje_requerido=required_percentage,
        ingreso_mensual=monthly_income,
        cuota_proyectada=projected_installment,
    )


def calculate_paid_percentage(credit) -> Decimal | None:
    return calcular_porcentaje_pagado(credit)

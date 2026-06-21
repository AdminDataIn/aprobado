"""Servicio de riesgo para recogida de cartera.

Este modulo es dueno de la decision read-only de recogida de cartera: detecta
si existe un credito vigente con saldo pendiente y calcula cuanto se recogeria
contra el nuevo monto solicitado. No ejecuta desembolsos ni modifica creditos.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from risk.policies.elegibilidad import evaluar_minimo_pagado
from risk.policies.mora import MOTIVO_MORA_ACTIVA_RELEVANTE, evaluar_mora_relevante
from risk.policies.porcentaje_pagado import a_dinero, calcular_porcentaje_pagado, calcular_saldo_pendiente
from risk.selectors import listar_creditos_vigentes_cliente, obtener_credito_vigente_para_recogida_cartera


DOS_DECIMALES = Decimal('0.01')
PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA = Decimal('40')

MOTIVO_SIN_CREDITO_VIGENTE = 'sin_credito_vigente'
MOTIVO_CREDITO_VIGENTE_SIN_SALDO = 'credito_vigente_sin_saldo'
MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO = 'monto_solicitado_menor_o_igual_al_saldo'
MOTIVO_RECOGIDA_CARTERA_APLICA = 'recogida_cartera_aplica'
MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO = 'minimo_pagado_no_cumplido'
MOTIVO_DATOS_DE_PAGO_INSUFICIENTES = 'datos_de_pago_insuficientes'

__all__ = [
    'DOS_DECIMALES',
    'MOTIVO_CREDITO_VIGENTE_SIN_SALDO',
    'MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO',
    'MOTIVO_MORA_ACTIVA_RELEVANTE',
    'MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO',
    'MOTIVO_RECOGIDA_CARTERA_APLICA',
    'MOTIVO_SIN_CREDITO_VIGENTE',
    'PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA',
    'PortfolioTakeoverDecision',
    'PortfolioTakeoverRequest',
    'PortfolioTakeoverService',
    'REASON_CURRENT_CREDIT_WITHOUT_BALANCE',
    'REASON_NO_CURRENT_CREDIT',
    'REASON_REQUESTED_AMOUNT_NOT_ENOUGH',
    'REASON_TAKEOVER_APPLIES',
    'DecisionRecogidaCartera',
    'ServicioRecogidaCartera',
    'SolicitudRecogidaCartera',
    'calcular_decision_recogida_cartera',
    'evaluar_recogida_cartera',
    'evaluate_portfolio_takeover',
]


@dataclass(frozen=True)
class SolicitudRecogidaCartera:
    cliente_id: int
    monto_solicitado: Decimal
    linea_credito: str | None = None
    total_obligaciones_externas: Decimal = Decimal('0.00')


@dataclass(frozen=True)
class DecisionRecogidaCartera:
    aplica: bool
    elegible: bool
    motivo: str
    credito_vigente_id: int | None = None
    saldo_pendiente: Decimal | None = None
    monto_recogida: Decimal | None = None
    monto_desembolso_neto: Decimal | None = None
    porcentaje_pagado: Decimal | None = None
    porcentaje_requerido: Decimal = PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA
    valor_solicitado: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def aprobado(self) -> bool:
        return self.elegible

    @property
    def motivos(self) -> tuple[str, ...]:
        return (self.motivo,) if self.motivo else ()

    def como_dict(self) -> dict:
        return {
            'applies': self.aplica,
            'eligible': self.elegible,
            'reason': self.motivo,
            'current_credit_id': self.credito_vigente_id,
            'outstanding_balance': self.saldo_pendiente,
            'takeover_amount': self.monto_recogida,
            'net_disbursement_amount': self.monto_desembolso_neto,
            'paid_percentage': self.porcentaje_pagado,
            'required_percentage': self.porcentaje_requerido,
            'requested_amount': self.valor_solicitado,
            'metadata': self.metadata,
        }

    # Compatibilidad con el contrato inicial en ingles.
    @property
    def applies(self) -> bool:
        return self.aplica

    @property
    def eligible(self) -> bool:
        return self.elegible

    @property
    def reason(self) -> str:
        return self.motivo

    @property
    def current_credit_id(self) -> int | None:
        return self.credito_vigente_id

    @property
    def outstanding_balance(self) -> Decimal | None:
        return self.saldo_pendiente

    @property
    def takeover_amount(self) -> Decimal | None:
        return self.monto_recogida

    @property
    def net_disbursement_amount(self) -> Decimal | None:
        return self.monto_desembolso_neto

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.motivos

    def as_dict(self) -> dict:
        return self.como_dict()


class ServicioRecogidaCartera:
    """Servicio read-only para decisiones de recogida de cartera."""

    def evaluar(self, solicitud: SolicitudRecogidaCartera) -> dict:
        return evaluar_recogida_cartera(
            cliente_id=solicitud.cliente_id,
            monto_solicitado=solicitud.monto_solicitado,
            linea_credito=solicitud.linea_credito,
        )

    # Compatibilidad con PR inicial.
    def evaluate(self, request: 'PortfolioTakeoverRequest') -> dict:
        return self.evaluar(request)


def evaluar_recogida_cartera(*, cliente_id, monto_solicitado, linea_credito=None) -> dict:
    creditos_vigentes = list(
        listar_creditos_vigentes_cliente(
            cliente_id=cliente_id,
            linea_credito=linea_credito,
        )
    )
    credito_vigente = obtener_credito_vigente_para_recogida_cartera(
        cliente_id=cliente_id,
        linea_credito=linea_credito,
    )
    decision = calcular_decision_recogida_cartera(
        credito_vigente=credito_vigente,
        creditos_vigentes=creditos_vigentes,
        monto_solicitado=monto_solicitado,
    )
    return decision.como_dict()


def calcular_decision_recogida_cartera(
    *,
    credito_vigente,
    monto_solicitado,
    creditos_vigentes=None,
    porcentaje_requerido=PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA,
) -> DecisionRecogidaCartera:
    monto_solicitado = a_dinero(monto_solicitado) or Decimal('0.00')
    porcentaje_requerido = Decimal(str(porcentaje_requerido))
    metadata = {'policy_version': 'risk.portfolio_takeover.v1'}
    if not credito_vigente:
        return DecisionRecogidaCartera(
            aplica=False,
            elegible=True,
            motivo=MOTIVO_SIN_CREDITO_VIGENTE,
            monto_desembolso_neto=monto_solicitado,
            valor_solicitado=monto_solicitado,
            porcentaje_requerido=porcentaje_requerido,
            metadata=metadata,
        )

    creditos_vigentes = creditos_vigentes if creditos_vigentes is not None else [credito_vigente]
    porcentaje_pagado = calcular_porcentaje_pagado(credito_vigente)
    saldo_pendiente = calcular_saldo_pendiente(credito_vigente)
    credito_vigente_id = getattr(credito_vigente, 'id', None)
    if saldo_pendiente <= Decimal('0.00'):
        return DecisionRecogidaCartera(
            aplica=False,
            elegible=True,
            motivo=MOTIVO_CREDITO_VIGENTE_SIN_SALDO,
            credito_vigente_id=credito_vigente_id,
            saldo_pendiente=Decimal('0.00'),
            monto_recogida=Decimal('0.00'),
            monto_desembolso_neto=monto_solicitado,
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            valor_solicitado=monto_solicitado,
            metadata=metadata,
        )

    mora = evaluar_mora_relevante(creditos_vigentes)
    if not mora.aprobado:
        return DecisionRecogidaCartera(
            aplica=True,
            elegible=False,
            motivo=mora.motivo,
            credito_vigente_id=mora.credito_bloqueante_id or credito_vigente_id,
            saldo_pendiente=saldo_pendiente,
            monto_recogida=saldo_pendiente,
            monto_desembolso_neto=Decimal('0.00'),
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            valor_solicitado=monto_solicitado,
            metadata=metadata,
        )

    minimo_pagado = evaluar_minimo_pagado(porcentaje_pagado, porcentaje_requerido)
    if not minimo_pagado.aprobado:
        return DecisionRecogidaCartera(
            aplica=True,
            elegible=False,
            motivo=minimo_pagado.motivo,
            credito_vigente_id=credito_vigente_id,
            saldo_pendiente=saldo_pendiente,
            monto_recogida=saldo_pendiente,
            monto_desembolso_neto=Decimal('0.00'),
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            valor_solicitado=monto_solicitado,
            metadata=metadata,
        )

    if monto_solicitado <= saldo_pendiente:
        return DecisionRecogidaCartera(
            aplica=True,
            elegible=False,
            motivo=MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO,
            credito_vigente_id=credito_vigente_id,
            saldo_pendiente=saldo_pendiente,
            monto_recogida=saldo_pendiente,
            monto_desembolso_neto=Decimal('0.00'),
            porcentaje_pagado=porcentaje_pagado,
            porcentaje_requerido=porcentaje_requerido,
            valor_solicitado=monto_solicitado,
            metadata=metadata,
        )

    return DecisionRecogidaCartera(
        aplica=True,
        elegible=True,
        motivo=MOTIVO_RECOGIDA_CARTERA_APLICA,
        credito_vigente_id=credito_vigente_id,
        saldo_pendiente=saldo_pendiente,
        monto_recogida=saldo_pendiente,
        monto_desembolso_neto=a_dinero(monto_solicitado - saldo_pendiente),
        porcentaje_pagado=porcentaje_pagado,
        porcentaje_requerido=porcentaje_requerido,
        valor_solicitado=monto_solicitado,
        metadata=metadata,
    )


def _a_decimal(valor) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except Exception:
        return None


def _a_dinero(valor) -> Decimal | None:
    return a_dinero(valor)


# Alias de compatibilidad con PR inicial.
PortfolioTakeoverRequest = SolicitudRecogidaCartera
PortfolioTakeoverDecision = DecisionRecogidaCartera
PortfolioTakeoverService = ServicioRecogidaCartera

REASON_NO_CURRENT_CREDIT = MOTIVO_SIN_CREDITO_VIGENTE
REASON_CURRENT_CREDIT_WITHOUT_BALANCE = MOTIVO_CREDITO_VIGENTE_SIN_SALDO
REASON_REQUESTED_AMOUNT_NOT_ENOUGH = MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO
REASON_TAKEOVER_APPLIES = MOTIVO_RECOGIDA_CARTERA_APLICA


def evaluate_portfolio_takeover(*, customer_id, requested_amount, product_type=None) -> dict:
    return evaluar_recogida_cartera(
        cliente_id=customer_id,
        monto_solicitado=requested_amount,
        linea_credito=product_type,
    )

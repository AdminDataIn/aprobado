"""Selectores de solo lectura para el dominio de riesgo.

Los selectors pueden leer modelos legacy de ``gestion_creditos`` mientras los
modelos sigan en esa app, pero no deben mutar datos ni disparar flujos.
"""

from gestion_creditos.models import Credito


ESTADOS_REVISION_SEGUNDO_CREDITO = (
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
)
ESTADOS_CREDITO_VIGENTE_RECOGIDA_CARTERA = (
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
)

__all__ = [
    'ESTADOS_CREDITO_VIGENTE_RECOGIDA_CARTERA',
    'ESTADOS_REVISION_SEGUNDO_CREDITO',
    'PORTFOLIO_TAKEOVER_ACTIVE_STATES',
    'SECOND_CREDIT_REVIEW_STATES',
    'get_current_credit_for_portfolio_takeover',
    'listar_creditos_vigentes_cliente',
    'list_current_customer_credits',
    'get_latest_credit_for_second_credit_review',
    'obtener_credito_vigente_para_recogida_cartera',
    'obtener_ultimo_credito_para_revision_segundo_credito',
]


def obtener_ultimo_credito_para_revision_segundo_credito(*, cliente_id, linea_credito=None):
    queryset = (
        Credito.objects
        .filter(
            usuario_id=cliente_id,
            estado__in=ESTADOS_REVISION_SEGUNDO_CREDITO,
        )
        .order_by('-fecha_solicitud', '-id')
    )
    if linea_credito:
        queryset = queryset.filter(linea=linea_credito)
    return queryset.first()


def obtener_credito_vigente_para_recogida_cartera(*, cliente_id, linea_credito=None):
    queryset = (
        Credito.objects
        .filter(
            usuario_id=cliente_id,
            estado__in=ESTADOS_CREDITO_VIGENTE_RECOGIDA_CARTERA,
        )
        .order_by('-fecha_solicitud', '-id')
    )
    if linea_credito:
        queryset = queryset.filter(linea=linea_credito)
    return queryset.first()


def listar_creditos_vigentes_cliente(*, cliente_id, linea_credito=None):
    queryset = (
        Credito.objects
        .filter(
            usuario_id=cliente_id,
            estado__in=ESTADOS_REVISION_SEGUNDO_CREDITO,
        )
        .order_by('-fecha_solicitud', '-id')
    )
    if linea_credito:
        queryset = queryset.filter(linea=linea_credito)
    return queryset


# Alias de compatibilidad con PR 5.
SECOND_CREDIT_REVIEW_STATES = ESTADOS_REVISION_SEGUNDO_CREDITO
PORTFOLIO_TAKEOVER_ACTIVE_STATES = ESTADOS_CREDITO_VIGENTE_RECOGIDA_CARTERA


def get_latest_credit_for_second_credit_review(*, customer_id, product_type=None):
    return obtener_ultimo_credito_para_revision_segundo_credito(
        cliente_id=customer_id,
        linea_credito=product_type,
    )


def get_current_credit_for_portfolio_takeover(*, customer_id, product_type=None):
    return obtener_credito_vigente_para_recogida_cartera(
        cliente_id=customer_id,
        linea_credito=product_type,
    )


def list_current_customer_credits(*, customer_id, product_type=None):
    return listar_creditos_vigentes_cliente(
        cliente_id=customer_id,
        linea_credito=product_type,
    )

from decimal import Decimal
from typing import Any

from gestion_creditos.models import CreditoReglaEspecialAudit
from libranza.services.special_cases import SpecialCaseSimulationResult


def serialize_simulation_result(result: SpecialCaseSimulationResult) -> dict[str, Any]:
    return _serialize_value(result.as_dict())


def create_special_case_audit(
    *,
    simulation_result: SpecialCaseSimulationResult,
    created_by,
    business_reason: str,
    credito=None,
    ip_address: str | None = None,
    user_agent: str = '',
    simulation_payload: dict[str, Any] | None = None,
) -> CreditoReglaEspecialAudit:
    payload = simulation_payload or serialize_simulation_result(simulation_result)
    return CreditoReglaEspecialAudit.objects.create(
        credito=credito,
        created_by=created_by,
        amount=simulation_result.requested_amount,
        term_months=simulation_result.term_months,
        monthly_rate=simulation_result.monthly_rate,
        commission_rate=simulation_result.commission_rate,
        commission_amount=simulation_result.commission_amount,
        vat_amount=simulation_result.vat_amount,
        estimated_monthly_payment=simulation_result.monthly_payment,
        estimated_total_payment=simulation_result.total_to_pay,
        estimated_interest=simulation_result.estimated_interest,
        simulation_payload=payload,
        business_reason=business_reason,
        ip_address=ip_address,
        user_agent=user_agent or '',
    )


def _serialize_value(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value

"""Pure simulation service for special-case libranza credits.

This module is intentionally detached from Django models, views and settings.
It only validates inputs and calculates financial projections for a future
admin-only special-case flow.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


TWOPLACES = Decimal('0.01')
MAX_SPECIAL_CASE_AMOUNT = Decimal('100000000.00')
MAX_SPECIAL_CASE_TERM_MONTHS = 48
MAX_SPECIAL_CASE_MONTHLY_RATE = Decimal('10.00')
MAX_REASONABLE_COMMISSION_RATE = Decimal('100.00')

REASON_VALID = 'valid'
REASON_AMOUNT_MUST_BE_POSITIVE = 'amount_must_be_positive'
REASON_AMOUNT_EXCEEDS_LIMIT = 'amount_exceeds_special_case_limit'
REASON_TERM_BELOW_MINIMUM = 'term_below_minimum'
REASON_TERM_EXCEEDS_LIMIT = 'term_exceeds_special_case_limit'
REASON_MONTHLY_RATE_NEGATIVE = 'monthly_rate_negative'
REASON_MONTHLY_RATE_TOO_PRECISE = 'monthly_rate_too_precise'
REASON_MONTHLY_RATE_EXCEEDS_LIMIT = 'monthly_rate_exceeds_special_case_limit'
REASON_COMMISSION_RATE_INVALID = 'commission_rate_invalid'
REASON_COMMISSION_AMOUNT_NEGATIVE = 'commission_amount_negative'


class SpecialCaseSimulationError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SpecialCaseSimulationInput:
    amount: Decimal
    term_months: int
    monthly_rate: Decimal
    vat_rate: Decimal
    commission_rate: Decimal | None = None
    commission_amount: Decimal | None = None
    include_vat: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpecialCaseSimulationResult:
    requested_amount: Decimal
    commission_amount: Decimal
    vat_amount: Decimal
    principal_financed: Decimal
    estimated_interest: Decimal
    total_to_pay: Decimal
    monthly_payment: Decimal
    monthly_rate: Decimal
    term_months: int
    commission_rate: Decimal | None
    vat_rate: Decimal
    include_vat: bool
    eligible: bool = True
    reason: str = REASON_VALID
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            'requested_amount': self.requested_amount,
            'commission_amount': self.commission_amount,
            'vat_amount': self.vat_amount,
            'principal_financed': self.principal_financed,
            'estimated_interest': self.estimated_interest,
            'total_to_pay': self.total_to_pay,
            'monthly_payment': self.monthly_payment,
            'monthly_rate': self.monthly_rate,
            'term_months': self.term_months,
            'commission_rate': self.commission_rate,
            'vat_rate': self.vat_rate,
            'include_vat': self.include_vat,
            'eligible': self.eligible,
            'reason': self.reason,
            'metadata': self.metadata,
        }


def simulate_special_case_libranza(data: SpecialCaseSimulationInput | dict) -> SpecialCaseSimulationResult:
    if isinstance(data, dict):
        data = SpecialCaseSimulationInput(**data)

    amount = _money(data.amount)
    term_months = int(data.term_months)
    monthly_rate = _decimal(data.monthly_rate)
    vat_rate = _decimal(data.vat_rate)
    commission_rate = _optional_decimal(data.commission_rate)
    fixed_commission = _optional_money(data.commission_amount)

    _validate_inputs(
        amount=amount,
        term_months=term_months,
        monthly_rate=monthly_rate,
        commission_rate=commission_rate,
        commission_amount=fixed_commission,
    )

    commission_amount = _resolve_commission_amount(
        amount=amount,
        commission_rate=commission_rate,
        commission_amount=fixed_commission,
    )
    vat_amount = _money(commission_amount * vat_rate / Decimal('100')) if data.include_vat else Decimal('0.00')
    principal_financed = _money(amount + commission_amount + vat_amount)
    monthly_payment = _calculate_monthly_payment(
        principal_financed=principal_financed,
        monthly_rate=monthly_rate,
        term_months=term_months,
    )
    total_to_pay = _money(monthly_payment * Decimal(term_months))
    estimated_interest = _money(max(Decimal('0.00'), total_to_pay - principal_financed))

    return SpecialCaseSimulationResult(
        requested_amount=amount,
        commission_amount=commission_amount,
        vat_amount=vat_amount,
        principal_financed=principal_financed,
        estimated_interest=estimated_interest,
        total_to_pay=total_to_pay,
        monthly_payment=monthly_payment,
        monthly_rate=monthly_rate,
        term_months=term_months,
        commission_rate=commission_rate,
        vat_rate=vat_rate,
        include_vat=data.include_vat,
        metadata=data.metadata,
    )


def _validate_inputs(*, amount, term_months, monthly_rate, commission_rate, commission_amount):
    if amount <= Decimal('0.00'):
        raise SpecialCaseSimulationError(REASON_AMOUNT_MUST_BE_POSITIVE)
    if amount > MAX_SPECIAL_CASE_AMOUNT:
        raise SpecialCaseSimulationError(REASON_AMOUNT_EXCEEDS_LIMIT)
    if term_months < 1:
        raise SpecialCaseSimulationError(REASON_TERM_BELOW_MINIMUM)
    if term_months > MAX_SPECIAL_CASE_TERM_MONTHS:
        raise SpecialCaseSimulationError(REASON_TERM_EXCEEDS_LIMIT)
    if monthly_rate < Decimal('0.00'):
        raise SpecialCaseSimulationError(REASON_MONTHLY_RATE_NEGATIVE)
    if _decimal_places(monthly_rate) > 2:
        raise SpecialCaseSimulationError(REASON_MONTHLY_RATE_TOO_PRECISE)
    if monthly_rate > MAX_SPECIAL_CASE_MONTHLY_RATE:
        raise SpecialCaseSimulationError(REASON_MONTHLY_RATE_EXCEEDS_LIMIT)
    if commission_rate is not None and (
        commission_rate < Decimal('0.00') or commission_rate > MAX_REASONABLE_COMMISSION_RATE
    ):
        raise SpecialCaseSimulationError(REASON_COMMISSION_RATE_INVALID)
    if commission_amount is not None and commission_amount < Decimal('0.00'):
        raise SpecialCaseSimulationError(REASON_COMMISSION_AMOUNT_NEGATIVE)


def _resolve_commission_amount(*, amount, commission_rate, commission_amount) -> Decimal:
    percentage_commission = Decimal('0.00')
    fixed_commission = commission_amount or Decimal('0.00')
    if commission_rate is not None:
        percentage_commission = _money(amount * commission_rate / Decimal('100'))
    return _money(percentage_commission + fixed_commission)


def _calculate_monthly_payment(*, principal_financed, monthly_rate, term_months) -> Decimal:
    rate = monthly_rate / Decimal('100')
    if rate <= Decimal('0.00'):
        return _money(principal_financed / Decimal(term_months))

    factor = (rate * (Decimal('1.00') + rate) ** term_months) / (
        ((Decimal('1.00') + rate) ** term_months) - Decimal('1.00')
    )
    return _money(principal_financed * factor)


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _optional_decimal(value) -> Decimal | None:
    return None if value is None else _decimal(value)


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return abs(exponent) if exponent < 0 else 0


def _money(value) -> Decimal:
    return _decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _optional_money(value) -> Decimal | None:
    return None if value is None else _money(value)

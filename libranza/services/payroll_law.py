"""Payroll-law boundary for libranza.

Use this module for legal constraints and evidence required by the payroll-loan
product. Keep generic credit risk decisions in risk.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PayrollLawCompliance:
    compliant: bool
    reasons: tuple[str, ...] = ()


class PayrollLawService:
    """Interface placeholder for payroll-law compliance checks."""

    def check(self, *, customer_id: int, company_id: int | None = None) -> PayrollLawCompliance:
        raise NotImplementedError("Payroll-law compliance is not implemented in libranza yet.")


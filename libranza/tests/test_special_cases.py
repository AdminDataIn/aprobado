from decimal import Decimal
import importlib
import sys

from django.test import SimpleTestCase

from libranza.services.special_cases import (
    MAX_SPECIAL_CASE_AMOUNT,
    MAX_SPECIAL_CASE_MONTHLY_RATE,
    MAX_SPECIAL_CASE_TERM_MONTHS,
    REASON_AMOUNT_EXCEEDS_LIMIT,
    REASON_COMMISSION_AMOUNT_NEGATIVE,
    REASON_MONTHLY_RATE_EXCEEDS_LIMIT,
    REASON_MONTHLY_RATE_NEGATIVE,
    REASON_MONTHLY_RATE_TOO_PRECISE,
    REASON_TERM_EXCEEDS_LIMIT,
    SpecialCaseSimulationError,
    SpecialCaseSimulationInput,
    simulate_special_case_libranza,
)


class SpecialCaseSimulationTests(SimpleTestCase):
    def test_valid_simulation_with_percentage_commission(self):
        result = simulate_special_case_libranza(
            SpecialCaseSimulationInput(
                amount=Decimal('10000000.00'),
                term_months=24,
                monthly_rate=Decimal('1.90'),
                commission_rate=Decimal('5.00'),
                vat_rate=Decimal('19.00'),
            )
        )

        self.assertEqual(result.requested_amount, Decimal('10000000.00'))
        self.assertEqual(result.commission_amount, Decimal('500000.00'))
        self.assertEqual(result.vat_amount, Decimal('95000.00'))
        self.assertEqual(result.principal_financed, Decimal('10595000.00'))
        self.assertEqual(result.monthly_rate, Decimal('1.90'))
        self.assertEqual(result.term_months, 24)
        self.assertTrue(result.monthly_payment > Decimal('0.00'))
        self.assertTrue(result.total_to_pay > result.principal_financed)
        self.assertTrue(result.estimated_interest > Decimal('0.00'))
        self.assertTrue(result.eligible)

    def test_valid_simulation_with_fixed_commission(self):
        result = simulate_special_case_libranza(
            {
                'amount': Decimal('5000000.00'),
                'term_months': 12,
                'monthly_rate': Decimal('0.00'),
                'commission_amount': Decimal('250000.00'),
                'vat_rate': Decimal('19.00'),
            }
        )

        self.assertEqual(result.commission_amount, Decimal('250000.00'))
        self.assertEqual(result.vat_amount, Decimal('47500.00'))
        self.assertEqual(result.principal_financed, Decimal('5297500.00'))
        self.assertEqual(result.monthly_payment, Decimal('441458.33'))
        self.assertEqual(result.total_to_pay, Decimal('5297499.96'))
        self.assertEqual(result.estimated_interest, Decimal('0.00'))

    def test_rejects_amount_over_100m(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(
                _input(amount=MAX_SPECIAL_CASE_AMOUNT + Decimal('0.01'))
            )

        self.assertEqual(ctx.exception.reason, REASON_AMOUNT_EXCEEDS_LIMIT)

    def test_rejects_term_over_24(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(
                _input(term_months=MAX_SPECIAL_CASE_TERM_MONTHS + 1)
            )

        self.assertEqual(ctx.exception.reason, REASON_TERM_EXCEEDS_LIMIT)

    def test_rejects_negative_rate(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(_input(monthly_rate=Decimal('-0.01')))

        self.assertEqual(ctx.exception.reason, REASON_MONTHLY_RATE_NEGATIVE)

    def test_rejects_monthly_rate_with_more_than_two_decimals(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(_input(monthly_rate=Decimal('1.901')))

        self.assertEqual(ctx.exception.reason, REASON_MONTHLY_RATE_TOO_PRECISE)

    def test_rejects_monthly_rate_above_safe_limit(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(
                _input(monthly_rate=MAX_SPECIAL_CASE_MONTHLY_RATE + Decimal('0.01'))
            )

        self.assertEqual(ctx.exception.reason, REASON_MONTHLY_RATE_EXCEEDS_LIMIT)

    def test_rejects_negative_commission(self):
        with self.assertRaises(SpecialCaseSimulationError) as ctx:
            simulate_special_case_libranza(_input(commission_amount=Decimal('-1.00')))

        self.assertEqual(ctx.exception.reason, REASON_COMMISSION_AMOUNT_NEGATIVE)

    def test_vat_can_be_excluded(self):
        result = simulate_special_case_libranza(
            _input(
                amount=Decimal('1000000.00'),
                commission_rate=Decimal('10.00'),
                vat_rate=Decimal('19.00'),
                include_vat=False,
            )
        )

        self.assertEqual(result.commission_amount, Decimal('100000.00'))
        self.assertEqual(result.vat_amount, Decimal('0.00'))
        self.assertEqual(result.principal_financed, Decimal('1100000.00'))

    def test_module_does_not_import_django_models(self):
        sys.modules.pop('libranza.services.special_cases', None)
        sys.modules.pop('gestion_creditos.models', None)

        importlib.import_module('libranza.services.special_cases')

        self.assertNotIn('gestion_creditos.models', sys.modules)


def _input(**overrides):
    data = {
        'amount': Decimal('1000000.00'),
        'term_months': 12,
        'monthly_rate': Decimal('1.90'),
        'commission_rate': Decimal('5.00'),
        'vat_rate': Decimal('19.00'),
    }
    data.update(overrides)
    return SpecialCaseSimulationInput(**data)

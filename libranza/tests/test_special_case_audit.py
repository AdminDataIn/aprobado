from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos.models import Credito, CreditoReglaEspecialAudit
from libranza.services.special_case_audit import (
    create_special_case_audit,
    serialize_simulation_result,
)
from libranza.services.special_cases import SpecialCaseSimulationInput, simulate_special_case_libranza


User = get_user_model()


class SpecialCaseAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='special-audit-admin')

    def test_create_valid_audit_without_credit(self):
        simulation = _simulation()

        audit = create_special_case_audit(
            simulation_result=simulation,
            created_by=self.user,
            business_reason='Caso aprobado por excepcion comercial.',
            ip_address='127.0.0.1',
            user_agent='TestAgent/1.0',
        )

        self.assertIsNone(audit.credito)
        self.assertEqual(audit.created_by, self.user)
        self.assertEqual(audit.amount, Decimal('10000000.00'))
        self.assertEqual(audit.term_months, 24)
        self.assertEqual(audit.monthly_rate, Decimal('1.90'))
        self.assertEqual(audit.commission_rate, Decimal('5.0000'))
        self.assertEqual(audit.business_reason, 'Caso aprobado por excepcion comercial.')
        self.assertEqual(audit.ip_address, '127.0.0.1')
        self.assertEqual(audit.user_agent, 'TestAgent/1.0')

    def test_payload_serialized_correctly(self):
        simulation = _simulation()

        payload = serialize_simulation_result(simulation)

        self.assertEqual(payload['requested_amount'], '10000000.00')
        self.assertEqual(payload['commission_amount'], '500000.00')
        self.assertEqual(payload['vat_amount'], '95000.00')
        self.assertEqual(payload['monthly_rate'], '1.90')
        self.assertEqual(payload['term_months'], 24)
        self.assertEqual(payload['metadata']['source'], 'unit-test')

    def test_ordering_newest_first(self):
        first = create_special_case_audit(
            simulation_result=_simulation(amount=Decimal('1000000.00')),
            created_by=self.user,
            business_reason='Primera simulacion.',
        )
        second = create_special_case_audit(
            simulation_result=_simulation(amount=Decimal('2000000.00')),
            created_by=self.user,
            business_reason='Segunda simulacion.',
        )
        CreditoReglaEspecialAudit.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=1)
        )
        CreditoReglaEspecialAudit.objects.filter(pk=second.pk).update(
            created_at=timezone.now()
        )

        audits = list(CreditoReglaEspecialAudit.objects.all())

        self.assertEqual(audits[0].pk, second.pk)
        self.assertEqual(audits[1].pk, first.pk)

    def test_nullable_credit_works(self):
        audit = create_special_case_audit(
            simulation_result=_simulation(),
            created_by=self.user,
            business_reason='Simulacion sin originacion.',
            credito=None,
        )

        self.assertIsNone(audit.credito_id)

    def test_audit_can_reference_credit(self):
        credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.EN_REVISION,
            monto_solicitado=Decimal('10000000.00'),
            plazo_solicitado=24,
        )

        audit = create_special_case_audit(
            simulation_result=_simulation(),
            created_by=self.user,
            business_reason='Simulacion asociada a credito futuro.',
            credito=credito,
        )

        self.assertEqual(audit.credito, credito)
        self.assertIn(credito.numero_credito, str(audit))

    def test_audit_preserves_exact_financial_values(self):
        simulation = _simulation(
            amount=Decimal('5000000.00'),
            term_months=12,
            monthly_rate=Decimal('0.00'),
            commission_amount=Decimal('250000.00'),
            commission_rate=None,
        )

        audit = create_special_case_audit(
            simulation_result=simulation,
            created_by=self.user,
            business_reason='Comision fija negociada.',
        )

        self.assertEqual(audit.amount, simulation.requested_amount)
        self.assertEqual(audit.commission_rate, simulation.commission_rate)
        self.assertEqual(audit.commission_amount, simulation.commission_amount)
        self.assertEqual(audit.vat_amount, simulation.vat_amount)
        self.assertEqual(audit.estimated_monthly_payment, simulation.monthly_payment)
        self.assertEqual(audit.estimated_total_payment, simulation.total_to_pay)
        self.assertEqual(audit.estimated_interest, simulation.estimated_interest)


def _simulation(**overrides):
    data = {
        'amount': Decimal('10000000.00'),
        'term_months': 24,
        'monthly_rate': Decimal('1.90'),
        'commission_rate': Decimal('5.00'),
        'vat_rate': Decimal('19.00'),
        'metadata': {'source': 'unit-test'},
    }
    data.update(overrides)
    return simulate_special_case_libranza(SpecialCaseSimulationInput(**data))

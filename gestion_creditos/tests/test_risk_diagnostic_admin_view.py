from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CreditoReglaEspecialAudit,
    HistorialEstado,
    HistorialPago,
)


User = get_user_model()


class RiskDiagnosticAdminViewTests(TestCase):
    def setUp(self):
        self.url = reverse('gestion:risk_diagnostic')
        self.permission = Permission.objects.get(codename='can_run_risk_diagnostic')
        self.staff = User.objects.create_user(
            username='risk-diagnostic-staff',
            password='123456',
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username='risk-diagnostic-user',
            password='123456',
            is_staff=False,
        )
        self.customer = User.objects.create_user(
            username='100200300',
            email='customer@example.com',
        )

    def grant_permission(self):
        self.staff.user_permissions.add(self.permission)

    def payload(self, **overrides):
        data = {
            'document_number': self.customer.username,
            'requested_amount': '1000000.00',
            'projected_monthly_payment': '250000.00',
            'monthly_income': '2000000.00',
            'scenario': 'second_credit',
        }
        data.update(overrides)
        return data

    def create_credit(self, **overrides):
        data = {
            'usuario': self.customer,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'monto_solicitado': Decimal('1000000.00'),
            'monto_aprobado': Decimal('1000000.00'),
            'plazo_solicitado': 6,
            'plazo': 6,
            'valor_cuota': Decimal('120000.00'),
            'capital_pendiente': Decimal('600000.00'),
            'saldo_pendiente': Decimal('600000.00'),
            'total_a_pagar': Decimal('1200000.00'),
        }
        data.update(overrides)
        return Credito.objects.create(**data)

    def test_anonymous_user_cannot_access(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_without_permission_cannot_access(self):
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_staff_with_permission_can_access(self):
        self.grant_permission()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Diagnostico interno de riesgo')
        self.assertContains(response, 'Numero de documento')
        self.assertContains(response, 'Diagnostico preliminar interno, no constituye aprobacion automatica.')
        self.assertContains(response, 'Segundo credito:')
        self.assertContains(response, 'Recogida de cartera:')
        self.assertNotContains(response, 'Descuentos actuales')

    def test_second_credit_diagnostic_eligible(self):
        self.grant_permission()
        self.create_credit()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Elegible')
        self.assertContains(response, 'minimo_pagado_cumplido')
        self.assertContains(response, 'Porcentaje pagado')

    def test_second_credit_diagnostic_rejected(self):
        self.grant_permission()
        self.create_credit(capital_pendiente=Decimal('800000.00'))
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No elegible')
        self.assertContains(response, 'minimo_pagado_no_cumplido')

    def test_portfolio_takeover_diagnostic_eligible(self):
        self.grant_permission()
        self.create_credit(saldo_pendiente=Decimal('300000.00'), capital_pendiente=Decimal('300000.00'))
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(
            self.url,
            self.payload(scenario='portfolio_takeover', requested_amount='1000000.00'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Elegible')
        self.assertContains(response, 'recogida_cartera_aplica')
        self.assertContains(response, 'Desembolso neto')

    def test_portfolio_takeover_diagnostic_rejected(self):
        self.grant_permission()
        self.create_credit(saldo_pendiente=Decimal('700000.00'), capital_pendiente=Decimal('700000.00'))
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(
            self.url,
            self.payload(scenario='portfolio_takeover', requested_amount='1000000.00'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No elegible')
        self.assertContains(response, 'minimo_pagado_no_cumplido')

    def test_diagnostic_does_not_create_credit_or_modify_states(self):
        self.grant_permission()
        credit = self.create_credit()
        original_state = credit.estado
        original_saldo = credit.saldo_pendiente
        original_capital = credit.capital_pendiente
        original_valor_cuota = credit.valor_cuota
        credit_count = Credito.objects.count()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.count(), credit_count)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(HistorialEstado.objects.count(), 0)
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 0)
        credit.refresh_from_db()
        self.assertEqual(credit.estado, original_state)
        self.assertEqual(credit.saldo_pendiente, original_saldo)
        self.assertEqual(credit.capital_pendiente, original_capital)
        self.assertEqual(credit.valor_cuota, original_valor_cuota)

    def test_unknown_document_returns_customer_not_found(self):
        self.grant_permission()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload(document_number='999999999'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No elegible')
        self.assertContains(response, 'cliente_no_encontrado')
        self.assertEqual(Credito.objects.count(), 0)

    def test_second_credit_without_monthly_income_does_not_block_by_capacity(self):
        self.grant_permission()
        self.create_credit()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(
            self.url,
            self.payload(monthly_income='', projected_monthly_payment='9999999.00'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Elegible')
        self.assertContains(response, 'minimo_pagado_cumplido')
        self.assertNotContains(response, 'capacidad_insuficiente')

    @override_settings(RISK_MAX_SIMULTANEOUS_ACTIVE_CREDITS=1)
    def test_multiple_active_credits_block_when_limit_is_exceeded(self):
        self.grant_permission()
        self.create_credit(valor_cuota=Decimal('100000.00'), capital_pendiente=Decimal('500000.00'))
        self.create_credit(valor_cuota=Decimal('100000.00'), capital_pendiente=Decimal('500000.00'))
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No elegible')
        self.assertContains(response, 'maximo_creditos_activos_superado')

    def test_portfolio_takeover_with_multiple_active_credits_uses_latest_credit(self):
        self.grant_permission()
        first_credit = self.create_credit(
            saldo_pendiente=Decimal('100000.00'),
            capital_pendiente=Decimal('100000.00'),
        )
        latest_credit = self.create_credit(
            saldo_pendiente=Decimal('300000.00'),
            capital_pendiente=Decimal('300000.00'),
        )
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(
            self.url,
            self.payload(scenario='portfolio_takeover', requested_amount='1000000.00'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<td>{latest_credit.id}</td>', html=True)
        self.assertContains(response, '$700.000,00')
        self.assertNotContains(response, f'<td>{first_credit.id}</td>', html=True)

    def test_staff_with_permission_can_post(self):
        self.grant_permission()
        self.create_credit()
        self.client.login(username='risk-diagnostic-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resultado')

    def test_public_alternative_route_does_not_exist(self):
        response = self.client.get('/libranza/risk/diagnostico/')

        self.assertEqual(response.status_code, 404)

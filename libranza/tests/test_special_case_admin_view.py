from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import Credito, CreditoLibranza, CreditoReglaEspecialAudit


User = get_user_model()


class SpecialCaseAdminViewTests(TestCase):
    def setUp(self):
        self.url = reverse('gestion:libranza_special_case_simulator')
        self.staff = User.objects.create_user(
            username='special-staff',
            password='123456',
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username='special-user',
            password='123456',
            is_staff=False,
        )
        self.permission = Permission.objects.get(codename='can_originate_special_libranza')

    def grant_special_case_permission(self, user=None):
        user = user or self.staff
        user.user_permissions.add(self.permission)

    def valid_payload(self, **overrides):
        data = {
            'amount': '10000000.00',
            'term_months': '24',
            'monthly_rate': '1.90',
            'commission_rate': '5.0000',
            'commission_amount': '',
            'vat_rate': '19.0000',
            'business_reason': 'Caso especial aprobado para simulacion interna.',
        }
        data.update(overrides)
        return data

    def test_anonymous_user_cannot_access(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_non_staff_user_cannot_access(self):
        self.client.login(username='special-user', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_sees_form(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Simulador de casos especiales de libranza')
        self.assertContains(response, 'Motivo del caso especial')

    def test_staff_without_permission_cannot_access(self):
        self.client.login(username='special-staff', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_valid_post_creates_audit(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(
            self.url,
            self.valid_payload(),
            HTTP_USER_AGENT='SpecialCaseTest/1.0',
            REMOTE_ADDR='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 1)
        audit = CreditoReglaEspecialAudit.objects.get()
        self.assertEqual(audit.created_by, self.staff)
        self.assertEqual(audit.amount, Decimal('10000000.00'))
        self.assertEqual(audit.term_months, 24)
        self.assertEqual(audit.monthly_rate, Decimal('1.90'))
        self.assertEqual(audit.commission_rate, Decimal('5.0000'))
        self.assertEqual(audit.commission_amount, Decimal('500000.00'))
        self.assertEqual(audit.vat_amount, Decimal('95000.00'))
        self.assertEqual(audit.ip_address, '127.0.0.1')
        self.assertEqual(audit.user_agent, 'SpecialCaseTest/1.0')

    def test_valid_post_shows_result(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(self.url, self.valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resultado de simulacion')
        self.assertContains(response, 'Auditoria generada')
        self.assertContains(response, 'Cuota estimada')
        self.assertContains(response, 'Total estimado')
        self.assertContains(response, '#1')

    def test_invalid_post_does_not_create_audit(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(
            self.url,
            self.valid_payload(commission_amount='100000.00'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no ambas simultaneamente')
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 0)

    def test_post_does_not_create_credit_or_libranza_detail(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(self.url, self.valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 1)

    def test_public_libranza_area_does_not_expose_special_case_route(self):
        response = self.client.get('/libranza/casos-especiales/simular/')

        self.assertEqual(response.status_code, 404)

    def test_monthly_rate_with_more_than_two_decimals_is_rejected(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(self.url, self.valid_payload(monthly_rate='1.901'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 0)
        self.assertContains(response, 'no hayan')

    def test_high_monthly_rate_is_rejected(self):
        self.grant_special_case_permission()
        self.client.login(username='special-staff', password='123456')

        response = self.client.post(self.url, self.valid_payload(monthly_rate='10.01'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), 0)
        self.assertContains(response, 'menor o igual a 10.00')

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CreditoReglaEspecialAudit,
    Empresa,
    HistorialEstado,
    Pagare,
)
from libranza.services.special_case_audit import create_special_case_audit
from libranza.services.special_cases import SpecialCaseSimulationInput, simulate_special_case_libranza


User = get_user_model()


class SpecialCaseOriginatorViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='origin-staff', password='123456', is_staff=True)
        self.staff_without_permission = User.objects.create_user(
            username='origin-staff-no-perm',
            password='123456',
            is_staff=True,
        )
        self.user = User.objects.create_user(username='origin-user', password='123456', is_staff=False)
        self.empresa = Empresa.objects.create(nombre='Empresa Originacion Especial', convenio_activo=True)
        self.permission = Permission.objects.get(codename='can_originate_special_libranza')
        self.staff.user_permissions.add(self.permission)
        self.audit = self.create_audit()
        self.url = reverse('gestion:libranza_special_case_originate', args=[self.audit.id])

    def create_audit(self, amount=Decimal('10000000.00')):
        simulation = simulate_special_case_libranza(
            SpecialCaseSimulationInput(
                amount=amount,
                term_months=24,
                monthly_rate=Decimal('1.90'),
                commission_rate=Decimal('5.0000'),
                vat_rate=Decimal('19.0000'),
            )
        )
        return create_special_case_audit(
            simulation_result=simulation,
            created_by=self.staff,
            business_reason='Caso especial autorizado por comite interno.',
        )

    def payload(self, **overrides):
        data = {
            'tipo_documento': 'CC',
            'numero_documento': '123456789',
            'nombres': 'Cliente',
            'apellidos': 'Especial',
            'celular': '3001234567',
            'correo': 'cliente.especial@example.com',
            'direccion': 'Calle 123',
            'empresa': str(self.empresa.id),
            'ingresos_mensuales': '5000000.00',
            'cedula_frontal': SimpleUploadedFile('frontal.png', b'file', content_type='image/png'),
            'cedula_trasera': SimpleUploadedFile('trasera.png', b'file', content_type='image/png'),
            'certificado_bancario': SimpleUploadedFile('banco.pdf', b'%PDF-1.4', content_type='application/pdf'),
        }
        data.update(overrides)
        return data

    def test_get_confirmation_with_staff(self):
        self.client.login(username='origin-staff', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Originar credito especial de libranza')
        self.assertContains(response, f'auditoria #{self.audit.id}')

    def test_non_staff_user_blocked(self):
        self.client.login(username='origin-user', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_without_permission_blocked(self):
        self.client.login(username='origin-staff-no-perm', password='123456')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_missing_audit_returns_404(self):
        self.client.login(username='origin-staff', password='123456')

        response = self.client.get(reverse('gestion:libranza_special_case_originate', args=[999999]))

        self.assertEqual(response.status_code, 404)

    def test_post_creates_credit_and_libranza_detail(self):
        self.client.login(username='origin-staff', password='123456')

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)
        credito = Credito.objects.get()
        detalle = CreditoLibranza.objects.get()
        self.assertEqual(detalle.credito, credito)
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertEqual(credito.linea, Credito.LineaCredito.LIBRANZA)
        self.assertEqual(credito.monto_solicitado, self.audit.amount)
        self.assertEqual(credito.monto_aprobado, self.audit.amount)
        self.assertEqual(credito.plazo_solicitado, self.audit.term_months)
        self.assertEqual(credito.plazo, self.audit.term_months)
        self.assertEqual(credito.tasa_forzada, self.audit.monthly_rate)
        self.assertEqual(credito.tasa_forzada, Decimal('1.90'))
        self.assertEqual(credito.plazo_forzado, self.audit.term_months)
        self.assertEqual(credito.comision, self.audit.commission_amount)
        self.assertEqual(credito.tipo_regla_credito, Credito.TipoReglaCredito.ESPECIAL)
        self.assertEqual(credito.observacion_regla_especial, self.audit.business_reason)
        self.assertEqual(detalle.cedula, '123456789')
        self.assertEqual(detalle.empresa, self.empresa)

    def test_audit_linked_to_credit(self):
        self.client.login(username='origin-staff', password='123456')

        self.client.post(self.url, self.payload())

        self.audit.refresh_from_db()
        self.assertEqual(self.audit.credito, Credito.objects.get())
        self.assertEqual(self.audit.simulation_payload['origination']['originated_by_id'], self.staff.id)

    def test_double_post_does_not_duplicate(self):
        self.client.login(username='origin-staff', password='123456')

        first = self.client.post(self.url, self.payload())
        second = self.client.post(self.url, self.payload())

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)

    def test_used_audit_rejected(self):
        self.client.login(username='origin-staff', password='123456')
        self.client.post(self.url, self.payload())

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'ya tiene un credito asociado', status_code=400)

    def test_does_not_create_pagare_or_disburse_or_activate(self):
        self.client.login(username='origin-staff', password='123456')

        self.client.post(self.url, self.payload())

        credito = Credito.objects.get()
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertIsNone(credito.fecha_desembolso)
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertEqual(HistorialEstado.objects.filter(credito=credito).count(), 1)
        self.assertEqual(
            HistorialEstado.objects.get(credito=credito).usuario_modificacion,
            self.staff,
        )

    def test_public_libranza_flow_still_does_not_expose_origin_route(self):
        response = self.client.get(f'/libranza/casos-especiales/{self.audit.id}/originar/')

        self.assertEqual(response.status_code, 404)

    def test_can_originate_audit_with_100m_amount(self):
        audit = self.create_audit(amount=Decimal('100000000.00'))
        url = reverse('gestion:libranza_special_case_originate', args=[audit.id])
        self.client.login(username='origin-staff', password='123456')

        response = self.client.post(url, self.payload(correo='cliente.100m@example.com'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Credito.objects.get().monto_solicitado, Decimal('100000000.00'))

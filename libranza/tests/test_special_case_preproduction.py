from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.credit_services import activar_credito
from gestion_creditos.forms import CreditoLibranzaForm
from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CreditoReglaEspecialAudit,
    CuotaAmortizacion,
    Empresa,
    Pagare,
)
from libranza.services.special_case_audit import create_special_case_audit
from libranza.services.special_case_originator import originate_special_case_libranza
from libranza.services.special_cases import SpecialCaseSimulationInput, simulate_special_case_libranza


User = get_user_model()


class SpecialCasePreproductionFlowTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='qa-special-staff', password='123456', is_staff=True)
        self.staff_without_permission = User.objects.create_user(
            username='qa-special-staff-no-perm',
            password='123456',
            is_staff=True,
        )
        permission = Permission.objects.get(codename='can_originate_special_libranza')
        self.staff.user_permissions.add(permission)
        self.empresa = Empresa.objects.create(nombre='Empresa QA Especial', convenio_activo=True)

    def test_originated_special_credit_preserves_audit_financial_conditions(self):
        audit = self.create_audit()

        result = originate_special_case_libranza(
            audit_id=audit.id,
            applicant_data=self.applicant_data(),
            files=self.files(),
            originated_by=self.staff,
        )

        credito = result.credito
        audit.refresh_from_db()
        self.assertEqual(audit.credito, credito)
        self.assertEqual(credito.monto_aprobado, audit.amount)
        self.assertEqual(credito.plazo_forzado, audit.term_months)
        self.assertEqual(credito.tasa_forzada, Decimal('10.00'))
        self.assertEqual(credito.tasa_interes, Decimal('10.00'))
        self.assertEqual(credito.comision, Decimal('100000000.00'))
        self.assertEqual(credito.iva_comision, Decimal('19000000.00'))
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertEqual(CreditoLibranza.objects.filter(credito=credito).count(), 1)

    def test_activar_credito_respects_forced_terms_and_persisted_commission_values(self):
        audit = self.create_audit()
        result = originate_special_case_libranza(
            audit_id=audit.id,
            applicant_data=self.applicant_data(correo='activar.especial@example.com'),
            files=self.files(),
            originated_by=self.staff,
        )

        activar_credito(result.credito)

        credito = Credito.objects.get(pk=result.credito.pk)
        self.assertEqual(credito.tasa_interes, Decimal('10.00'))
        self.assertEqual(credito.plazo, 24)
        self.assertEqual(credito.plazo_forzado, 24)
        self.assertEqual(credito.tasa_forzada, Decimal('10.00'))
        self.assertEqual(credito.comision, Decimal('100000000.00'))
        self.assertEqual(credito.iva_comision, Decimal('19000000.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('100000000.00'))
        self.assertEqual(credito.saldo_pendiente, Decimal('219000000.00'))
        self.assertIsNotNone(credito.total_a_pagar)
        self.assertGreater(credito.total_a_pagar, credito.saldo_pendiente)
        self.assertLess(credito.total_a_pagar, Decimal('9999999999.99'))
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito).count(), 24)

    def test_public_libranza_form_still_rejects_amount_above_normal_limit(self):
        form = CreditoLibranzaForm(
            data={
                'valor_credito': '100000000',
                'ingresos_mensuales': '5000000',
                'plazo': '6',
                'nombres': 'Cliente',
                'apellidos': 'Publico',
                'cedula': '123456789',
                'direccion': 'Calle Publica',
                'telefono': '3001234567',
                'correo_electronico': 'publico@example.com',
                'empresa': str(self.empresa.id),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('valor_credito', form.errors)
        self.assertIn('no puede ser mayor a $3.000.000', str(form.errors['valor_credito']))

    def test_staff_without_specific_permission_cannot_simulate_or_originate(self):
        audit = self.create_audit()
        self.client.login(username='qa-special-staff-no-perm', password='123456')

        simulator_response = self.client.get(reverse('gestion:libranza_special_case_simulator'))
        origin_response = self.client.get(reverse('gestion:libranza_special_case_originate', args=[audit.id]))

        self.assertEqual(simulator_response.status_code, 403)
        self.assertEqual(origin_response.status_code, 403)

    def test_originating_from_audit_does_not_create_pagare_or_disbursement(self):
        audit = self.create_audit()

        result = originate_special_case_libranza(
            audit_id=audit.id,
            applicant_data=self.applicant_data(correo='sin.pagare@example.com'),
            files=self.files(),
            originated_by=self.staff,
        )

        credito = Credito.objects.get(pk=result.credito.pk)
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertIsNone(credito.fecha_desembolso)
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)

    def test_audit_cannot_be_originated_twice(self):
        audit = self.create_audit()

        originate_special_case_libranza(
            audit_id=audit.id,
            applicant_data=self.applicant_data(correo='primera@example.com'),
            files=self.files(),
            originated_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('gestion:libranza_special_case_originate', args=[audit.id]),
            self.post_payload(correo='segunda@example.com'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoReglaEspecialAudit.objects.get(pk=audit.pk).credito, Credito.objects.get())

    def create_audit(self):
        simulation = simulate_special_case_libranza(
            SpecialCaseSimulationInput(
                amount=Decimal('100000000.00'),
                term_months=24,
                monthly_rate=Decimal('10.00'),
                commission_rate=Decimal('100.00'),
                vat_rate=Decimal('19.00'),
            )
        )
        return create_special_case_audit(
            simulation_result=simulation,
            created_by=self.staff,
            business_reason='QA preproduccion caso especial maximo razonable.',
        )

    def applicant_data(self, **overrides):
        data = {
            'tipo_documento': 'CC',
            'numero_documento': '123456789',
            'nombres': 'Cliente',
            'apellidos': 'Especial QA',
            'celular': '3001234567',
            'correo': 'qa.especial@example.com',
            'direccion': 'Calle QA 123',
            'empresa': self.empresa,
            'ingresos_mensuales': Decimal('12000000.00'),
        }
        data.update(overrides)
        return data

    def post_payload(self, **overrides):
        data = {
            'tipo_documento': 'CC',
            'numero_documento': '123456789',
            'nombres': 'Cliente',
            'apellidos': 'Especial QA',
            'celular': '3001234567',
            'correo': 'qa.especial.post@example.com',
            'direccion': 'Calle QA 123',
            'empresa': str(self.empresa.id),
            'ingresos_mensuales': '12000000.00',
            **self.files(),
        }
        data.update(overrides)
        return data

    def files(self):
        return {
            'cedula_frontal': SimpleUploadedFile('frontal.png', b'file', content_type='image/png'),
            'cedula_trasera': SimpleUploadedFile('trasera.png', b'file', content_type='image/png'),
            'certificado_bancario': SimpleUploadedFile('banco.pdf', b'%PDF-1.4', content_type='application/pdf'),
        }

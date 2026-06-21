from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ContractorProfile,
)


class AdminContratistasTests(TestCase):
    def test_modelos_registrados_en_admin(self):
        self.assertIn(ContractorOrganization, admin.site._registry)
        self.assertIn(ContractorBranding, admin.site._registry)
        self.assertIn(ContractorProductConfig, admin.site._registry)
        self.assertIn(ContractorProfile, admin.site._registry)
        self.assertIn(ContractorApplication, admin.site._registry)
        self.assertIn(ContractorApplicationDocument, admin.site._registry)

    def test_admin_muestra_metadatos_readonly(self):
        for modelo in (
            ContractorOrganization,
            ContractorBranding,
            ContractorProductConfig,
            ContractorProfile,
            ContractorApplication,
        ):
            admin_modelo = admin.site._registry[modelo]
            self.assertIn('created_at', admin_modelo.readonly_fields)
            self.assertIn('updated_at', admin_modelo.readonly_fields)

    def test_nombres_visibles_en_espanol(self):
        self.assertEqual(ContractorOrganization._meta.verbose_name, 'Organizacion contratista')
        self.assertEqual(ContractorBranding._meta.verbose_name, 'Marca contratista')
        self.assertEqual(ContractorProductConfig._meta.verbose_name, 'Configuracion de producto contratista')
        self.assertEqual(ContractorProfile._meta.verbose_name, 'Perfil contratista')
        self.assertEqual(ContractorApplication._meta.verbose_name, 'Pre-solicitud contratista')
        self.assertEqual(ContractorApplicationDocument._meta.verbose_name, 'Documento de pre-solicitud contratista')

    def test_admin_solicitud_muestra_usuario_asociado(self):
        admin_modelo = admin.site._registry[ContractorApplication]

        self.assertIn('usuario', admin_modelo.list_display)
        self.assertIn('usuario__email', admin_modelo.search_fields)


class ValidacionesContratistasTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='acme',
        )

    def _configuracion(self, **overrides):
        datos = {
            'organization': self.organizacion,
            'product_type': ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            'min_amount': Decimal('100000.00'),
            'max_amount': Decimal('5000000.00'),
            'min_term_months': 3,
            'max_term_months': 24,
            'monthly_rate': Decimal('2.5000'),
            'commission_rate': Decimal('5.0000'),
            'commission_amount': Decimal('100000.00'),
            'vat_rate': Decimal('19.0000'),
        }
        datos.update(overrides)
        return ContractorProductConfig(**datos)

    def test_organizacion_no_permite_subdominio_vacio(self):
        organizacion = ContractorOrganization(
            name='Sin Subdominio',
            slug='sin-subdominio',
            subdomain='   ',
        )

        with self.assertRaises(ValidationError) as contexto:
            organizacion.full_clean()

        self.assertIn('subdomain', contexto.exception.message_dict)

    def test_organizacion_normaliza_subdominio(self):
        organizacion = ContractorOrganization(
            name='Nueva Organizacion',
            slug='nueva',
            subdomain=' NUEVA ',
        )

        organizacion.full_clean()

        self.assertEqual(organizacion.subdomain, 'nueva')

    def test_no_permite_min_amount_mayor_que_max_amount(self):
        configuracion = self._configuracion(
            min_amount=Decimal('6000000.00'),
            max_amount=Decimal('5000000.00'),
        )

        with self.assertRaises(ValidationError) as contexto:
            configuracion.full_clean()

        self.assertIn('min_amount', contexto.exception.message_dict)

    def test_no_permite_min_term_mayor_que_max_term(self):
        configuracion = self._configuracion(min_term_months=25, max_term_months=24)

        with self.assertRaises(ValidationError) as contexto:
            configuracion.full_clean()

        self.assertIn('min_term_months', contexto.exception.message_dict)

    def test_no_permite_tasa_negativa(self):
        configuracion = self._configuracion(monthly_rate=Decimal('-0.0100'))

        with self.assertRaises(ValidationError) as contexto:
            configuracion.full_clean()

        self.assertIn('monthly_rate', contexto.exception.message_dict)

    def test_no_permite_comision_porcentual_negativa(self):
        configuracion = self._configuracion(commission_rate=Decimal('-0.0100'))

        with self.assertRaises(ValidationError) as contexto:
            configuracion.full_clean()

        self.assertIn('commission_rate', contexto.exception.message_dict)

    def test_no_permite_comision_fija_negativa(self):
        configuracion = self._configuracion(commission_amount=Decimal('-1.00'))

        with self.assertRaises(ValidationError) as contexto:
            configuracion.full_clean()

        self.assertIn('commission_amount', contexto.exception.message_dict)

    def test_configuracion_valida_pasa_full_clean(self):
        configuracion = self._configuracion()

        configuracion.full_clean()

        self.assertEqual(configuracion.min_amount, Decimal('100000.00'))

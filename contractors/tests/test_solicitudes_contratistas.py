from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from contractors.models import ContractorApplication, ContractorOrganization, ContractorProductConfig
from contractors.selectors import obtener_solicitud_contratista, listar_solicitudes_por_organizacion
from gestion_creditos.models import Credito, CreditoLibranza


class SolicitudesContratistasTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='acme',
        )
        self.otra_organizacion = ContractorOrganization.objects.create(
            name='Beta Contractors',
            slug='beta',
            subdomain='beta',
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('5000000.00'),
            min_term_months=3,
            max_term_months=24,
            monthly_rate=Decimal('2.5000'),
            commission_rate=Decimal('5.0000'),
            commission_amount=Decimal('100000.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.otra_configuracion = ContractorProductConfig.objects.create(
            organization=self.otra_organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('50000.00'),
            max_amount=Decimal('2000000.00'),
            min_term_months=1,
            max_term_months=12,
            monthly_rate=Decimal('7.0000'),
            commission_rate=Decimal('1.0000'),
            commission_amount=Decimal('0.00'),
            vat_rate=Decimal('19.0000'),
        )

    def _solicitud(self, **overrides):
        datos = {
            'organization': self.organizacion,
            'product_config': self.configuracion,
            'status': ContractorApplication.Estado.RECIBIDA,
            'requested_amount': Decimal('1000000.00'),
            'term_months': 12,
            'estimated_monthly_payment': Decimal('120000.00'),
            'simulation_payload': {
                'monto_solicitado': '1000000.00',
                'plazo_meses': 12,
            },
            'document_type': 'CC',
            'document_number': '123456789',
            'first_name': 'Ana',
            'last_name': 'Perez',
            'phone': '3001234567',
            'email': 'ana@example.com',
            'address': 'Calle 1 # 2-3',
            'accepted_terms': True,
            'source_subdomain': 'acme',
            'ip_address': '127.0.0.1',
            'user_agent': 'Prueba',
        }
        datos.update(overrides)
        return ContractorApplication(**datos)

    def test_crea_pre_solicitud_valida(self):
        creditos_antes = Credito.objects.count()
        detalles_antes = CreditoLibranza.objects.count()
        solicitud = self._solicitud()

        solicitud.full_clean()
        solicitud.save()

        self.assertEqual(solicitud.status, ContractorApplication.Estado.RECIBIDA)
        self.assertEqual(solicitud.organization, self.organizacion)
        self.assertEqual(solicitud.product_config, self.configuracion)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), detalles_antes)

    def test_no_permite_terminos_no_aceptados(self):
        solicitud = self._solicitud(accepted_terms=False)

        with self.assertRaises(ValidationError) as contexto:
            solicitud.full_clean()

        self.assertIn('accepted_terms', contexto.exception.message_dict)

    def test_no_permite_configuracion_de_otra_organizacion(self):
        solicitud = self._solicitud(product_config=self.otra_configuracion)

        with self.assertRaises(ValidationError) as contexto:
            solicitud.full_clean()

        self.assertIn('product_config', contexto.exception.message_dict)

    def test_no_permite_credito_vinculado_si_no_esta_convertida(self):
        solicitud = self._solicitud()
        solicitud.credito_id = 999

        with self.assertRaises(ValidationError) as contexto:
            solicitud.clean()

        self.assertIn('credito', contexto.exception.message_dict)

    def test_permite_credito_vinculado_si_esta_convertida(self):
        solicitud = self._solicitud(status=ContractorApplication.Estado.CONVERTIDA)
        solicitud.credito_id = 999

        solicitud.clean()

        self.assertEqual(solicitud.status, ContractorApplication.Estado.CONVERTIDA)

    def test_no_permite_organizacion_inactiva(self):
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])
        solicitud = self._solicitud()

        with self.assertRaises(ValidationError) as contexto:
            solicitud.full_clean()

        self.assertIn('organization', contexto.exception.message_dict)

    def test_lista_solicitudes_solo_de_su_organizacion(self):
        solicitud_acme = self._solicitud(document_number='111')
        solicitud_acme.full_clean()
        solicitud_acme.save()

        solicitud_beta = self._solicitud(
            organization=self.otra_organizacion,
            product_config=self.otra_configuracion,
            document_number='222',
            source_subdomain='beta',
        )
        solicitud_beta.full_clean()
        solicitud_beta.save()

        solicitudes = list(listar_solicitudes_por_organizacion(self.organizacion))

        self.assertEqual(solicitudes, [solicitud_acme])
        self.assertNotIn(solicitud_beta, solicitudes)

    def test_obtener_solicitud_filtra_por_organizacion(self):
        solicitud = self._solicitud()
        solicitud.full_clean()
        solicitud.save()

        self.assertEqual(obtener_solicitud_contratista(solicitud.id, self.organizacion), solicitud)
        self.assertIsNone(obtener_solicitud_contratista(solicitud.id, self.otra_organizacion))

    def test_pre_solicitud_no_crea_credito_ni_credito_libranza(self):
        creditos_antes = Credito.objects.count()
        detalles_antes = CreditoLibranza.objects.count()
        solicitud = self._solicitud()

        solicitud.full_clean()
        solicitud.save()

        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), detalles_antes)

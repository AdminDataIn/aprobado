from decimal import Decimal

from django.test import TestCase

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
)
from contractors.services.elegibilidad_conversion import (
    TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION,
    evaluar_elegibilidad_conversion_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago, Pagare


class ElegibilidadConversionContratistaTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='acme',
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
        self.solicitud = self._crear_solicitud()

    def _crear_solicitud(self, estado=ContractorApplication.Estado.EN_REVISION, **overrides):
        datos = {
            'organization': self.organizacion,
            'product_config': self.configuracion,
            'status': estado,
            'requested_amount': Decimal('1000000.00'),
            'term_months': 12,
            'estimated_monthly_payment': Decimal('120000.00'),
            'simulation_payload': {'cuota_mensual': '120000.00'},
            'document_type': 'CC',
            'document_number': '123456789',
            'first_name': 'Ana',
            'last_name': 'Perez',
            'phone': '3001234567',
            'email': 'ana@example.com',
            'address': 'Calle 1 # 2-3',
            'accepted_terms': True,
            'source_subdomain': 'acme',
        }
        datos.update(overrides)
        return ContractorApplication.objects.create(**datos)

    def _documento(self, tipo_documento, estado=ContractorApplicationDocument.Estado.APROBADO):
        return ContractorApplicationDocument.objects.create(
            application=self.solicitud,
            document_type=tipo_documento,
            file=f'contractors/applications/documents/{tipo_documento}.pdf',
            original_filename=f'{tipo_documento}.pdf',
            content_type='application/pdf',
            file_size=100,
            status=estado,
        )

    def _documentos_minimos_aprobados(self, omitir=None):
        omitir = set(omitir or [])
        for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
            if tipo_documento not in omitir:
                self._documento(tipo_documento)

    def test_elegible_con_solicitud_en_revision_y_documentos_minimos_aprobados(self):
        self._documentos_minimos_aprobados()

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertTrue(resultado.elegible)
        self.assertTrue(resultado.eligible)
        self.assertEqual(resultado.razon, 'elegible')
        self.assertEqual(resultado.reason, 'elegible')
        self.assertEqual(resultado.solicitud_id, self.solicitud.id)
        self.assertEqual(resultado.application_id, self.solicitud.id)
        self.assertEqual(resultado.razones, ())
        self.assertEqual(resultado.missing_documents, ())
        self.assertEqual(resultado.rejected_documents, ())
        self.assertEqual(resultado.como_dict()['eligible'], True)

    def test_no_elegible_si_esta_recibida(self):
        self.solicitud.status = ContractorApplication.Estado.RECIBIDA
        self.solicitud.save(update_fields=['status'])
        self._documentos_minimos_aprobados()

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('solicitud_no_esta_en_revision', resultado.razones)

    def test_no_elegible_si_esta_rechazada(self):
        self.solicitud.status = ContractorApplication.Estado.RECHAZADA
        self.solicitud.save(update_fields=['status'])
        self._documentos_minimos_aprobados()

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('solicitud_rechazada', resultado.razones)

    def test_no_elegible_si_esta_convertida(self):
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])
        self._documentos_minimos_aprobados()

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('solicitud_convertida', resultado.razones)

    def test_no_elegible_si_falta_documento(self):
        tipo_faltante = ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO
        self._documentos_minimos_aprobados(omitir={tipo_faltante})

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn(tipo_faltante, resultado.documentos_faltantes)
        self.assertIn(f'documento_faltante:{tipo_faltante}', resultado.razones)

    def test_no_elegible_si_documento_esta_rechazado(self):
        tipo_rechazado = ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL
        self._documentos_minimos_aprobados()
        self._documento(tipo_rechazado, estado=ContractorApplicationDocument.Estado.RECHAZADO)

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn(tipo_rechazado, resultado.documentos_rechazados)
        self.assertIn(f'documento_rechazado:{tipo_rechazado}', resultado.razones)

    def test_no_elegible_si_organizacion_inactiva(self):
        self._documentos_minimos_aprobados()
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])
        self.solicitud = ContractorApplication.objects.select_related('organization', 'product_config').get(
            id=self.solicitud.id,
        )

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('organizacion_inactiva', resultado.razones)

    def test_no_elegible_si_configuracion_inactiva(self):
        self._documentos_minimos_aprobados()
        self.configuracion.is_active = False
        self.configuracion.save(update_fields=['is_active'])
        self.solicitud = ContractorApplication.objects.select_related('organization', 'product_config').get(
            id=self.solicitud.id,
        )

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('configuracion_producto_inactiva', resultado.razones)

    def test_no_elegible_si_monto_ya_no_cumple_configuracion(self):
        self._documentos_minimos_aprobados()
        self.configuracion.max_amount = Decimal('900000.00')
        self.configuracion.save(update_fields=['max_amount'])
        self.solicitud = ContractorApplication.objects.select_related('organization', 'product_config').get(
            id=self.solicitud.id,
        )

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('monto_fuera_de_configuracion_vigente', resultado.razones)

    def test_no_elegible_si_plazo_ya_no_cumple_configuracion(self):
        self._documentos_minimos_aprobados()
        self.configuracion.max_term_months = 10
        self.configuracion.save(update_fields=['max_term_months'])
        self.solicitud = ContractorApplication.objects.select_related('organization', 'product_config').get(
            id=self.solicitud.id,
        )

        resultado = evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('plazo_fuera_de_configuracion_vigente', resultado.razones)

    def test_no_crea_credito_ni_flujos_productivos(self):
        self._documentos_minimos_aprobados()
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        evaluar_elegibilidad_conversion_contratista(self.solicitud)

        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])

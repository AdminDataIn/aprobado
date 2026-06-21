from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from contractors.datacredito import EntradaConsultaDatacreditoPrestador, consultar_datacredito_prestador
from contractors.datacredito.dto import (
    ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY,
    ESTADO_DATACREDITO_DISPONIBLE,
    ESTADO_DATACREDITO_PENDIENTE,
    FUENTE_DATACREDITO_REAL,
    FUENTE_MOCK,
    FUENTE_NO_CONFIGURADO,
)
from contractors.datacredito.mock import consultar_datacredito_mock
from contractors.datacredito.normalizador import enmascarar_documento, hash_documento
from integrations.datacredito.dto import ResultadoHistorialCreditoRawSeguro, ResultadoMiDecisorRawSeguro
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
    InformacionLaboralSolicitudContratista,
)
from contractors.services.elegibilidad_conversion import TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION
from contractors.services.predecision import evaluar_predecision_contratista
from contractors.services.predecision import DECISION_REQUIERE_REVISION_MANUAL, DECISION_PREAPROBADO_READ_ONLY
from gestion_creditos.models import Credito, CreditoLibranza
from gestion_creditos.models import Empresa


class AdapterDatacreditoPrestadoresTests(SimpleTestCase):
    def _entrada(self):
        return EntradaConsultaDatacreditoPrestador(
            solicitud_id=10,
            tipo_documento='CC',
            numero_documento='1234567890',
        )

    @override_settings(CONTRACTORS_DATACREDITO_ENABLED=False)
    def test_adapter_no_configurado_devuelve_no_configurado(self):
        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.fuente, FUENTE_NO_CONFIGURADO)
        self.assertIsNone(resultado.score_normalizado_0_1000)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertEqual(resultado.status, ESTADO_DATACREDITO_PENDIENTE)

    @override_settings(CONTRACTORS_DATACREDITO_ENABLED=True, CONTRACTORS_DATACREDITO_PROVIDER='mock')
    def test_mock_bueno_devuelve_score_alto(self):
        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.fuente, FUENTE_MOCK)
        self.assertEqual(resultado.score_normalizado_0_1000, 880)
        self.assertEqual(resultado.nivel_riesgo, 'BAJO')
        self.assertEqual(resultado.status, ESTADO_DATACREDITO_DISPONIBLE)

    def test_mock_medio_devuelve_score_medio(self):
        resultado = consultar_datacredito_mock(self._entrada(), escenario='medio')

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.score_normalizado_0_1000, 690)
        self.assertEqual(resultado.nivel_riesgo, 'MEDIO')
        self.assertTrue(resultado.requiere_revision_manual)

    def test_mock_mora_severa_marca_bloqueo(self):
        resultado = consultar_datacredito_mock(self._entrada(), escenario='mora_severa')

        self.assertTrue(resultado.disponible)
        self.assertTrue(resultado.mora_severa)
        self.assertTrue(resultado.mora_actual)
        self.assertEqual(resultado.status, ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY)
        self.assertEqual(resultado.alertas[0].codigo, 'mora_severa')

    def test_mock_no_disponible_no_entrega_score(self):
        resultado = consultar_datacredito_mock(self._entrada(), escenario='no_disponible')

        self.assertFalse(resultado.disponible)
        self.assertIsNone(resultado.score_normalizado_0_1000)
        self.assertEqual(resultado.error_tipo, 'mock_no_disponible')

    def test_metadata_no_guarda_documento_completo_ni_respuesta_cruda(self):
        resultado = consultar_datacredito_mock(self._entrada(), escenario='bueno')
        serializado = str(resultado.como_dict())

        self.assertNotIn('1234567890', serializado)
        self.assertNotIn('xml', serializado.lower())
        self.assertNotIn('respuesta_cruda', serializado.lower())
        self.assertEqual(resultado.metadata_segura['documento_enmascarado'], '******7890')
        self.assertEqual(resultado.metadata_segura['documento_hash'], hash_documento('CC', '1234567890'))
        self.assertEqual(enmascarar_documento('1234567890'), '******7890')

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='real',
        DATACREDITO_REAL_ENABLED=False,
    )
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    def test_provider_real_apagado_no_consume(self, historial_mock, decisor_mock):
        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.fuente, FUENTE_NO_CONFIGURADO)
        self.assertEqual(resultado.error_tipo, 'datacredito_real_deshabilitado')
        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='real',
        DATACREDITO_REAL_ENABLED=True,
    )
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    def test_provider_real_consolida_y_prioriza_score_decisor(self, decisor_mock, historial_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={
                'responseCode': '13',
                'informacionRiesgo': {'score': 820},
                'viabilidad': 'APROBADO',
                'montoSugerido': 7000000,
                'saldoMora': 0,
                'valorCuotaTotal': 400000,
                'porcentajeCuotaVsIngreso': '18.5',
                'ingresoEstimado': 2500000,
                'ratingRecaudos': 'A',
            },
        )
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'scoreCrediticio': 650},
        )

        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.fuente, FUENTE_DATACREDITO_REAL)
        self.assertEqual(resultado.score_externo, 820)
        self.assertEqual(resultado.score_normalizado_0_1000, 820)
        self.assertEqual(resultado.monto_sugerido_datacredito, 7000000)
        self.assertEqual(resultado.valor_cuota_total, 400000)
        self.assertEqual(resultado.ingreso_estimado, 2500000)
        self.assertTrue(resultado.viabilidad)
        self.assertFalse(resultado.mora_severa)
        self.assertNotIn('1234567890', str(resultado.como_dict()))
        self.assertNotIn('raw_sanitizado', str(resultado.como_dict()))

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='real',
        DATACREDITO_REAL_ENABLED=True,
    )
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    def test_provider_real_no_usa_score_historial_como_score_principal(self, decisor_mock, historial_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13'},
        )
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'scoreCrediticio': 715},
        )

        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertIsNone(resultado.score_externo)
        self.assertIsNone(resultado.score_normalizado_0_1000)
        self.assertIsNone(resultado.metadata_segura['score_fuente'])
        self.assertTrue(resultado.requiere_revision_manual)

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='real',
        DATACREDITO_REAL_ENABLED=True,
    )
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    def test_mora_severa_decisor_bloquea_read_only(self, decisor_mock, historial_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={
                'responseCode': '13',
                'score': 700,
                'comportamientoPago': {'vectorComportamiento': 'NN3'},
            },
        )
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13'},
        )

        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertTrue(resultado.mora_severa)
        self.assertEqual(resultado.status, ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY)

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='real',
        DATACREDITO_REAL_ENABLED=True,
    )
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    def test_mora_severa_historial_bloquea_read_only_y_saldo_mora_marca_mora_actual(self, decisor_mock, historial_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'score': 700, 'saldoMora': 100000},
        )
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'comportamientoPago': 'N4N'},
        )

        resultado = consultar_datacredito_prestador(self._entrada())

        self.assertTrue(resultado.mora_severa)
        self.assertTrue(resultado.mora_actual)
        self.assertEqual(resultado.saldo_mora, 100000)


class PredecisionDatacreditoPrestadoresTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.organizacion = ContractorOrganization.objects.create(
            name='Portal Contratistas',
            slug='contratistas',
            subdomain='contratistas',
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('10000000.00'),
            min_term_months=1,
            max_term_months=24,
            monthly_rate=Decimal('2.00'),
            commission_rate=Decimal('5.00'),
            vat_rate=Decimal('19.00'),
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagadora Test',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            product_config=self.configuracion,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('1000000.00'),
            term_months=6,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number='123456789',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )

    def _documentos_minimos_aprobados(self):
        for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
            ContractorApplicationDocument.objects.create(
                application=self.solicitud,
                document_type=tipo_documento,
                file=f'contractors/applications/documents/{tipo_documento}.pdf',
                original_filename=f'{tipo_documento}.pdf',
                content_type='application/pdf',
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )

    def _datos_contractuales(self):
        return InformacionLaboralSolicitudContratista.objects.create(
            solicitud=self.solicitud,
            cargo='Contratista comercial',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=self.hoy - relativedelta(months=1),
            fecha_fin_contrato=self.hoy + relativedelta(months=8),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('4000000.00'),
            valor_pendiente_cobrar=Decimal('8000000.00'),
            empresa=self.empresa,
            empresa_contratante_nombre='Empresa Pagadora Test',
            pagador_nombre='Pagador Principal',
            pagador_email='pagador@example.com',
            pagador_telefono='3007654321',
        )

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='bueno',
    )
    def test_predecision_incluye_datacredito_y_score_usa_score_disponible(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        estado_inicial = self.solicitud.status
        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertTrue(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_PREAPROBADO_READ_ONLY)
        self.assertEqual(resultado.datacredito_resultado['status'], ESTADO_DATACREDITO_DISPONIBLE)
        self.assertEqual(resultado.datacredito_resultado['score_normalizado_0_1000'], 880)
        self.assertNotIn('datacredito', resultado.score_resultado['componentes_pendientes'])
        componentes = {
            componente['nombre']: componente
            for componente in resultado.score_resultado['componentes']
        }
        self.assertEqual(componentes['datacredito']['valor'], '880.00')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_inicial)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='no_disponible',
    )
    def test_score_queda_pendiente_si_datacredito_no_disponible(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertEqual(resultado.datacredito_resultado['status'], ESTADO_DATACREDITO_PENDIENTE)
        self.assertIn('datacredito', resultado.score_resultado['componentes_pendientes'])

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='mora_severa',
    )
    def test_mora_severa_bloquea_predecision_read_only(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        estado_inicial = self.solicitud.status
        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('datacredito:mora_severa', resultado.razones)
        self.assertEqual(resultado.datacredito_status, ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY)
        self.assertEqual(resultado.score_resultado, {})
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_inicial)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

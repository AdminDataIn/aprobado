import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from integrations.datacredito.dto import EntradaHistorialCredito, TokenDatacredito
from integrations.datacredito.exceptions import DatacreditoProviderError
from integrations.datacredito.historial_client import consultar_historial_credito
from integrations.datacredito.normalizadores import normalizar_historial_credito


CONFIGURACION_HDC = {
    'DATACREDITO_ENABLED': True,
    'DATACREDITO_REAL_ENABLED': True,
    'DATACREDITO_ENVIRONMENT': 'uat',
    'DATACREDITO_HDC_CLIENT_ID': 'cliente-prueba',
    'DATACREDITO_HDC_CLIENT_SECRET': 'secreto-prueba',
    'DATACREDITO_HDC_TOKEN_USERNAME': 'usuario-prueba',
    'DATACREDITO_HDC_TOKEN_PASSWORD': 'password-prueba',
    'DATACREDITO_HDC_SERVICE_USER': 'servicio-prueba',
    'DATACREDITO_HDC_SERVICE_PASSWORD': 'servicio-password-prueba',
    'DATACREDITO_HDC_PRODUCT_ID': '64',
    'DATACREDITO_HDC_INFO_ACCOUNT_TYPE': '1',
    'DATACREDITO_HDC_SERVER_IP_ADDRESS': '192.0.2.30',
}


class HDCPlusNormalizacionDualTest(SimpleTestCase):
    def test_normalizador_proyecta_obligaciones_cuota_saldo_y_mora(self):
        resultado = normalizar_historial_credito(self._payload_hdc())

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.creditos_vigentes, 2)
        self.assertEqual(str(resultado.saldo_actual), '3500000')
        self.assertEqual(str(resultado.valor_cuota_total), '450000')
        self.assertEqual(str(resultado.saldo_mora), '0')
        self.assertFalse(resultado.mora_actual)
        self.assertFalse(resultado.mora_severa)
        self.assertEqual(
            resultado.metadata_segura['hdc_resumen']['huellas_ultimos_6_meses'],
            1,
        )

        allowlist = _proyectar_resultado_allowlist(resultado, servicio='historial')
        self.assertEqual(allowlist.obligaciones_vigentes, 2)
        self.assertEqual(allowlist.cuota_mensual_total, '450000')
        self.assertEqual(allowlist.saldo_total, '3500000')
        self.assertEqual(allowlist.consultas_recientes, 1)

    def test_mora_90_dias_es_severa(self):
        payload = self._payload_hdc(mora=100000, dias=90)
        resultado = normalizar_historial_credito(payload)

        self.assertTrue(resultado.mora_actual)
        self.assertTrue(resultado.mora_severa)
        self.assertIn('mora_severa_detectada', resultado.alertas_resumen)

    @override_settings(**CONFIGURACION_HDC)
    @patch('integrations.datacredito.historial_client.crear_session_datacredito')
    @patch('integrations.datacredito.historial_client.obtener_token_cacheado')
    def test_cliente_detecta_codigo_funcional_anidado_y_sanitiza_pii(
        self, obtener_token, crear_session
    ):
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')
        respuesta = MagicMock(status_code=200)
        payload = self._payload_hdc()
        payload['ReportHDCplus']['identifyingAttributes'] = {
            'personIdNumber': 'documento-real-no-persistir',
            'fullName': 'nombre-real-no-persistir',
        }
        respuesta.json.return_value = payload
        session = MagicMock()
        session.post.return_value = respuesta
        crear_session.return_value = session

        resultado = consultar_historial_credito(
            EntradaHistorialCredito(
                tipo_identificacion='CC',
                numero_identificacion='123456789',
                apellido='PRUEBA',
            )
        )

        serializado = json.dumps(resultado.raw_sanitizado)
        self.assertEqual(resultado.response_code, '13')
        self.assertNotIn('documento-real-no-persistir', serializado)
        self.assertNotIn('nombre-real-no-persistir', serializado)
        self.assertNotIn('token-prueba', serializado)

    @override_settings(**CONFIGURACION_HDC)
    @patch('integrations.datacredito.historial_client.crear_session_datacredito')
    @patch('integrations.datacredito.historial_client.obtener_token_cacheado')
    def test_http_4xx_conserva_status_para_clasificacion_permanente(
        self, obtener_token, crear_session
    ):
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=400)
        crear_session.return_value = session

        with self.assertRaises(DatacreditoProviderError) as capturado:
            consultar_historial_credito(self._entrada())

        self.assertEqual(capturado.exception.http_status, 400)
        self.assertEqual(capturado.exception.error_tipo, 'HTTP_PROVEEDOR')

    @override_settings(**CONFIGURACION_HDC)
    @patch('integrations.datacredito.historial_client.crear_session_datacredito')
    @patch('integrations.datacredito.historial_client.obtener_token_cacheado')
    def test_http_5xx_conserva_status_para_clasificacion_transitoria(
        self, obtener_token, crear_session
    ):
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=503)
        crear_session.return_value = session

        with self.assertRaises(DatacreditoProviderError) as capturado:
            consultar_historial_credito(self._entrada())

        self.assertEqual(capturado.exception.http_status, 503)

    @staticmethod
    def _entrada():
        return EntradaHistorialCredito(
            tipo_identificacion='CC',
            numero_identificacion='123456789',
            apellido='PRUEBA',
        )

    @staticmethod
    def _payload_hdc(*, mora=0, dias=0):
        return {
            'ReportHDCplus': {
                'productResult': {'responseCode': '13', 'responseDesc': 'OK'},
                'liabilities': [
                    {
                        'account': {'accountTypeDesc': 'Credito'},
                        'status': {
                            'account': {'businessAccountStatusDesc': 'AL DIA'},
                            'payment': {'businessBureauEventDesc': 'AL DIA'},
                        },
                        'values': [{
                            'debtBalance': 2000000,
                            'businessValueBalanceOverdue': mora,
                            'valueMonthlyPayment': 250000,
                            'installmentsOverdue': 1 if mora else 0,
                            'delinquencyMaturation': dias,
                        }],
                    },
                    {
                        'account': {'accountTypeDesc': 'Tarjeta'},
                        'status': {
                            'account': {'businessAccountStatusDesc': 'AL DIA'},
                            'payment': {'businessBureauEventDesc': 'AL DIA'},
                        },
                        'values': [{
                            'debtBalance': 1500000,
                            'businessValueBalanceOverdue': 0,
                            'valueMonthlyPayment': 200000,
                            'installmentsOverdue': 0,
                            'delinquencyMaturation': 0,
                        }],
                    },
                ],
                'inquiryFootprints': [
                    {'inquiryDate': '2026-07-01', 'economicSectorName': 'FINANCIERO'},
                ],
            }
        }

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from integrations.datacredito.auth import generar_token, revocar_token
from integrations.datacredito.decisor_client import consultar_midecisor_persona_natural
from integrations.datacredito.dto import (
    EntradaHistorialCredito,
    EntradaMiDecisor,
    TokenDatacredito,
)
from integrations.datacredito.historial_client import consultar_historial_credito
from integrations.datacredito.http import crear_session_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito


CONFIGURACION_HTTP_PRUEBA = {
    'DATACREDITO_ENABLED': True,
    'DATACREDITO_REAL_ENABLED': True,
    'DATACREDITO_ENVIRONMENT': 'uat',
    'DATACREDITO_DECISOR_CLIENT_ID': 'decisor-client-prueba',
    'DATACREDITO_DECISOR_CLIENT_SECRET': 'decisor-secret-prueba',
    'DATACREDITO_DECISOR_TOKEN_USERNAME': 'decisor-usuario-prueba',
    'DATACREDITO_DECISOR_TOKEN_PASSWORD': 'decisor-password-prueba',
    'DATACREDITO_HDC_CLIENT_ID': 'hdc-client-prueba',
    'DATACREDITO_HDC_CLIENT_SECRET': 'hdc-secret-prueba',
    'DATACREDITO_HDC_TOKEN_USERNAME': 'hdc-usuario-prueba',
    'DATACREDITO_HDC_TOKEN_PASSWORD': 'hdc-password-prueba',
    'DATACREDITO_HDC_SERVICE_USER': 'hdc-service-user-prueba',
    'DATACREDITO_HDC_SERVICE_PASSWORD': 'hdc-service-password-prueba',
    'DATACREDITO_HDC_PRODUCT_ID': '64',
    'DATACREDITO_HDC_INFO_ACCOUNT_TYPE': '1',
    'DATACREDITO_HDC_SERVER_IP_ADDRESS': '192.0.2.30',
}


@override_settings(**CONFIGURACION_HTTP_PRUEBA)
class DatacreditoProxyTest(SimpleTestCase):
    @override_settings(DATACREDITO_PROXY_URL='')
    def test_proxy_vacio_crea_session_directa(self):
        configuracion = obtener_configuracion_datacredito()
        session = crear_session_datacredito(configuracion)

        self.assertEqual(configuracion.proxy_url, '')
        self.assertEqual(session.proxies, {})
        self.assertIs(session.verify, True)

    @override_settings(DATACREDITO_PROXY_URL='socks5h://127.0.0.1:1080')
    def test_proxy_configurado_aplica_a_http_y_https_sin_desactivar_tls(self):
        session = crear_session_datacredito()

        self.assertEqual(
            session.proxies,
            {
                'http': 'socks5h://127.0.0.1:1080',
                'https': 'socks5h://127.0.0.1:1080',
            },
        )
        self.assertIs(session.verify, True)

    @override_settings(DATACREDITO_PROXY_URL='socks5h://usuario:clave@127.0.0.1:1080')
    def test_proxy_url_no_aparece_en_logs(self):
        configuracion = obtener_configuracion_datacredito()
        with self.assertLogs('integrations.datacredito.http', level='INFO') as capturados:
            crear_session_datacredito(configuracion)

        logs = '\n'.join(capturados.output)
        self.assertIn('proxy_configured=True', logs)
        self.assertNotIn('socks5h://', logs)
        self.assertNotIn('usuario', logs)
        self.assertNotIn('clave', logs)
        self.assertNotIn('socks5h://', repr(configuracion))
        self.assertNotIn('usuario:clave', repr(configuracion))

    @patch('integrations.datacredito.decisor_client.crear_session_datacredito')
    @patch('integrations.datacredito.decisor_client.obtener_token_cacheado')
    def test_midecisor_usa_misma_session_para_oauth_y_consulta(self, obtener_token, crear_session):
        session = self._session_con_respuesta({'content': {}})
        crear_session.return_value = session
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')

        resultado = consultar_midecisor_persona_natural(
            EntradaMiDecisor(
                tipo_identificacion='CC',
                numero_identificacion='123456789',
                apellido_razon_social='PRUEBA',
            )
        )

        crear_session.assert_called_once()
        obtener_token.assert_called_once_with(servicio='decisor', session=session)
        session.post.assert_called_once()
        self.assertNotIn('verify', session.post.call_args.kwargs)
        self.assertEqual(resultado.status_code, 200)

    @patch('integrations.datacredito.historial_client.crear_session_datacredito')
    @patch('integrations.datacredito.historial_client.obtener_token_cacheado')
    def test_hdcplus_usa_misma_session_para_oauth_y_consulta(self, obtener_token, crear_session):
        session = self._session_con_respuesta({'responseCode': '13'})
        crear_session.return_value = session
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')

        resultado = consultar_historial_credito(
            EntradaHistorialCredito(
                tipo_identificacion='CC',
                numero_identificacion='123456789',
                apellido='PRUEBA',
            )
        )

        crear_session.assert_called_once()
        obtener_token.assert_called_once_with(servicio='historial', session=session)
        session.post.assert_called_once()
        self.assertNotIn('verify', session.post.call_args.kwargs)
        self.assertEqual(resultado.response_code, '13')

    @patch('integrations.datacredito.auth.crear_session_datacredito')
    def test_oauth_usa_session_configurada(self, crear_session):
        session = self._session_con_respuesta(
            {
                'access_token': 'token-prueba',
                'token_type': 'Bearer',
                'expires_in': 300,
            }
        )
        crear_session.return_value = session

        token = generar_token(servicio='decisor')

        crear_session.assert_called_once()
        session.post.assert_called_once()
        self.assertNotIn('verify', session.post.call_args.kwargs)
        self.assertEqual(token.access_token, 'token-prueba')

    @patch('integrations.datacredito.auth.crear_session_datacredito')
    def test_revoke_usa_session_configurada(self, crear_session):
        session = self._session_con_respuesta({})
        crear_session.return_value = session

        resultado = revocar_token(
            token=TokenDatacredito(access_token='token-prueba'),
            servicio='decisor',
        )

        crear_session.assert_called_once()
        session.post.assert_called_once()
        self.assertNotIn('verify', session.post.call_args.kwargs)
        self.assertTrue(resultado['revocado'])

    @override_settings(DATACREDITO_PROXY_URL='socks5h://usuario:clave@127.0.0.1:1080')
    @patch('integrations.datacredito.decisor_client.crear_session_datacredito')
    @patch('integrations.datacredito.decisor_client.obtener_token_cacheado')
    def test_resultado_sanitizado_no_contiene_proxy(self, obtener_token, crear_session):
        session = self._session_con_respuesta({'content': {}})
        crear_session.return_value = session
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')

        resultado = consultar_midecisor_persona_natural(
            EntradaMiDecisor(
                tipo_identificacion='CC',
                numero_identificacion='123456789',
                apellido_razon_social='PRUEBA',
            )
        )
        serializado = json.dumps(asdict(resultado), sort_keys=True)

        self.assertNotIn('proxy', serializado.lower())
        self.assertNotIn('socks5h://', serializado)
        self.assertNotIn('usuario:clave', serializado)

    @patch('integrations.datacredito.decisor_client.crear_session_datacredito')
    @patch('integrations.datacredito.decisor_client.obtener_token_cacheado')
    def test_session_inyectada_no_es_reemplazada(self, obtener_token, crear_session):
        session = self._session_con_respuesta({'content': {}})
        obtener_token.return_value = TokenDatacredito(access_token='token-prueba')

        consultar_midecisor_persona_natural(
            EntradaMiDecisor(
                tipo_identificacion='CC',
                numero_identificacion='123456789',
                apellido_razon_social='PRUEBA',
            ),
            session=session,
        )

        crear_session.assert_not_called()
        obtener_token.assert_called_once_with(servicio='decisor', session=session)
        session.post.assert_called_once()

    @staticmethod
    def _session_con_respuesta(payload):
        respuesta = MagicMock()
        respuesta.status_code = 200
        respuesta.json.return_value = payload
        session = MagicMock()
        session.post.return_value = respuesta
        return session

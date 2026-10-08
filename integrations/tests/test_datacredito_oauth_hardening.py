from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from integrations.datacredito import auth
from integrations.datacredito.decisor_client import consultar_midecisor_persona_natural
from integrations.datacredito.historial_client import consultar_historial_credito
from integrations.datacredito.dto import EntradaMiDecisor, EntradaHistorialCredito, TokenDatacredito
from integrations.datacredito.exceptions import DatacreditoAuthError, DatacreditoProviderDisabled, DatacreditoProviderError, DatacreditoTimeoutError
from integrations.tests import test_datacredito_proxy as fixtures


@override_settings(**fixtures.CONFIGURACION_HTTP_PRUEBA)
class OAuthHardeningTest(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_cache_ambiente_servicio_credenciales_separados(self):
        claves = set()
        with patch.object(auth, 'generar_token', return_value=TokenDatacredito('token-test', expires_in=300)) as generar:
            for ambiente in ('uat', 'prod'):
                with override_settings(DATACREDITO_ENVIRONMENT=ambiente):
                    for servicio in ('decisor', 'historial'):
                        claves.add(auth.clave_cache_token(servicio))
                        auth.obtener_token_cacheado(servicio)
                        auth.obtener_token_cacheado(servicio)
        self.assertEqual(len(claves), 4)
        self.assertEqual(generar.call_count, 4)
        for clave in claves:
            self.assertNotIn('secret-prueba', clave)
            self.assertNotIn('usuario-prueba', clave)
        primera = auth.clave_cache_token('decisor')
        with override_settings(DATACREDITO_DECISOR_CLIENT_SECRET='otra-credencial-sintetica'):
            self.assertNotEqual(primera, auth.clave_cache_token('decisor'))

    def test_cache_no_bypassea_flag_deshabilitado(self):
        cache.set(auth.clave_cache_token('decisor'), TokenDatacredito('token-test'))
        with override_settings(DATACREDITO_REAL_ENABLED=False), self.assertRaises(DatacreditoProviderDisabled):
            auth.obtener_token_cacheado()

    def test_401_invalida_token_sin_repetir_consulta_y_429_no_reintenta(self):
        for servicio, status in ((s, h) for s in ('decisor','historial') for h in (401,429)):
            with self.subTest(servicio=servicio, status=status):
                cache.clear()
                token = TokenDatacredito('token-rechazado', expires_in=300)
                cache.set(auth.clave_cache_token(servicio), token)
                http = Mock()
                http.post.return_value.status_code = status
                with self.assertRaises(DatacreditoProviderError) as error:
                    if servicio == 'decisor':
                        consultar_midecisor_persona_natural(EntradaMiDecisor('CC','123456789','PRUEBA'), session=http)
                    else:
                        consultar_historial_credito(EntradaHistorialCredito('CC','123456789','PRUEBA'), session=http)
                self.assertEqual(error.exception.http_status, status)
                http.post.assert_called_once()
                with patch.object(auth, 'generar_token', return_value=TokenDatacredito('renovado', expires_in=300)) as generar:
                    resultado = auth.obtener_token_cacheado(servicio)
                self.assertEqual(generar.call_count, 1 if status == 401 else 0)
                self.assertEqual(resultado.access_token, 'renovado' if status == 401 else 'token-rechazado')

    def test_invalidacion_no_descarta_token_renovado_por_otro_worker(self):
        cache.set(auth.clave_cache_token('decisor'), TokenDatacredito('nuevo', expires_in=300))
        auth.invalidar_token(TokenDatacredito('anterior', expires_in=300), 'decisor')
        with patch.object(auth, 'generar_token') as generar:
            self.assertEqual(auth.obtener_token_cacheado().access_token, 'nuevo')
        generar.assert_not_called()

    def test_timeout_no_repite_consulta(self):
        http = Mock()
        http.post.side_effect = requests.exceptions.Timeout()
        with patch('integrations.datacredito.decisor_client.obtener_token_cacheado', return_value=TokenDatacredito('test')):
            with self.assertRaises(DatacreditoTimeoutError):
                consultar_midecisor_persona_natural(EntradaMiDecisor('CC','123456789','PRUEBA'), session=http)
        http.post.assert_called_once()

    def test_revoke_cumple_swagger_y_no_expone_secretos(self):
        http = Mock()
        http.post.return_value.status_code = 200
        token = TokenDatacredito('token-test', expires_in=300)
        with self.assertLogs('integrations.datacredito.auth', level='INFO') as logs:
            self.assertTrue(auth.revocar_token(token, 'decisor', session=http)['revocado'])
        parametros = http.post.call_args.kwargs
        self.assertEqual(parametros['json'], {'username':'decisor-usuario-prueba', 'password':'decisor-password-prueba'})
        self.assertEqual(parametros['headers']['client_id'], 'decisor-client-prueba')
        self.assertEqual(parametros['headers']['client_secret'], 'decisor-secret-prueba')
        self.assertEqual(parametros['headers']['token'], token.access_token)
        for secreto in ('token-test','decisor-secret-prueba','decisor-password-prueba'):
            self.assertNotIn(secreto, str(logs.output))

    def test_oauth_error_conserva_status_y_token_corto_no_cachea(self):
        http = Mock()
        http.post.return_value.status_code = 401
        with self.assertRaises(DatacreditoAuthError) as error:
            auth.generar_token(session=http)
        self.assertEqual(error.exception.http_status, 401)
        with patch.object(auth, 'generar_token', return_value=TokenDatacredito('corto', expires_in=10)) as generar:
            auth.obtener_token_cacheado()
            auth.obtener_token_cacheado()
        self.assertEqual(generar.call_count, 2)

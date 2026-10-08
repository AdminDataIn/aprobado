import json
import os
import subprocess
import sys
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.conf import settings
from django.test import TestCase, override_settings

from contractors.consentimiento_centrales import TEXTO_CONSENTIMIENTO_CENTRALES, VERSION_CONSENTIMIENTO_CENTRALES
from integrations.datacredito.readiness import evaluar_readiness_datacredito
from integrations.datacredito.settings import secreto_documental_valido
from integrations.datacredito.settings import obtener_configuracion_datacredito
from contractors.services.datacredito_evaluacion import _validar_configuracion


CANONICOS = [
    'DATACREDITO_DECISOR_' + campo
    for campo in ('CLIENT_ID', 'CLIENT_SECRET', 'TOKEN_USERNAME', 'TOKEN_PASSWORD')
] + [
    'DATACREDITO_HDC_' + campo
    for campo in ('CLIENT_ID', 'CLIENT_SECRET', 'TOKEN_USERNAME', 'TOKEN_PASSWORD',
                  'SERVICE_USER', 'SERVICE_PASSWORD', 'SERVER_IP_ADDRESS')
]
CONFIGURACION_FICTICIA = {
    **{nombre: 'test-secret-no-imprimir-' + nombre for nombre in CANONICOS},
    'DATACREDITO_HDC_SERVER_IP_ADDRESS': '192.0.2.20',
    'DATACREDITO_DOCUMENT_HASH_SECRET': 'test-hmac-sintetico-no-imprimir-32-bytes',
    'DATACREDITO_AUTHORIZATION_TEXT': TEXTO_CONSENTIMIENTO_CENTRALES,
    'DATACREDITO_AUTHORIZATION_TEXT_VERSION': VERSION_CONSENTIMIENTO_CENTRALES,
    'DATACREDITO_ENABLED': False, 'DATACREDITO_REAL_ENABLED': False,
    'DATACREDITO_ENVIRONMENT': 'uat',
    'DATACREDITO_TOKEN_URL': '', 'DATACREDITO_REVOKE_TOKEN_URL': '',
    'DATACREDITO_MIDECISOR_URL': '', 'DATACREDITO_HISTORIAL_URL': '',
    'DATACREDITO_HDC_PRODUCT_ID': '64', 'DATACREDITO_HDC_PRODUCT_ID_EXPLICIT': True,
    'DATACREDITO_HDC_INFO_ACCOUNT_TYPE': '1', 'DATACREDITO_HDC_INFO_ACCOUNT_TYPE_EXPLICIT': True,
    'DATACREDITO_HDC_CHANNEL_NAME': 'CONEXRED-01', 'DATACREDITO_HDC_CHANNEL_TYPE': '42',
    'DATACREDITO_HDC_PARAMETERS_JSON': '',
}


@override_settings(**CONFIGURACION_FICTICIA)
class DatacreditoReadinessTest(TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.prohibidos = [stack.enter_context(patch(nombre, side_effect=AssertionError('Red prohibida'))) for nombre in (
            'requests.sessions.Session.request', 'httpx.Client.send', 'httpx.AsyncClient.send',
            'socket.getaddrinfo', 'socket.create_connection',
            'integrations.datacredito.auth.generar_token',
            'integrations.datacredito.auth.obtener_token_cacheado',
            'integrations.datacredito.auth.revocar_token',
        )]
        # Configuration checks must also be independent of database/policy state.
        self.consultas = stack.enter_context(self.assertNumQueries(0))

    def tearDown(self):
        for prohibido in self.prohibidos:
            prohibido.assert_not_called()

    def requisito(self, resultado, nombre):
        return next(r for requisitos in resultado['secciones'].values() for r in requisitos if r['nombre'] == nombre)

    def test_completo_deshabilitado_no_activa_ni_falla_por_flags(self):
        resultado = evaluar_readiness_datacredito()
        self.assertEqual(resultado['resultado'], 'LISTO_CONFIGURACION')
        self.assertEqual(resultado['estado'], 'CONFIGURADO_PERO_DESHABILITADO')
        self.assertFalse(resultado['habilitado'])
        self.assertTrue(all(not r['critico'] for r in resultado['secciones']['FLAGS']))
        for nombre in ('CONSENTIMIENTO_TEXTO_CANONICO', 'CONSENTIMIENTO_VERSION_CANONICA', 'CONSENTIMIENTO_SHA256'):
            self.assertTrue(self.requisito(resultado, nombre)['configurado'])

    def test_sin_overrides_consentimiento_canonico_listo(self):
        with override_settings():
            delattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT')
            delattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION')
            resultado = evaluar_readiness_datacredito()
        self.assertEqual(resultado['resultado'], 'LISTO_CONFIGURACION')
        self.assertTrue(self.requisito(resultado, 'DATACREDITO_AUTHORIZATION_TEXT')['configurado'])
        self.assertTrue(self.requisito(resultado, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION')['configurado'])

    def test_overrides_distintos_bloquean_con_flags_true_o_false_sin_mostrar_valores(self):
        for habilitado in (False, True):
            for nombre, valor in (
                ('DATACREDITO_AUTHORIZATION_TEXT', 'test-override-privado-no-imprimir'),
                ('DATACREDITO_AUTHORIZATION_TEXT_VERSION', 'test-version-no-imprimir'),
                ('DATACREDITO_AUTHORIZATION_TEXT', TEXTO_CONSENTIMIENTO_CENTRALES + '\n'),
                ('DATACREDITO_AUTHORIZATION_TEXT_VERSION', ' ' + VERSION_CONSENTIMIENTO_CENTRALES),
            ):
                with self.subTest(nombre=nombre, habilitado=habilitado), override_settings(**{
                    nombre: valor, 'DATACREDITO_ENABLED': habilitado, 'DATACREDITO_REAL_ENABLED': habilitado,
                }):
                    resultado = evaluar_readiness_datacredito()
                    self.assertEqual(resultado['resultado'], 'NO_LISTO')
                    self.assertFalse(self.requisito(resultado, nombre)['configurado'])
                    self.assertIn('Override', self.requisito(resultado, nombre)['mensaje'])
                    self.assertNotIn(valor, json.dumps(resultado))
                    salida = StringIO()
                    call_command('verificar_readiness_datacredito', stdout=salida)
                    self.assertNotIn(valor, salida.getvalue())

    def test_definicion_canonica_incompleta_bloquea(self):
        for constante, nombre, valor in (
            ('TEXTO_CONSENTIMIENTO_CENTRALES', 'DATACREDITO_AUTHORIZATION_TEXT', ''),
            ('VERSION_CONSENTIMIENTO_CENTRALES', 'DATACREDITO_AUTHORIZATION_TEXT_VERSION', ''),
        ):
            with self.subTest(constante=constante), patch('contractors.consentimiento_centrales.' + constante, valor), override_settings(**{nombre: valor}):
                resultado = evaluar_readiness_datacredito()
                self.assertEqual(resultado['resultado'], 'NO_LISTO')
                self.assertFalse(all(r['configurado'] for r in resultado['secciones']['CONSENTIMIENTO']))

    def test_todo_vacio_identifica_global_consentimiento_y_servicios(self):
        with override_settings(**{nombre: '' for nombre in CANONICOS + [
            'DATACREDITO_DOCUMENT_HASH_SECRET', 'DATACREDITO_AUTHORIZATION_TEXT',
            'DATACREDITO_AUTHORIZATION_TEXT_VERSION',
        ]}):
            resultado = evaluar_readiness_datacredito()
        self.assertEqual(resultado['resultado'], 'NO_LISTO')
        self.assertEqual(resultado['estado'], 'INCOMPLETO_Y_DESHABILITADO')
        for nombre in CANONICOS:
            self.assertFalse(self.requisito(resultado, nombre)['configurado'])

    def test_falta_cada_credencial_canonica_identifica_el_componente_sin_aceptar_legacy(self):
        for nombre in CANONICOS:
            with self.subTest(nombre=nombre), override_settings(**{
                nombre: '', 'DATACREDITO_CLIENT_ID': 'test-legacy-id',
                'DATACREDITO_CLIENT_SECRET': 'test-legacy-secret',
                'DATACREDITO_USERNAME': 'test-legacy-user', 'DATACREDITO_PASSWORD': 'test-legacy-pass',
            }):
                resultado = evaluar_readiness_datacredito()
                self.assertEqual(resultado['resultado'], 'NO_LISTO')
                self.assertFalse(self.requisito(resultado, nombre)['configurado'])

    def test_canal_diferente_bloquea(self):
        for nombre in ('DATACREDITO_HDC_CHANNEL_NAME', 'DATACREDITO_HDC_CHANNEL_TYPE'):
            with self.subTest(nombre=nombre), override_settings(**{nombre: 'otro'}):
                self.assertFalse(self.requisito(evaluar_readiness_datacredito(), nombre)['configurado'])

    def test_defaults_no_son_confirmacion_contractual(self):
        for nombre in ('DATACREDITO_HDC_PRODUCT_ID', 'DATACREDITO_HDC_INFO_ACCOUNT_TYPE'):
            with self.subTest(nombre=nombre), override_settings(**{nombre + '_EXPLICIT': False}):
                resultado = evaluar_readiness_datacredito()
                requisito = self.requisito(resultado, nombre)
                self.assertEqual(requisito['estado'], 'DEFAULT_NO_CONFIRMADO')
                self.assertFalse(requisito['configurado'])
                self.assertEqual(resultado['resultado'], 'NO_LISTO')
                salida = StringIO()
                call_command('verificar_readiness_datacredito', stdout=salida)
                self.assertIn('PENDIENTE_CONFIRMACION', salida.getvalue())

    def test_parametros_explicitos_invalidos_bloquean(self):
        for valor in ('', '0', '-1', 'texto'):
            with self.subTest(valor=valor), override_settings(DATACREDITO_HDC_PRODUCT_ID=valor):
                requisito = self.requisito(evaluar_readiness_datacredito(), 'DATACREDITO_HDC_PRODUCT_ID')
                self.assertEqual(requisito['estado'], 'FALTA_O_INVALIDO')
                self.assertFalse(requisito['configurado'])

    def test_hmac_minimo_y_sin_entropia_inferida(self):
        for longitud, valido in ((0, False), (31, False), (32, True), (43, True)):
            with self.subTest(longitud=longitud), override_settings(DATACREDITO_DOCUMENT_HASH_SECRET='x' * longitud):
                self.assertEqual(secreto_documental_valido('x' * longitud), valido)
                self.assertEqual(self.requisito(evaluar_readiness_datacredito(), 'DATACREDITO_DOCUMENT_HASH_SECRET')['configurado'], valido)

    def test_urls_contratadas_uat_y_credenciales_separadas(self):
        resultado = evaluar_readiness_datacredito()
        urls = {r['nombre']: r['url'] for requisitos in resultado['secciones'].values() for r in requisitos if 'url' in r}
        self.assertEqual(urls, {
            'DATACREDITO_TOKEN_URL': 'https://uat-api.datacredito.com.co/spla/oauth2/v1/token',
            'DATACREDITO_REVOKE_TOKEN_URL': 'https://uat-api.datacredito.com.co/spla/oauth2/v1/revokeToken',
            'DATACREDITO_MIDECISOR_URL': 'https://uat-api.datacredito.com.co/co/cs/midecisor/v1/client',
            'DATACREDITO_HISTORIAL_URL': 'https://uat-api.datacredito.com.co/cs/credit-history/v1/hdcplus',
        })

    def test_guard_runtime_rechaza_hmac_corto_antes_de_oauth(self):
        with override_settings(DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True,
                               DATACREDITO_DOCUMENT_HASH_SECRET='test-short'):
            configuracion = obtener_configuracion_datacredito()
            for servicio in ('decisor', 'historial'):
                self.assertEqual(_validar_configuracion(configuracion, servicio), 'secreto_hash_documento_insuficiente')

    def test_ambiente_o_url_no_contractual_bloquea_sin_imprimir_url_insegura(self):
        for nombre, valor in (
            ('DATACREDITO_ENVIRONMENT', 'desconocido'),
            ('DATACREDITO_TOKEN_URL', 'https://test-user:test-secret@invalid.example/token?secret=test-secret'),
        ):
            with self.subTest(nombre=nombre), override_settings(**{nombre: valor}):
                resultado = evaluar_readiness_datacredito()
                self.assertEqual(resultado['resultado'], 'NO_LISTO')
                self.assertNotIn(valor, json.dumps(resultado))

    def test_json_hdc_invalido_bloquea_sin_imprimir_contenido(self):
        with override_settings(DATACREDITO_HDC_PARAMETERS_JSON='test-secret-json-invalido'):
            resultado = evaluar_readiness_datacredito()
            self.assertFalse(self.requisito(resultado, 'DATACREDITO_HDC_PARAMETERS_JSON')['configurado'])
            self.assertNotIn('test-secret-json-invalido', json.dumps(resultado))

    def test_salida_humana_y_json_no_exponen_credenciales_ni_hmac(self):
        for opciones in ({}, {'json': True}):
            salida = StringIO()
            call_command('verificar_readiness_datacredito', stdout=salida, **opciones)
            texto = salida.getvalue()
            self.assertIn('LISTO_CONFIGURACION', texto)
            for nombre in CANONICOS + ['DATACREDITO_DOCUMENT_HASH_SECRET']:
                self.assertNotIn(CONFIGURACION_FICTICIA[nombre], texto)
            if opciones:
                self.assertEqual(json.loads(texto)['estado'], 'CONFIGURADO_PERO_DESHABILITADO')

    def test_flags_solo_describen_estado_no_realizan_consultas(self):
        with override_settings(DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True):
            self.assertEqual(evaluar_readiness_datacredito()['estado'], 'CONFIGURADO_Y_HABILITADO')
        with override_settings(DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True, DATACREDITO_HDC_SERVICE_PASSWORD=''):
            self.assertEqual(evaluar_readiness_datacredito()['estado'], 'INCOMPLETO_Y_HABILITADO')

    def test_cli_completo_no_sondea_redis_ni_base_de_datos_al_arrancar(self):
        entorno = {
            **os.environ, 'DJANGO_SETTINGS_MODULE': 'aprobado_web.settings', 'USE_SQLITE': 'true',
            'REDIS_URL': 'redis://localhost:6379/0', 'ALLOW_LOCAL_REDIS_FALLBACK': 'true',
            'DATACREDITO_ENABLED': 'false', 'DATACREDITO_REAL_ENABLED': 'false',
            **{nombre: '' for nombre in CANONICOS + ['DATACREDITO_DOCUMENT_HASH_SECRET']},
        }
        codigo = (
            "import sys; from unittest.mock import patch; "
            "sys.argv=['manage.py','verificar_readiness_datacredito','--json']; "
            "nombres=['socket.getaddrinfo','socket.create_connection','socket.socket.connect','socket.socket.connect_ex',"
            "'requests.sessions.Session.request','httpx.Client.send','httpx.AsyncClient.send',"
            "'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection']; "
            "guardas=[patch(n,side_effect=AssertionError('Acceso externo prohibido')) for n in nombres]; "
            "[g.start() for g in guardas]; "
            "patch('dotenv.load_dotenv',return_value=False).start(); "
            "from django.core.management import execute_from_command_line; execute_from_command_line(sys.argv)"
        )
        for nombre in ('DATACREDITO_AUTHORIZATION_TEXT', 'DATACREDITO_AUTHORIZATION_TEXT_VERSION'):
            entorno.pop(nombre, None)
        for overrides, texto_valido, version_valida in (
            ({}, True, True),
            ({'DATACREDITO_AUTHORIZATION_TEXT': TEXTO_CONSENTIMIENTO_CENTRALES,
              'DATACREDITO_AUTHORIZATION_TEXT_VERSION': VERSION_CONSENTIMIENTO_CENTRALES}, True, True),
            ({'DATACREDITO_AUTHORIZATION_TEXT': TEXTO_CONSENTIMIENTO_CENTRALES + '\n'}, False, True),
            ({'DATACREDITO_AUTHORIZATION_TEXT_VERSION': ' ' + VERSION_CONSENTIMIENTO_CENTRALES}, True, False),
        ):
            with self.subTest(texto_valido=texto_valido, version_valida=version_valida):
                proceso = subprocess.run([sys.executable, '-c', codigo], env={**entorno, **overrides},
                                         capture_output=True, text=True, timeout=30)
                self.assertEqual(proceso.returncode, 0, proceso.stderr)
                resultado = json.loads(proceso.stdout)
                self.assertEqual(resultado['estado'], 'INCOMPLETO_Y_DESHABILITADO')
                self.assertEqual(self.requisito(resultado, 'DATACREDITO_AUTHORIZATION_TEXT')['configurado'], texto_valido)
                self.assertEqual(self.requisito(resultado, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION')['configurado'], version_valida)

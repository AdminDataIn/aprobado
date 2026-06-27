import logging
import importlib
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from integrations.datacredito.auth import (
    SERVICIO_DECISOR,
    SERVICIO_HISTORIAL,
    TOKEN_CACHE_KEY,
    TOKEN_CACHE_KEYS,
    generar_token,
    obtener_token_cacheado,
)
from integrations.datacredito.decisor_client import consultar_midecisor_persona_natural
from integrations.datacredito.dto import (
    CredencialesOAuthDecisor,
    CredencialesOAuthHistorial,
    ESTADO_ERROR_CREDENCIAL_SERVICIO,
    ESTADO_EXITOSA_CON_INFORMACION,
    ESTADO_EXITOSA_SIN_INFORMACION,
    ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    FUENTE_MIDECISOR,
    CredencialesServicioHistorial,
    EntradaHistorialCredito,
    EntradaMiDecisor,
    ResultadoHistorialCreditoRawSeguro,
    ResultadoMiDecisorRawSeguro,
    TokenDatacredito,
)
from integrations.datacredito.exceptions import DatacreditoConfigError, DatacreditoProviderDisabled, DatacreditoProviderError
from integrations.datacredito.historial_client import consultar_historial_credito
from integrations.datacredito.normalizadores import (
    detectar_mora_severa_desde_vector,
    mapear_comportamiento_pago,
    normalizar_historial_credito,
    normalizar_midecisor_pj,
    normalizar_midecisor_pn,
    resumir_estructura_hdc_segura,
)
from integrations.datacredito.request_preview import construir_request_sanitizado_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.datacredito.snapshots import (
    construir_request_fingerprint,
    obtener_o_consultar_datacredito,
)
from integrations.models import ConsultaDatacreditoSnapshot


class FakeResponse:
    def __init__(self, status_code=200, body=None, json_error=None):
        self.status_code = status_code
        self._body = body or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._body


RUTA_FIXTURES_DATACREDITO = Path(__file__).resolve().parent / 'fixtures' / 'datacredito'


def cargar_fixture_datacredito(nombre):
    with (RUTA_FIXTURES_DATACREDITO / nombre).open(encoding='utf-8') as archivo:
        return json.load(archivo)


class DatacreditoBaseTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_importa_modulos_datacredito_sin_error(self):
        self.assertIsNotNone(importlib.import_module('integrations.datacredito.dto'))
        self.assertIsNotNone(importlib.import_module('integrations.datacredito.auth'))
        self.assertIsNotNone(importlib.import_module('integrations.management.commands.diagnosticar_datacredito_uat'))

    def test_dtos_credenciales_se_construyen_con_argumentos_nombrados(self):
        decisor = CredencialesOAuthDecisor(
            client_id='cliente-decisor',
            client_secret='secreto-decisor',
            username='usuario-decisor',
            password='clave-decisor',
        )
        historial = CredencialesOAuthHistorial(
            client_id='cliente-historial',
            client_secret='secreto-historial',
            username='usuario-historial',
            password='clave-historial',
        )
        servicio_hdc = CredencialesServicioHistorial(
            user='usuario-servicio',
            password='clave-servicio',
            server_ip_address='127.0.0.1',
        )

        self.assertEqual(decisor.servicio, 'decisor')
        self.assertEqual(historial.servicio, 'historial')
        self.assertEqual(servicio_hdc.product_id, '64')

    def test_repr_dtos_credenciales_no_expone_secretos(self):
        decisor = CredencialesOAuthDecisor(
            client_id='cliente-decisor',
            client_secret='secreto-decisor',
            username='usuario-decisor',
            password='clave-decisor',
        )
        historial = CredencialesOAuthHistorial(
            client_id='cliente-historial',
            client_secret='secreto-historial',
            username='usuario-historial',
            password='clave-historial',
        )
        servicio_hdc = CredencialesServicioHistorial(
            user='usuario-servicio',
            password='clave-servicio',
            server_ip_address='127.0.0.1',
        )

        salida = f'{decisor!r}\n{historial!r}\n{servicio_hdc!r}'
        self.assertNotIn('secreto-decisor', salida)
        self.assertNotIn('clave-decisor', salida)
        self.assertNotIn('secreto-historial', salida)
        self.assertNotIn('clave-historial', salida)
        self.assertNotIn('clave-servicio', salida)

    def test_dto_credenciales_exige_campos_obligatorios_en_constructor(self):
        with self.assertRaises(TypeError):
            CredencialesOAuthDecisor(
                client_id='cliente',
                client_secret='secreto',
                username='usuario',
            )

    def test_payload_midecisor_homologa_cc_a_tipo_uno(self):
        entrada = EntradaMiDecisor('CC', '1234567890', 'Perez')

        payload = entrada.como_payload()

        self.assertEqual(payload['tipoIdentificacion'], '1')
        self.assertEqual(payload['numeroIdentificacion'], '1234567890')
        self.assertEqual(payload['apellidoRazonSocial'], 'Perez')

    @override_settings(DATACREDITO_REAL_ENABLED=False)
    @patch('integrations.datacredito.decisor_client.requests.post')
    def test_provider_apagado_no_consume(self, post_mock):
        entrada = EntradaMiDecisor('CC', '1234567890', 'Perez')
        with self.assertRaises(DatacreditoProviderDisabled):
            consultar_midecisor_persona_natural(entrada)
        post_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_CLIENT_ID='',
        DATACREDITO_CLIENT_SECRET='',
        DATACREDITO_USERNAME='',
        DATACREDITO_PASSWORD='',
        DATACREDITO_DECISOR_CLIENT_ID='',
        DATACREDITO_DECISOR_CLIENT_SECRET='',
        DATACREDITO_DECISOR_USERNAME='',
        DATACREDITO_DECISOR_PASSWORD='',
    )
    def test_faltan_credenciales_error_controlado(self):
        with self.assertRaises(DatacreditoConfigError):
            generar_token()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto',
        DATACREDITO_DECISOR_USERNAME='usuario',
        DATACREDITO_DECISOR_PASSWORD='clave',
        DATACREDITO_TIMEOUT_SECONDS=15,
    )
    @patch('integrations.datacredito.auth.requests.post')
    def test_token_no_se_loguea_completo(self, post_mock):
        post_mock.return_value = FakeResponse(
            body={'access_token': 'tokentestsecretomuygrande', 'token_type': 'Bearer', 'expires_in': 3600}
        )
        with self.assertLogs('integrations.datacredito.auth', level=logging.INFO) as logs:
            token = generar_token()
        self.assertEqual(token.access_token, 'tokentestsecretomuygrande')
        salida = '\n'.join(logs.output)
        self.assertNotIn('tokentestsecretomuygrande', salida)
        self.assertIn('****', salida)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
        DATACREDITO_HDC_CLIENT_ID='cliente-historial',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-historial',
        DATACREDITO_HDC_USERNAME='usuario-historial',
        DATACREDITO_HDC_PASSWORD='clave-historial',
    )
    @patch('integrations.datacredito.auth.requests.post')
    def test_tokens_usan_perfiles_y_cache_separada(self, post_mock):
        post_mock.side_effect = [
            FakeResponse(body={'access_token': 'token-decisor', 'token_type': 'Bearer', 'expires_in': 120}),
            FakeResponse(body={'access_token': 'token-historial', 'token_type': 'Bearer', 'expires_in': 120}),
        ]

        token_decisor = obtener_token_cacheado(servicio=SERVICIO_DECISOR)
        token_historial = obtener_token_cacheado(servicio=SERVICIO_HISTORIAL)

        self.assertEqual(token_decisor.access_token, 'token-decisor')
        self.assertEqual(token_historial.access_token, 'token-historial')
        self.assertEqual(cache.get(TOKEN_CACHE_KEYS[SERVICIO_DECISOR]).access_token, 'token-decisor')
        self.assertEqual(cache.get(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL]).access_token, 'token-historial')
        primera_llamada = post_mock.call_args_list[0]
        segunda_llamada = post_mock.call_args_list[1]
        self.assertEqual(primera_llamada.kwargs['headers']['client_id'], 'cliente-decisor')
        self.assertEqual(primera_llamada.kwargs['json']['username'], 'usuario-decisor')
        self.assertEqual(segunda_llamada.kwargs['headers']['client_id'], 'cliente-historial')
        self.assertEqual(segunda_llamada.kwargs['json']['username'], 'usuario-historial')
        self.assertNotIn('data', primera_llamada.kwargs)
        self.assertNotIn('grant_type', primera_llamada.kwargs['json'])
        self.assertNotIn('data', segunda_llamada.kwargs)
        self.assertNotIn('grant_type', segunda_llamada.kwargs['json'])

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_TOKEN_USERNAME='usuario-token-decisor',
        DATACREDITO_DECISOR_TOKEN_PASSWORD='clave-token-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-legacy-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-legacy-decisor',
        DATACREDITO_HDC_CLIENT_ID='cliente-historial',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-historial',
        DATACREDITO_HDC_TOKEN_USERNAME='usuario-token-hdc',
        DATACREDITO_HDC_TOKEN_PASSWORD='clave-token-hdc',
        DATACREDITO_HDC_USERNAME='usuario-legacy-hdc',
        DATACREDITO_HDC_PASSWORD='clave-legacy-hdc',
    )
    @patch('integrations.datacredito.auth.requests.post')
    def test_tokens_prefieren_credenciales_token_separadas(self, post_mock):
        post_mock.side_effect = [
            FakeResponse(body={'access_token': 'token-decisor', 'token_type': 'Bearer', 'expires_in': 120}),
            FakeResponse(body={'access_token': 'token-historial', 'token_type': 'Bearer', 'expires_in': 120}),
        ]

        generar_token(servicio=SERVICIO_DECISOR)
        generar_token(servicio=SERVICIO_HISTORIAL)

        primera_llamada = post_mock.call_args_list[0]
        segunda_llamada = post_mock.call_args_list[1]
        self.assertEqual(primera_llamada.kwargs['json']['username'], 'usuario-token-decisor')
        self.assertEqual(segunda_llamada.kwargs['json']['username'], 'usuario-token-hdc')

    def test_normalizador_decisor_pn_extrae_campos(self):
        resultado = normalizar_midecisor_pn({
            'responseCode': '13',
            'score': 812,
            'viabilidad': 'APROBADO',
            'montoSugerido': 9000000,
            'saldoMora': 0,
        })
        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.score, 812)
        self.assertTrue(resultado.viable)
        self.assertEqual(resultado.monto_sugerido, 9000000)
        self.assertEqual(resultado.saldo_mora, 0)

    def test_midecisor_con_informacion_extrae_campos_contractuales(self):
        resultado = normalizar_midecisor_pn(cargar_fixture_datacredito('midecisor_pn_con_informacion.json'))

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.estado, ESTADO_EXITOSA_CON_INFORMACION)
        self.assertTrue(resultado.con_informacion)
        self.assertEqual(resultado.score_midecisor, 853)
        self.assertEqual(resultado.score_normalizado_0_1000, 853)
        self.assertEqual(resultado.viabilidad, 'ALTA')
        self.assertEqual(resultado.rating_recaudo, 'A')
        self.assertEqual(resultado.monto_sugerido, 13809492)
        self.assertEqual(resultado.ingreso_estimado, Decimal('12507000.0'))
        self.assertEqual(resultado.porcentaje_cuota_vs_ingreso, Decimal('38.9'))
        self.assertEqual(resultado.creditos_vigentes, 7)
        self.assertEqual(resultado.creditos_cerrados, 57)
        self.assertEqual(resultado.saldo_actual, Decimal('205834'))
        self.assertEqual(resultado.valor_cuota_total, Decimal('4865'))
        self.assertEqual(resultado.saldo_mora, Decimal('0'))
        self.assertEqual(resultado.porcentaje_deuda, Decimal('77.9'))
        self.assertFalse(resultado.bloqueo_automatico)
        self.assertTrue(resultado.requiere_revision_cumplimiento)
        self.assertIn('coincidencia_solo_nombre', resultado.alertas_resumen)
        serializado = str(resultado.como_dict())
        self.assertNotIn('123456789', serializado)
        self.assertNotIn('Persona', serializado)

    def test_midecisor_sin_informacion_identificacion_no_encontrada(self):
        resultado = normalizar_midecisor_pn(cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'))

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.estado, ESTADO_IDENTIFICACION_NO_ENCONTRADA)
        self.assertFalse(resultado.con_informacion)
        self.assertIsNone(resultado.score)
        self.assertIsNone(resultado.score_midecisor)
        self.assertIsNone(resultado.mora_severa)
        self.assertTrue(resultado.requiere_revision_manual)

    def test_hdcplus_sanitizado_extrae_resumen_estructural_real(self):
        resultado = normalizar_historial_credito(cargar_fixture_datacredito('hdcplus_sample_sanitized.json'))
        resumen = resultado.metadata_segura['hdc_resumen']
        estructura = resultado.metadata_segura['hdc_estructura']

        self.assertTrue(resumen['hdc_disponible'])
        self.assertEqual(resumen['response_code'], '13')
        self.assertTrue(resumen['consulta_efectiva'])
        self.assertEqual(resumen['total_savings'], 6)
        self.assertEqual(resumen['total_liabilities'], 7)
        self.assertEqual(resumen['liabilities_castigadas'], 3)
        self.assertEqual(resumen['liabilities_en_mora'], 5)
        self.assertEqual(resumen['saldo_total_hdc'], '12629000')
        self.assertEqual(resumen['saldo_mora_hdc'], '1853000')
        self.assertEqual(resumen['cuota_total_hdc'], '1127000')
        self.assertEqual(resumen['max_mora_dias'], 999)
        self.assertEqual(resumen['huellas_consulta'], 3)
        self.assertEqual(resumen['huellas_ultimos_6_meses'], 3)
        self.assertEqual(resumen['alertas_hdc'], 1)
        self.assertIn('SECTOR REAL', resumen['sectores_detectados'])
        self.assertIn('SECTOR FINANCIERO', resumen['sectores_detectados'])
        self.assertIn('CON', resumen['tipos_cartera_detectados'])
        self.assertTrue(resumen['endeudamiento_global_detectado'])
        self.assertTrue(resumen['resumen_agregado_detectado'])
        self.assertTrue(resumen['resumen_microcredito_detectado'])
        self.assertTrue(resumen['requiere_revision_manual_hdc'])
        self.assertIn('liabilities', estructura['report_hdcplus_keys'])
        self.assertEqual(estructura['conteos']['liabilities'], 7)

    def test_hdcplus_resumen_no_expone_pii(self):
        resultado = normalizar_historial_credito(cargar_fixture_datacredito('hdcplus_sample_sanitized.json'))
        salida = json.dumps(resultado.metadata_segura, ensure_ascii=False)

        self.assertNotIn('personIdNumber', salida)
        self.assertNotIn('accountNumber', salida)
        self.assertNotIn('primary' + 'Key', salida)
        self.assertNotIn('counterpartyId' + 'Number', salida)
        self.assertNotIn('address', salida)

    def test_midecisor_sanitizado_preserva_score_y_viabilidad_separado_de_hdc(self):
        resultado = normalizar_midecisor_pn(cargar_fixture_datacredito('midecisor_sample_sanitized.json'))

        self.assertEqual(resultado.fuente, FUENTE_MIDECISOR)
        self.assertEqual(resultado.score_midecisor, 837)
        self.assertEqual(resultado.viabilidad, 'ALTA')
        self.assertEqual(resultado.rating_recaudo, 'A')
        self.assertNotIn('hdc_resumen', resultado.metadata_segura)

    def test_normalizador_decisor_pj_extrae_riesgo_y_alertas(self):
        resultado = normalizar_midecisor_pj({
            'puntaje': 430,
            'nivelRiesgo': 'ALTO',
            'tieneEmbargos': True,
            'enLiquidacion': True,
        })
        self.assertEqual(resultado.score, 430)
        self.assertEqual(resultado.nivel_riesgo, 'ALTO')
        self.assertTrue(resultado.embargos)
        self.assertTrue(resultado.liquidacion)
        self.assertIn('embargos_reportados', resultado.alertas_resumen)

    def test_normalizador_historial_detecta_response_code_exitoso(self):
        resultado = normalizar_historial_credito({'responseCode': '13', 'scoreCrediticio': 700})
        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.response_code, '13')
        self.assertEqual(resultado.score, 700)

    def test_historial_codigo_02_no_es_exito(self):
        resultado = normalizar_historial_credito(cargar_fixture_datacredito('historial_codigo_02_clave_errada.json'))

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.estado, ESTADO_ERROR_CREDENCIAL_SERVICIO)
        self.assertEqual(resultado.response_code, '02')
        self.assertEqual(resultado.error_tipo, 'error_credencial_servicio')
        self.assertTrue(resultado.requiere_revision_manual)

    def test_historial_codigo_14_queda_revision_manual(self):
        resultado = normalizar_historial_credito(cargar_fixture_datacredito('historial_codigo_14_sin_informacion.json'))

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.estado, ESTADO_EXITOSA_SIN_INFORMACION)
        self.assertFalse(resultado.con_informacion)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIsNone(resultado.mora_severa)

    def test_historial_codigo_13_documentacion_conserva_scores_hdc_sin_score_principal(self):
        resultado = normalizar_historial_credito(cargar_fixture_datacredito('historial_codigo_13_documentacion.json'))

        self.assertTrue(resultado.disponible)
        self.assertEqual(resultado.estado, ESTADO_EXITOSA_CON_INFORMACION)
        self.assertTrue(resultado.con_informacion)
        self.assertIsNone(resultado.score)
        self.assertIsNone(resultado.score_normalizado_0_1000)
        self.assertEqual(resultado.scores_hdc[0]['nombre_modelo'], 'HDC Plus')
        self.assertEqual(resultado.scores_hdc[0]['score_value'], 711)

    def test_valores_ausentes_no_se_convierten_a_cero(self):
        resultado = normalizar_midecisor_pn({
            'status': 'ACCEPTED',
            'content': {
                'infoTransaccion': {'codigosRespuesta': [{'clave': 'HC', 'valor': '13'}]},
                'respuesta': {
                    'validacion': {'conInformacion': True},
                    'informacionRiesgo': {'score': '-1', 'montoSugerido': '-'},
                    'comportamientoCrediticio': {'indicadoresValores': {'saldoMora': '-', 'valorCuota': '-1'}},
                },
            },
        })

        self.assertIsNone(resultado.score)
        self.assertIsNone(resultado.saldo_mora)
        self.assertIsNone(resultado.valor_cuota_total)
        self.assertIsNone(resultado.mora_severa)

    def test_normalizador_historial_detecta_mora_severa(self):
        for codigo in ['3', '4', '5', '6', 'C', 'D']:
            self.assertTrue(detectar_mora_severa_desde_vector([codigo]))
        self.assertFalse(detectar_mora_severa_desde_vector(['N', '1', '2']))
        self.assertEqual(mapear_comportamiento_pago('C'), 'cartera_castigada')

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto',
        DATACREDITO_DECISOR_USERNAME='usuario',
        DATACREDITO_DECISOR_PASSWORD='clave',
        DATACREDITO_TIMEOUT_SECONDS=15,
    )
    @patch('integrations.datacredito.auth.requests.post')
    def test_cache_token_usa_expires_in(self, post_mock):
        post_mock.return_value = FakeResponse(
            body={'access_token': 'token-cacheado', 'token_type': 'Bearer', 'expires_in': 120}
        )
        token = obtener_token_cacheado()
        self.assertIsInstance(token, TokenDatacredito)
        self.assertEqual(cache.get(TOKEN_CACHE_KEY).access_token, 'token-cacheado')
        segundo = obtener_token_cacheado()
        self.assertEqual(segundo.access_token, 'token-cacheado')
        self.assertEqual(post_mock.call_count, 1)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='127.0.0.1',
        DATACREDITO_HDC_CHANNEL_NAME='Canal-01',
        DATACREDITO_HDC_CHANNEL_TYPE='42',
    )
    def test_historial_requiere_token_sin_guardar_raw_sensible(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(
            body={
                'responseCode': '13',
                'scoreCrediticio': 710,
                'personIdNumber': '1234567890',
            }
        )
        from integrations.datacredito.dto import EntradaHistorialCredito
        entrada = EntradaHistorialCredito(
            tipo_identificacion='CC',
            numero_identificacion='1234567890',
            apellido='Perez',
            request_uuid='uuid-prueba',
            fecha_hora='2026-06-07T10:00:00',
        )
        resultado = consultar_historial_credito(entrada, session=session)
        self.assertEqual(resultado.response_code, '13')
        self.assertNotIn('personIdNumber', resultado.raw_sanitizado)
        llamada = session.post.call_args
        self.assertNotIn('Accept', llamada.kwargs['headers'])
        self.assertEqual(llamada.kwargs['headers']['ProductId'], '64')
        self.assertEqual(llamada.kwargs['headers']['InfoAccountType'], '1')
        self.assertEqual(llamada.kwargs['headers']['serverIpAddress'], '127.0.0.1')
        self.assertEqual(llamada.kwargs['headers']['client_id'], 'cliente-hdc')
        self.assertEqual(llamada.kwargs['headers']['client_secret'], 'secreto-hdc')
        self.assertEqual(llamada.kwargs['json']['user'], 'usuario-servicio')
        self.assertEqual(llamada.kwargs['json']['password'], 'clave-servicio')
        self.assertEqual(llamada.kwargs['json']['identifyingUser']['person']['personId']['personIdType'], 1)
        self.assertEqual(llamada.kwargs['json']['identifyingTrx']['originatorChannelName'], 'Canal-01')
        self.assertEqual(llamada.kwargs['json']['identifyingTrx']['originatorChannelType'], '42')

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='127.0.0.1',
        DATACREDITO_HDC_PARAMETERS_JSON='[{"type":"0","nameParameter":"codigos","valueParameter":"TOM-001"}]',
    )
    def test_historial_incluye_parametros_configurados(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(body={'responseCode': '13'})
        entrada = EntradaHistorialCredito(
            tipo_identificacion='CC',
            numero_identificacion='1234567890',
            apellido=' Perez ',
            request_uuid='uuid-prueba',
        )

        consultar_historial_credito(entrada, session=session)

        payload = session.post.call_args.kwargs['json']
        self.assertEqual(
            payload['parameters'],
            [{'type': '0', 'nameParameter': 'codigos', 'valueParameter': 'TOM-001'}],
        )
        self.assertEqual(payload['identifyingUser']['person']['personLastName'], 'PEREZ')

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='127.0.0.1',
        DATACREDITO_HDC_PARAMETERS_JSON='',
    )
    def test_historial_no_envia_parametros_vacios(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(body={'responseCode': '13'})

        with patch.dict('os.environ', {'DATACREDITO_HDC_PARAMETERS_JSON': ''}):
            consultar_historial_credito(
                EntradaHistorialCredito(tipo_identificacion='CC', numero_identificacion='1234567890', apellido='Perez'),
                session=session,
            )

        self.assertNotIn('parameters', session.post.call_args.kwargs['json'])

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
        DATACREDITO_HDC_PARAMETERS_JSON='',
    )
    def test_historial_request_coincide_con_postman_funcional_sin_parametros(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(body={'responseCode': '13'})

        with patch.dict('os.environ', {'DATACREDITO_HDC_PARAMETERS_JSON': ''}):
            consultar_historial_credito(
                EntradaHistorialCredito(tipo_identificacion='CC', numero_identificacion='1234567890', apellido='PEREZ'),
                session=session,
            )

        llamada = session.post.call_args
        headers = llamada.kwargs['headers']
        payload = llamada.kwargs['json']
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Accept', headers)
        self.assertEqual(headers['serverIpAddress'], '72.60.67.60')
        self.assertEqual(headers['ProductId'], '64')
        self.assertEqual(headers['InfoAccountType'], '1')
        self.assertEqual(headers['client_id'], 'cliente-hdc')
        self.assertEqual(headers['client_secret'], 'secreto-hdc')
        self.assertEqual(headers['Authorization'], 'Bearer token-seguro')
        self.assertEqual(payload['user'], 'usuario-servicio')
        self.assertEqual(payload['password'], 'clave-servicio')
        self.assertEqual(payload['identifyingTrx']['originatorChannelName'], 'Canal-01')
        self.assertEqual(payload['identifyingTrx']['originatorChannelType'], '42')
        self.assertTrue(payload['identifyingTrx']['dateTime'].endswith('-05:00'))
        self.assertEqual(payload['identifyingUser']['person']['personId']['personIdType'], 1)
        self.assertEqual(payload['identifyingUser']['person']['personLastName'], 'PEREZ')
        self.assertNotIn('parameters', payload)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='127.0.0.1',
    )
    def test_historial_genera_uuid_nuevo_y_fecha_timezone_aware(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(body={'responseCode': '13'})
        entrada = EntradaHistorialCredito(
            tipo_identificacion='CC',
            numero_identificacion='1234567890',
            apellido='Perez',
        )

        consultar_historial_credito(entrada, session=session)
        primer_payload = session.post.call_args.kwargs['json']
        consultar_historial_credito(entrada, session=session)
        segundo_payload = session.post.call_args.kwargs['json']

        primer_uuid = primer_payload['identifyingTrx']['requestUUID']
        segundo_uuid = segundo_payload['identifyingTrx']['requestUUID']
        self.assertNotEqual(primer_uuid, segundo_uuid)
        self.assertTrue(primer_payload['identifyingTrx']['dateTime'].endswith('-05:00'))

    @override_settings(DATACREDITO_HDC_PARAMETERS_JSON='[{"type":"0","nameParameter":"codigos","valueParameter":"TOM-001"}]')
    def test_settings_parsea_parametros_hdc_validos(self):
        configuracion = obtener_configuracion_datacredito()

        self.assertEqual(
            configuracion.parametros_historial,
            ({'type': '0', 'nameParameter': 'codigos', 'valueParameter': 'TOM-001'},),
        )
        self.assertEqual(configuracion.parametros_historial_error, '')
        self.assertTrue(configuracion.parametros_historial_configurados)
        self.assertGreater(configuracion.parametros_historial_longitud, 0)

    @override_settings(DATACREDITO_HDC_PARAMETERS_JSON='')
    def test_settings_lee_parametros_hdc_desde_entorno(self):
        with patch.dict(
            'os.environ',
            {'DATACREDITO_HDC_PARAMETERS_JSON': '[{"type":"0","nameParameter":"codigos","valueParameter":"TOM-001"}]'},
        ):
            configuracion = obtener_configuracion_datacredito()

        self.assertEqual(
            configuracion.parametros_historial,
            ({'type': '0', 'nameParameter': 'codigos', 'valueParameter': 'TOM-001'},),
        )
        self.assertTrue(configuracion.parametros_historial_configurados)

    @override_settings(DATACREDITO_HDC_PARAMETERS_JSON='{"type":"0"}')
    def test_settings_reporta_parametros_hdc_invalidos(self):
        configuracion = obtener_configuracion_datacredito()

        self.assertEqual(configuracion.parametros_historial, ())
        self.assertIn('debe ser una lista', configuracion.parametros_historial_error)
        self.assertTrue(configuracion.parametros_historial_configurados)

    @override_settings(
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
        DATACREDITO_HDC_PARAMETERS_JSON='',
    )
    def test_preview_request_historial_sanitizado_replica_postman(self):
        with patch.dict('os.environ', {'DATACREDITO_HDC_PARAMETERS_JSON': ''}):
            preview = construir_request_sanitizado_datacredito(
                servicio=SERVICIO_HISTORIAL,
                tipo_documento='CC',
                numero_documento='1234567890',
                apellido='PEREZ',
            )

        token_request = preview['token_request']
        service_request = preview['service_request']
        salida = json.dumps(preview)
        self.assertEqual(token_request['method'], 'POST')
        self.assertTrue(token_request['usa_json'])
        self.assertFalse(token_request['usa_form_urlencoded'])
        self.assertFalse(token_request['incluye_grant_type'])
        self.assertEqual(service_request['method'], 'POST')
        self.assertEqual(service_request['headers_presentes']['serverIpAddress'], True)
        self.assertEqual(service_request['headers_presentes']['ProductId'], True)
        self.assertEqual(service_request['headers_presentes']['InfoAccountType'], True)
        self.assertEqual(service_request['headers_presentes']['client_id'], True)
        self.assertEqual(service_request['headers_presentes']['client_secret'], True)
        self.assertEqual(service_request['headers_presentes']['Authorization'], True)
        self.assertFalse(service_request['parameters_incluidos'])
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('PEREZ', salida)
        self.assertNotIn('secreto-hdc', salida)
        self.assertNotIn('clave-servicio', salida)

    @override_settings(
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
        DATACREDITO_HDC_PARAMETERS_JSON='[{"type":"0","nameParameter":"codigos","valueParameter":"TOM-001"}]',
    )
    def test_preview_request_historial_incluye_parametros_solo_configurados(self):
        preview = construir_request_sanitizado_datacredito(
            servicio=SERVICIO_HISTORIAL,
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='PEREZ',
        )

        self.assertTrue(preview['service_request']['parameters_incluidos'])

    @override_settings(
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
    )
    def test_preview_request_midecisor_no_incluye_headers_hdc(self):
        preview = construir_request_sanitizado_datacredito(
            servicio=SERVICIO_DECISOR,
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='PEREZ',
        )

        service_request = preview['service_request']
        salida = json.dumps(preview)
        self.assertFalse(service_request['headers_hdc_incluidos'])
        self.assertEqual(service_request['body_keys'], ['tipoIdentificacion', 'numeroIdentificacion', 'apellidoRazonSocial'])
        self.assertNotIn('serverIpAddress', service_request['headers'])
        self.assertNotIn('ProductId', service_request['headers'])
        self.assertNotIn('InfoAccountType', service_request['headers'])
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('PEREZ', salida)

    def test_resumen_estructura_hdc_detecta_secciones_postman_sin_valores(self):
        estructura = resumir_estructura_hdc_segura(
            {
                'ReportHDCplus': {
                    'productResult': {'responseCode': '13', 'responseDesc': 'Consulta exitosa'},
                    'basicInformation': {'documentNumber': '1234567890', 'fullName': 'Nombre sensible'},
                    'savings': [{'accountNumber': '0001'}, {'accountNumber': '0002'}],
                    'inquiryFootprints': [{'inquiryReasonDesc': 'Consulta de prueba'}],
                    'creditCards': [{'accountNumber': '999'}],
                    'financialSector': [{'paymentBehavior': 'NNN'}],
                    'realSector': [],
                    'models': [{'scoreValue': '711'}],
                    'obligations': [{'paymentBehavior': 'NNN'}],
                    'location': {'address': 'Direccion sensible'},
                }
            }
        )

        self.assertEqual(estructura['top_level_keys'], ['ReportHDCplus'])
        self.assertIn('savings', estructura['report_hdcplus_keys'])
        self.assertEqual(estructura['conteos']['savings'], 2)
        self.assertEqual(estructura['conteos']['inquiryFootprints'], 1)
        self.assertEqual(estructura['conteos']['creditCards'], 1)
        self.assertEqual(estructura['conteos']['financialSector'], 1)
        self.assertEqual(estructura['conteos']['models'], 1)
        self.assertEqual(estructura['conteos']['obligations'], 1)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-oauth-hdc',
        DATACREDITO_HDC_PASSWORD='clave-oauth-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
    )
    def test_historial_raw_sanitizado_no_expone_pii_anidada(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL], TokenDatacredito(access_token='token-seguro', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(
            body={
                'ReportHDCplus': {
                    'productResult': {'responseCode': '13'},
                    'basicInformation': {
                        'personIdNumber': '1234567890',
                        'personLastName': 'PEREZ',
                        'address': 'Direccion sensible',
                    },
                    'savings': [{'accountNumber': '0000123'}],
                    'models': [{'modelName': 'HDC Plus', 'scoreValue': '711'}],
                }
            }
        )

        resultado = consultar_historial_credito(
            EntradaHistorialCredito(tipo_identificacion='CC', numero_identificacion='1234567890', apellido='PEREZ'),
            session=session,
        )

        salida = json.dumps(resultado.raw_sanitizado)
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('PEREZ', salida)
        self.assertNotIn('Direccion sensible', salida)
        self.assertNotIn('0000123', salida)
        self.assertIn('scoreValue', salida)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
    )
    def test_midecisor_no_envia_secretos_en_consulta_negocio(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_DECISOR], TokenDatacredito(access_token='token-decisor', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(body={'responseCode': '13', 'score': 800})
        entrada = EntradaMiDecisor('CC', '1234567890', 'Perez')

        consultar_midecisor_persona_natural(entrada, session=session)

        llamada = session.post.call_args
        headers = llamada.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer token-decisor')
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Accept', headers)
        self.assertEqual(llamada.kwargs['json']['tipoIdentificacion'], '1')
        self.assertEqual(llamada.kwargs['json']['numeroIdentificacion'], '1234567890')
        self.assertEqual(llamada.kwargs['json']['apellidoRazonSocial'], 'Perez')
        self.assertNotIn('serverIpAddress', headers)
        self.assertNotIn('ProductId', headers)
        self.assertNotIn('InfoAccountType', headers)
        self.assertNotIn('client_id', headers)
        self.assertNotIn('client_secret', headers)
        self.assertNotIn('client_secret', llamada.kwargs['json'])
        self.assertNotIn('user', llamada.kwargs['json'])
        self.assertNotIn('password', llamada.kwargs['json'])
        self.assertNotIn('parameters', llamada.kwargs['json'])

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
    )
    def test_midecisor_error_parseo_json_se_clasifica(self):
        cache.set(TOKEN_CACHE_KEYS[SERVICIO_DECISOR], TokenDatacredito(access_token='token-decisor', expires_in=120), timeout=60)
        session = Mock()
        session.post.return_value = FakeResponse(status_code=200, json_error=ValueError('json invalido sensible'))

        with self.assertRaises(DatacreditoProviderError) as contexto:
            consultar_midecisor_persona_natural(EntradaMiDecisor('CC', '1234567890', 'Perez'), session=session)

        self.assertEqual(contexto.exception.etapa, 'PARSEO_JSON')
        self.assertEqual(contexto.exception.http_status, 200)
        self.assertEqual(contexto.exception.error_tipo, 'ERROR_PARSEO_JSON')


class ConsultaDatacreditoSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.override = override_settings(
            DATACREDITO_REAL_ENABLED=True,
            DATACREDITO_ENVIRONMENT='uat',
            DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
            DATACREDITO_REUSE_DAYS=30,
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        cache.clear()

    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_primera_consulta_llama_proveedor_y_crea_snapshot(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )

        resultado = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertFalse(resultado.reutilizado)
        self.assertTrue(resultado.consultado_proveedor)
        self.assertIsNotNone(resultado.snapshot)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 1)
        self.assertEqual(resultado.snapshot.estado_normalizado, ESTADO_IDENTIFICACION_NO_ENCONTRADA)
        self.assertTrue(resultado.snapshot.requiere_revision_manual)
        self.assertFalse(resultado.snapshot.utilizable_para_score)
        self.assertNotIn('1234567890', str(resultado.snapshot.resultado_normalizado))
        self.assertNotIn('Perez', str(resultado.snapshot.resultado_normalizado))

    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_segunda_consulta_vigente_reutiliza_snapshot(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        primera = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        decisor_mock.reset_mock()
        segunda = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertTrue(segunda.reutilizado)
        self.assertFalse(segunda.consultado_proveedor)
        self.assertEqual(segunda.snapshot.id, primera.snapshot.id)
        decisor_mock.assert_not_called()

    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_snapshot_vencido_genera_nueva_consulta(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        primera = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )
        primera.snapshot.vigente_hasta = timezone.now() - timedelta(days=1)
        primera.snapshot.save(update_fields=['vigente_hasta'])

        segunda = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertFalse(segunda.reutilizado)
        self.assertNotEqual(segunda.snapshot.id, primera.snapshot.id)
        self.assertEqual(decisor_mock.call_count, 2)

    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_forzar_consulta_crea_snapshot_nuevo(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        primera = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )
        segunda = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
            forzar_consulta=True,
        )

        self.assertFalse(segunda.reutilizado)
        self.assertNotEqual(segunda.snapshot.id, primera.snapshot.id)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 2)

    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    def test_historial_codigo_14_se_reutiliza_y_sigue_revision_manual(self, historial_mock):
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='14',
            raw_sanitizado=cargar_fixture_datacredito('historial_codigo_14_sin_informacion.json'),
        )

        primera = obtener_o_consultar_datacredito(
            servicio='historial',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )
        historial_mock.reset_mock()
        segunda = obtener_o_consultar_datacredito(
            servicio='historial',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertTrue(segunda.reutilizado)
        self.assertTrue(primera.resultado_normalizado.requiere_revision_manual)
        self.assertFalse(segunda.snapshot.utilizable_para_score)
        historial_mock.assert_not_called()

    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    def test_historial_codigo_02_no_se_persiste_como_reutilizable(self, historial_mock):
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='02',
            raw_sanitizado=cargar_fixture_datacredito('historial_codigo_02_clave_errada.json'),
        )

        resultado = obtener_o_consultar_datacredito(
            servicio='historial',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertIsNone(resultado.snapshot)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 0)

    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    def test_error_temporal_no_se_persiste(self, historial_mock):
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='23',
            raw_sanitizado={'ReportHDCplus': {'productResult': {'responseCode': '23'}}},
        )

        resultado = obtener_o_consultar_datacredito(
            servicio='historial',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertIsNone(resultado.snapshot)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 0)

    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_http_401_no_se_persiste(self, decisor_mock):
        decisor_mock.side_effect = DatacreditoProviderError(
            'HTTP error',
            servicio='decisor',
            etapa='HTTP',
            http_status=401,
            error_tipo='HTTP_ERROR',
        )

        with self.assertRaises(DatacreditoProviderError):
            obtener_o_consultar_datacredito(
                servicio='decisor',
                tipo_documento='CC',
                numero_documento='1234567890',
                apellido='Perez',
            )

        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 0)

    def test_fingerprints_cambian_por_apellido_ambiente_y_servicio(self):
        base = construir_request_fingerprint(
            ambiente='uat',
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )
        self.assertNotEqual(
            base,
            construir_request_fingerprint(
                ambiente='uat',
                servicio='decisor',
                tipo_documento='CC',
                numero_documento='1234567890',
                apellido='Gomez',
            ),
        )
        self.assertNotEqual(
            base,
            construir_request_fingerprint(
                ambiente='prod',
                servicio='decisor',
                tipo_documento='CC',
                numero_documento='1234567890',
                apellido='Perez',
            ),
        )
        self.assertNotEqual(
            base,
            construir_request_fingerprint(
                ambiente='uat',
                servicio='historial',
                tipo_documento='CC',
                numero_documento='1234567890',
                apellido='Perez',
            ),
        )

    @patch('integrations.datacredito.snapshots._buscar_snapshot_vigente')
    @patch('integrations.datacredito.snapshots.time.sleep')
    @patch('integrations.datacredito.snapshots.cache.add', return_value=False)
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_lock_reutiliza_snapshot_creado_por_otro_proceso(
        self,
        decisor_mock,
        add_mock,
        sleep_mock,
        buscar_mock,
    ):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        creada = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
            forzar_consulta=True,
        ).snapshot
        decisor_mock.reset_mock()
        buscar_mock.side_effect = [None, creada]

        resultado = obtener_o_consultar_datacredito(
            servicio='decisor',
            tipo_documento='CC',
            numero_documento='1234567890',
            apellido='Perez',
        )

        self.assertTrue(resultado.reutilizado)
        decisor_mock.assert_not_called()
        sleep_mock.assert_called()

    def test_admin_snapshot_es_solo_lectura(self):
        from django.contrib import admin
        from integrations.admin import ConsultaDatacreditoSnapshotAdmin

        admin_obj = ConsultaDatacreditoSnapshotAdmin(ConsultaDatacreditoSnapshot, admin.site)

        self.assertFalse(admin_obj.has_add_permission(None))
        self.assertFalse(admin_obj.has_change_permission(None))
        self.assertFalse(admin_obj.has_delete_permission(None))


class DiagnosticoDatacreditoUatCommandTests(TestCase):
    def _ejecutar(self, *args, **kwargs):
        salida = StringIO()
        call_command(
            'diagnosticar_datacredito_uat',
            '--tipo-documento', 'CC',
            '--numero-documento', '1234567890',
            '--apellido', 'KENT',
            *args,
            stdout=salida,
            **kwargs,
        )
        return salida.getvalue()

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_sin_confirmar_consumo_real_no_llama_proveedor(self, decisor_mock):
        salida = self._ejecutar('--servicio', 'decisor')

        self.assertIn('Consumo real no ejecutado', salida)
        decisor_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
        DATACREDITO_HDC_CLIENT_ID='cliente-historial',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-historial',
        DATACREDITO_HDC_USERNAME='usuario-historial',
        DATACREDITO_HDC_PASSWORD='clave-historial',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_PRODUCT_ID='64',
        DATACREDITO_HDC_INFO_ACCOUNT_TYPE='1',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='127.0.0.1',
        DATACREDITO_HDC_PARAMETERS_JSON='[{"type":"0","nameParameter":"codigos","valueParameter":"TOM-001"}]',
    )
    @patch('integrations.datacredito.auth.generar_token')
    @patch('integrations.datacredito.auth.requests.post')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_validar_configuracion_no_consume_ni_muestra_secretos(self, decisor_mock, post_mock, generar_token_mock):
        salida = self._ejecutar('--validar-configuracion')

        self.assertIn('decisor.completo=True', salida)
        self.assertIn('historial.completo=True', salida)
        self.assertIn('historial.product_id_configurado=True', salida)
        self.assertIn('historial.server_ip_configurada=True', salida)
        self.assertIn('historial.parameters_configurados=True', salida)
        self.assertIn('historial.parameters_validos=True', salida)
        self.assertIn('historial.parameters_env_var=DATACREDITO_HDC_PARAMETERS_JSON', salida)
        self.assertIn('historial.parameters_env_presente=True', salida)
        self.assertIn('historial.parameters_json_parseado=True', salida)
        self.assertIn('historial.parameters_cantidad=1', salida)
        self.assertNotIn('secreto-decisor', salida)
        self.assertNotIn('clave-servicio', salida)
        decisor_mock.assert_not_called()
        post_mock.assert_not_called()
        generar_token_mock.assert_not_called()
        self.assertIsNone(cache.get(TOKEN_CACHE_KEYS[SERVICIO_DECISOR]))
        self.assertIsNone(cache.get(TOKEN_CACHE_KEYS[SERVICIO_HISTORIAL]))

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_HDC_PARAMETERS_JSON='{mal-json',
    )
    def test_validar_configuracion_reporta_parametros_hdc_invalidos(self):
        salida = self._ejecutar('--validar-configuracion')

        self.assertIn('historial.parameters_configurados=True', salida)
        self.assertIn('historial.parameters_validos=False', salida)
        self.assertIn('historial.parameters_env_presente=True', salida)
        self.assertIn('historial.parameters_json_parseado=False', salida)
        self.assertIn('historial.parameters_cantidad=0', salida)
        self.assertIn('DATACREDITO_HDC_PARAMETERS_JSON no es JSON valido', salida)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_HDC_PARAMETERS_JSON='',
    )
    def test_validar_configuracion_reporta_parametros_hdc_ausentes(self):
        with patch.dict('os.environ', {'DATACREDITO_HDC_PARAMETERS_JSON': ''}):
            salida = self._ejecutar('--validar-configuracion')

        self.assertIn('historial.parameters_configurados=False', salida)
        self.assertIn('historial.parameters_validos=True', salida)
        self.assertIn('historial.parameters_env_presente=False', salida)
        self.assertIn('historial.parameters_json_parseado=False', salida)
        self.assertIn('historial.parameters_cantidad=0', salida)

    @override_settings(
        DATACREDITO_REAL_ENABLED=False,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_DECISOR_CLIENT_ID='cliente-decisor',
        DATACREDITO_DECISOR_CLIENT_SECRET='secreto-decisor',
        DATACREDITO_DECISOR_USERNAME='usuario-decisor',
        DATACREDITO_DECISOR_PASSWORD='clave-decisor',
        DATACREDITO_HDC_CLIENT_ID='cliente-hdc',
        DATACREDITO_HDC_CLIENT_SECRET='secreto-hdc',
        DATACREDITO_HDC_USERNAME='usuario-hdc',
        DATACREDITO_HDC_PASSWORD='clave-hdc',
        DATACREDITO_HDC_SERVICE_USER='usuario-servicio',
        DATACREDITO_HDC_SERVICE_PASSWORD='clave-servicio',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
        DATACREDITO_HDC_PARAMETERS_JSON='',
    )
    @patch('integrations.datacredito.auth.generar_token')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.historial_client.consultar_historial_credito')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_mostrar_request_sanitizado_no_consume_ni_expone_pii(
        self,
        decisor_mock,
        historial_mock,
        generar_token_mock,
    ):
        with patch.dict('os.environ', {'DATACREDITO_HDC_PARAMETERS_JSON': ''}):
            salida = self._ejecutar('--servicio', 'ambos', '--mostrar-request-sanitizado', '--json')

        self.assertIn('"dry_run_request": true', salida)
        self.assertIn('"documento_enmascarado": "******7890"', salida)
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('KENT', salida)
        self.assertNotIn('secreto-decisor', salida)
        self.assertNotIn('clave-servicio', salida)
        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()
        generar_token_mock.assert_not_called()

    @override_settings(DATACREDITO_REAL_ENABLED=False, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_datacredito_real_deshabilitado_no_llama_proveedor(self, decisor_mock):
        salida = self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real')

        self.assertIn('DataCredito real no esta habilitado', salida)
        decisor_mock.assert_not_called()

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='prod')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_en_prod_aborta(self, decisor_mock):
        salida = self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real')

        self.assertIn('Este comando solo esta permitido en UAT', salida)
        decisor_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_CLIENT_SECRET='secreto-super-privado',
        DATACREDITO_PASSWORD='password-super-privado',
    )
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.historial_client.consultar_historial_credito')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_con_mocks_imprime_resumen_sanitizado(self, decisor_mock, historial_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={
                'responseCode': '13',
                'score': 810,
                'viabilidad': 'APROBADO',
                'montoSugerido': 9000000,
                'saldoMora': 0,
                'ratingRecaudos': 'A',
            },
        )
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'scoreCrediticio': 720, 'comportamientoPago': 'NNN'},
        )

        salida = self._ejecutar('--servicio', 'ambos', '--confirmar-consumo-real')

        self.assertIn('servicio=decisor', salida)
        self.assertIn('servicio=historial', salida)
        self.assertIn('score=810', salida)
        self.assertIn('monto_sugerido=9000000', salida)
        self.assertIn('rating_recaudos=A', salida)
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('secreto-super-privado', salida)
        self.assertNotIn('password-super-privado', salida)
        self.assertNotIn('access_token', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_salida_json_sanitizada(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'score': 800},
        )

        salida = self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real', '--json')

        self.assertIn('"ejecutado": true', salida)
        self.assertIn('"servicio": "decisor"', salida)
        self.assertNotIn('1234567890', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.historial_client.consultar_historial_credito')
    def test_historial_diagnostico_incluye_estructura_segura(self, historial_mock):
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado=cargar_fixture_datacredito('historial_codigo_13_documentacion.json'),
        )

        salida = self._ejecutar(
            '--servicio',
            'historial',
            '--confirmar-consumo-real',
            '--json',
            '--diagnostico-detallado',
        )
        resultado = json.loads(salida)['resultados'][0]
        estructura = resultado['hdc_estructura']

        self.assertTrue(estructura['has_ReportHDCplus'])
        self.assertEqual(estructura['models_detectados'], 1)
        self.assertEqual(estructura['obligations_detectadas'], 1)
        self.assertTrue(estructura['payment_behavior_detectado'])
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('KENT', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.historial_client.consultar_historial_credito')
    def test_historial_diagnostico_detallado_incluye_senales_hdc(self, historial_mock):
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado=cargar_fixture_datacredito('hdcplus_sample_sanitized.json'),
        )

        salida = self._ejecutar(
            '--servicio',
            'historial',
            '--confirmar-consumo-real',
            '--json',
            '--diagnostico-detallado',
        )
        resultado = json.loads(salida)['resultados'][0]

        self.assertEqual(resultado['hdc_total_liabilities'], 7)
        self.assertEqual(resultado['hdc_liabilities_castigadas'], 3)
        self.assertEqual(resultado['hdc_liabilities_en_mora'], 5)
        self.assertEqual(resultado['hdc_saldo_total_hdc'], '12629000')
        self.assertEqual(resultado['hdc_saldo_mora_hdc'], '1853000')
        self.assertEqual(resultado['hdc_cuota_total_hdc'], '1127000')
        self.assertEqual(resultado['hdc_max_mora_dias'], 999)
        self.assertEqual(resultado['hdc_huellas_ultimos_6_meses'], 3)
        self.assertEqual(resultado['hdc_alertas_hdc'], 1)
        self.assertIn('SECTOR REAL', resultado['hdc_sectores_detectados'])
        self.assertNotIn('personIdNumber', salida)
        self.assertNotIn('accountNumber', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_decisor_sin_informacion_devuelve_diagnostico_normalizado(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )

        salida = self._ejecutar(
            '--servicio',
            'decisor',
            '--confirmar-consumo-real',
            '--json',
            '--diagnostico-detallado',
        )
        payload = json.loads(salida)
        resultado = payload['resultados'][0]

        self.assertEqual(resultado['http_status'], 200)
        self.assertEqual(resultado['codigo_funcional'], 'HC09_TX07')
        self.assertEqual(resultado['estado_normalizado'], ESTADO_IDENTIFICACION_NO_ENCONTRADA)
        self.assertTrue(resultado['proveedor_respondio'])
        self.assertTrue(resultado['consulta_procesada'])
        self.assertFalse(resultado['con_informacion'])
        self.assertFalse(resultado['utilizable_para_score'])
        self.assertIsNone(resultado['error'])
        self.assertIsNone(resultado['etapa_error'])
        self.assertNotIn('1234567890', salida)
        self.assertNotIn('KENT', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.normalizar_midecisor_pn')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_decisor_error_normalizacion_reporta_etapa(self, decisor_mock, normalizar_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        normalizar_mock.side_effect = RuntimeError('detalle interno sensible')

        salida = self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real', '--json', '--diagnostico-detallado')
        resultado = json.loads(salida)['resultados'][0]

        self.assertEqual(resultado['etapa_error'], 'NORMALIZACION')
        self.assertEqual(resultado['error_tipo'], 'ERROR_NORMALIZACION')
        self.assertEqual(resultado['http_status'], 200)
        self.assertEqual(resultado['codigo_funcional'], 'HC09_TX07')
        self.assertNotIn('detalle interno sensible', salida)

    @override_settings(DATACREDITO_REAL_ENABLED=True, DATACREDITO_ENVIRONMENT='uat')
    @patch('integrations.management.commands.diagnosticar_datacredito_uat.decisor_client.consultar_midecisor_persona_natural')
    def test_no_guarda_modelos_financieros(self, decisor_mock):
        from gestion_creditos.models import Credito, CreditoLibranza

        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            raw_sanitizado={'responseCode': '13', 'score': 800},
        )

        self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real')

        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
    )
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_usar_snapshot_reporta_reutilizacion(self, decisor_mock):
        decisor_mock.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='09',
            codigo_funcional='HC09_TX07',
            raw_sanitizado=cargar_fixture_datacredito('midecisor_pn_sin_informacion.json'),
        )
        self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real', '--usar-snapshot', '--json')
        decisor_mock.reset_mock()

        salida = self._ejecutar('--servicio', 'decisor', '--confirmar-consumo-real', '--usar-snapshot', '--json')
        resultado = json.loads(salida)['resultados'][0]

        self.assertTrue(resultado['reutilizado'])
        self.assertFalse(resultado['consultado_proveedor'])
        self.assertIsNotNone(resultado['snapshot_id'])
        decisor_mock.assert_not_called()
        self.assertNotIn('1234567890', salida)

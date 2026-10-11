from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from integrations.datacredito.decisor_client import (
    _buscar_response_code,
    _codigo_funcional_midecisor,
    consultar_midecisor_persona_natural,
)
from integrations.datacredito.dto import EntradaMiDecisor, ResultadoMiDecisorRawSeguro, TokenDatacredito
from integrations.datacredito.exceptions import DatacreditoProviderError
from integrations.datacredito.normalizadores import normalizar_midecisor_pn
from integrations.tests.test_datacredito_proxy import CONFIGURACION_HTTP_PRUEBA


def payload_pn(*, hc='13', tx='02', informacion=True):
    codigos = []
    if hc is not None:
        codigos.append({'clave': 'HC', 'valor': hc})
    if tx is not None:
        codigos.append({'clave': 'TX', 'valor': tx})
    return {
        'status': 'ACCEPTED',
        'content': {
            'status': '202 ACCEPTED',
            'infoTransaccion': {'codigosRespuesta': codigos},
            'respuesta': {
                'validacion': {'conInformacion': informacion},
                'informacionRiesgo': (
                    {'conInformacion': True, 'score': '853'} if informacion is True else {}
                ),
            },
        },
    }


class MiDecisorClasificacionTest(SimpleTestCase):
    def setUp(self):
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_tx_01_a_08_con_informacion(self):
        for tx in range(1, 9):
            with self.subTest(tx=tx):
                resultado = normalizar_midecisor_pn(payload_pn(tx=tx))
                self.assertEqual(resultado.estado, 'EXITOSA_CON_INFORMACION')
                self.assertTrue(resultado.disponible)
                self.assertEqual(resultado.score, 853)
                self.assertFalse(resultado.requiere_revision_manual)

    def test_tx_exitoso_sin_informacion_coherente(self):
        for tx in range(1, 9):
            with self.subTest(tx=tx):
                resultado = normalizar_midecisor_pn(payload_pn(tx=tx, informacion=False))
                self.assertEqual(resultado.estado, 'EXITOSA_SIN_INFORMACION')
                self.assertFalse(resultado.disponible)
                self.assertIsNone(resultado.score)

    def test_hc13_no_neutraliza_tx_09_a_16(self):
        for tx in range(9, 17):
            for informacion in (True, False, None):
                with self.subTest(tx=tx, informacion=informacion):
                    resultado = normalizar_midecisor_pn(payload_pn(tx=tx, informacion=informacion))
                    self.assertEqual(resultado.estado, 'CONSULTA_FALLIDA')
                    self.assertEqual(resultado.error_tipo, 'RESPUESTA_FUNCIONAL')
                    self.assertFalse(resultado.disponible)
                    self.assertIsNone(resultado.score)

    def test_tx_17_no_asume_alcance_pn(self):
        for informacion in (True, False, None):
            with self.subTest(informacion=informacion):
                resultado = normalizar_midecisor_pn(payload_pn(tx='17', informacion=informacion))
                self.assertEqual(resultado.estado, 'ALCANCE_PN_NO_CONFIRMADO')
                self.assertEqual(resultado.metadata_segura['error_codigo'], 'tx17_pn_no_confirmado')

    def test_tx_20_a_25_errores_explicitos(self):
        for tx in range(20, 26):
            for informacion in (True, False, None):
                with self.subTest(tx=tx, informacion=informacion):
                    resultado = normalizar_midecisor_pn(payload_pn(tx=tx, informacion=informacion))
                    self.assertEqual(resultado.estado, 'ERROR_TEMPORAL' if tx == 23 else 'SOLICITUD_INVALIDA')
                    self.assertEqual(resultado.error_tipo, 'ERROR_PROVEEDOR' if tx == 23 else 'ERROR_SOLICITUD')
                    self.assertTrue(resultado.requiere_revision_manual)
                    self.assertFalse(resultado.disponible)

    def test_tx_ausente_vacio_desconocido_o_invalido_no_habilita_exito(self):
        for tx in (None, '', ' ', '00', '18', '99', 'secreto-sintetico', True, [], '123456789'):
            with self.subTest(tx=tx):
                resultado = normalizar_midecisor_pn(payload_pn(tx=tx))
                self.assertEqual(resultado.estado, 'ERROR_TECNICO')
                self.assertFalse(resultado.disponible)

    def test_con_informacion_ausente_o_invalida(self):
        for informacion in (None, '', 'desconocido', [], {}):
            with self.subTest(informacion=informacion):
                resultado = normalizar_midecisor_pn(payload_pn(informacion=informacion))
                self.assertEqual(resultado.estado, 'ERROR_TECNICO')
        payload = payload_pn()
        del payload['content']['respuesta']['validacion']['conInformacion']
        self.assertEqual(normalizar_midecisor_pn(payload).estado, 'ERROR_TECNICO')

    def test_sin_informacion_con_datos_contradictorios_falla_cerrado(self):
        for modulo in (
            {'informacionRiesgo': {'score': '853'}},
            {'informacionRiesgo': {'conInformacion': True}},
            {'informacionRiesgo': {'montoSugerido': '1000000'}},
            {'informacionRiesgo': {'viabilidad': 'SI'}},
            {'informacionRiesgo': {'ratingRecaudos': 'A'}},
            {'endeudamiento': {'ingreso': '1000000'}},
            {'comportamientoCrediticio': {'indicadoresValores': {'conInformacion': True}}},
            {'comportamientoCrediticio': {'indicadoresValores': {'saldoActual': '100'}}},
        ):
            with self.subTest(modulo=modulo):
                payload = payload_pn(informacion=False)
                payload['content']['respuesta'].update(modulo)
                self.assertEqual(normalizar_midecisor_pn(payload).estado, 'RESPUESTA_INCONSISTENTE')

    def test_sin_informacion_con_modulos_vacios_y_ceros_es_coherente(self):
        payload = payload_pn(informacion=False)
        payload['content']['respuesta']['comportamientoCrediticio'] = {
            'conInformacion': False,
            'indicadoresValores': {'conInformacion': False, 'saldoActual': '0', 'creditosVigentes': '0'},
        }
        self.assertEqual(normalizar_midecisor_pn(payload).estado, 'EXITOSA_SIN_INFORMACION')

    def test_modulos_y_valores_malformados_fallan_cerrado(self):
        for informacion in (True, False):
            for modulo in (
                {'informacionRiesgo': []},
                {'informacionRiesgo': {'score': 'NaN'}},
                {'informacionRiesgo': {'score': 'Infinity'}},
                {'informacionRiesgo': {'score': 'invalido'}},
                {'comportamientoCrediticio': {'indicadoresValores': []}},
                {'comportamientoCrediticio': {'indicadoresValores': {'saldoActual': 'NaN'}}},
                {'comportamientoCrediticio': {'indicadoresValores': {'saldoActual': 'invalido'}}},
            ):
                with self.subTest(modulo=modulo, informacion=informacion):
                    payload = payload_pn(informacion=informacion)
                    payload['content']['respuesta'].update(modulo)
                    self.assertEqual(normalizar_midecisor_pn(payload).estado, 'ERROR_TECNICO')

    def test_envelope_no_acredita_exito_y_contradicciones_no_son_sin_informacion(self):
        for status, interno in (
            ('PRECONDITION_FAILED', ''), ('PRECONDITION_FAILED', '202 ACCEPTED'),
            (None, '202 ACCEPTED'), ('ACCEPTED', None), ('DESCONOCIDO', '202 ACCEPTED'),
        ):
            for informacion in (True, False):
                with self.subTest(status=status, interno=interno, informacion=informacion):
                    payload = payload_pn(informacion=informacion)
                    payload['status'] = status
                    payload['content']['status'] = interno
                    self.assertEqual(normalizar_midecisor_pn(payload).estado, 'RESPUESTA_INCONSISTENTE')

    def test_precondition_failed_tx20_no_es_sin_informacion(self):
        payload = payload_pn(hc=None, tx='20', informacion=False)
        payload['status'] = 'PRECONDITION_FAILED'
        payload['content']['status'] = ''
        resultado = normalizar_midecisor_pn(payload)
        self.assertEqual(resultado.estado, 'SOLICITUD_INVALIDA')
        self.assertEqual(_codigo_funcional_midecisor(payload), 'TX20')

    def test_normaliza_codigos_y_status_sin_cambiar_semantica(self):
        payload = payload_pn()
        payload['status'] = ' accepted '
        payload['content']['status'] = ' 202   accepted '
        payload['content']['infoTransaccion']['codigosRespuesta'] = [
            {'clave': ' hc ', 'valor': ' 13 '}, {'clave': ' tx ', 'valor': 2},
        ]
        self.assertEqual(normalizar_midecisor_pn(payload).estado, 'EXITOSA_CON_INFORMACION')
        self.assertEqual(_buscar_response_code(payload), '13')
        self.assertEqual(_codigo_funcional_midecisor(payload), 'HC13_TX02')

    def test_otros_hc_no_se_interpretan_sin_respaldo_pn(self):
        for hc in (None, '00', '09', '14'):
            with self.subTest(hc=hc):
                self.assertEqual(normalizar_midecisor_pn(payload_pn(hc=hc)).estado, 'ERROR_TECNICO')
        self.assertEqual(normalizar_midecisor_pn(payload_pn(hc='09', tx='07', informacion=False)).estado, 'ERROR_TECNICO')

    def test_hc14_tx05_pendiente_de_respaldo_contractual_pn(self):
        for informacion in (True, False, None):
            with self.subTest(informacion=informacion):
                payload = payload_pn(hc='14', tx='05', informacion=informacion)
                payload['content']['respuesta']['informacionRiesgo'] = {
                    'conInformacion': informacion,
                    'score': '853' if informacion is True else None,
                }
                resultado = normalizar_midecisor_pn(payload)
                self.assertEqual(resultado.estado, 'ERROR_TECNICO')
                self.assertEqual(resultado.metadata_segura['error_codigo'], 'respuesta_funcional_indeterminada')
                self.assertEqual(resultado.error_tipo, 'RESPUESTA_INVALIDA')
                self.assertEqual(resultado.metadata_segura['codigo_hc'], '14')
                self.assertEqual(resultado.metadata_segura['codigo_tx'], '05')
                self.assertFalse(resultado.disponible)
                self.assertTrue(resultado.requiere_revision_manual)
                self.assertIsNone(resultado.score)

    def test_hc14_con_informacion_no_neutraliza_tx_fallido(self):
        casos = [(str(tx), 'CONSULTA_FALLIDA') for tx in range(9, 17)] + [
            ('17', 'ALCANCE_PN_NO_CONFIRMADO'),
            ('20', 'SOLICITUD_INVALIDA'), ('21', 'SOLICITUD_INVALIDA'),
            ('22', 'SOLICITUD_INVALIDA'), ('23', 'ERROR_TEMPORAL'),
            ('24', 'SOLICITUD_INVALIDA'), ('25', 'SOLICITUD_INVALIDA'),
            (None, 'ERROR_TECNICO'), ('', 'ERROR_TECNICO'), ('99', 'ERROR_TECNICO'),
        ]
        for tx, estado in casos:
            with self.subTest(tx=tx):
                resultado = normalizar_midecisor_pn(payload_pn(hc='14', tx=tx))
                self.assertEqual(resultado.estado, estado)
                self.assertFalse(resultado.disponible)
                self.assertTrue(resultado.requiere_revision_manual)
                self.assertIsNone(resultado.score)

    def test_hc14_tx05_incompleto_o_contradictorio_no_habilita_informacion(self):
        casos = []
        for informacion in (False, None):
            payload = payload_pn(hc='14', tx='05', informacion=informacion)
            payload['content']['respuesta']['informacionRiesgo'] = {'conInformacion': True, 'score': '853'}
            casos.append(payload)
        for informacion in (False, None):
            payload = payload_pn(hc='14', tx='05')
            payload['content']['respuesta']['informacionRiesgo']['conInformacion'] = informacion
            casos.append(payload)
        for modulo in ('validacion', 'informacionRiesgo'):
            payload = payload_pn(hc='14', tx='05')
            del payload['content']['respuesta'][modulo]
            casos.append(payload)
        for score in (None, '', 'invalido', 'NaN', 'Infinity', '-2', '1001', '853.5'):
            payload = payload_pn(hc='14', tx='05')
            payload['content']['respuesta']['informacionRiesgo']['score'] = score
            casos.append(payload)
        payload = payload_pn(hc='14', tx='05')
        payload['status'] = 'PRECONDITION_FAILED'
        casos.append(payload)
        for index, payload in enumerate(casos):
            with self.subTest(caso=index):
                resultado = normalizar_midecisor_pn(payload)
                self.assertNotIn(resultado.estado, {'EXITOSA_CON_INFORMACION', 'EXITOSA_SIN_INFORMACION'})
                self.assertFalse(resultado.disponible)
                self.assertTrue(resultado.requiere_revision_manual)
                self.assertIsNone(resultado.score)

    def test_estructuras_invalidas_y_codigos_duplicados_no_lanzan_excepcion(self):
        casos = [None, [], {}, {'content': []}, {'content': {'infoTransaccion': []}}]
        for codigos in (None, {}, ['invalido'], [{'clave': 'TX', 'valor': '02'}, {'clave': 'TX', 'valor': '20'}]):
            payload = payload_pn()
            payload['content']['infoTransaccion']['codigosRespuesta'] = codigos
            casos.append(payload)
        for payload in casos:
            with self.subTest(payload=payload):
                self.assertEqual(normalizar_midecisor_pn(payload).estado, 'ERROR_TECNICO')
                _codigo_funcional_midecisor(payload)

    def test_codigos_no_conservan_textos_libres(self):
        payload = payload_pn(hc='dato-personal-sintetico', tx='token-sintetico')
        self.assertIsNone(_codigo_funcional_midecisor(payload))
        self.assertIsNone(_buscar_response_code(payload))
        resultado = normalizar_midecisor_pn(payload)
        self.assertNotIn('sintetico', str(resultado.metadata_segura))

    def test_score_fraccionario_ambiguo_o_fuera_de_limite_no_se_trunca(self):
        for score in ('667.9', '1000.1', '-2', '1001', '6,67', '1,000', True):
            with self.subTest(score=score):
                payload = payload_pn()
                payload['content']['respuesta']['informacionRiesgo']['score'] = score
                resultado = normalizar_midecisor_pn(payload)
                self.assertEqual(resultado.estado, 'ERROR_TECNICO')
                self.assertFalse(resultado.disponible)
                self.assertIsNone(resultado.score)
                self.assertTrue(resultado.requiere_revision_manual)

    def test_score_entero_normalizable_conserva_compatibilidad(self):
        for score in ('667', ' 667 ', 667, '667.0', 0, 1000):
            with self.subTest(score=score):
                payload = payload_pn()
                payload['content']['respuesta']['informacionRiesgo']['score'] = score
                resultado = normalizar_midecisor_pn(payload)
                self.assertEqual(resultado.estado, 'EXITOSA_CON_INFORMACION')
                self.assertEqual(resultado.score, int(float(score)))
                self.assertFalse(resultado.requiere_revision_manual)

    def test_score_ausente_o_sentinela_no_inventa_puntaje(self):
        for score in (None, '', '-', '-1', -1):
            with self.subTest(score=score):
                payload = payload_pn()
                payload['content']['respuesta']['informacionRiesgo']['score'] = score
                resultado = normalizar_midecisor_pn(payload)
                self.assertIsNone(resultado.score)
                self.assertTrue(resultado.requiere_revision_manual)

    def test_score_fuera_de_informacion_riesgo_no_es_fuente_crediticia(self):
        for clave in ('score', 'puntaje', 'scoreDecisor'):
            with self.subTest(clave=clave):
                payload = payload_pn()
                payload['content']['respuesta']['informacionRiesgo'] = {}
                payload['content']['respuesta']['sugerencias'] = {clave: '900'}
                resultado = normalizar_midecisor_pn(payload)
                self.assertIsNone(resultado.score)
                self.assertIsNone(resultado.score_midecisor)
                self.assertTrue(resultado.requiere_revision_manual)

    def test_modulo_sin_informacion_no_puede_aportar_datos_crediticios(self):
        for modulo, datos in (
            ('informacionRiesgo', {'score': '667'}),
            ('endeudamiento', {'ingreso': '5000000'}),
            ('comportamientoCrediticio', {'indicadoresValores': {'saldoMora': '50'}}),
        ):
            for flag in (False, None, 'true', 1):
                with self.subTest(modulo=modulo, flag=flag):
                    payload = payload_pn()
                    payload['content']['respuesta'][modulo] = {'conInformacion': flag, **datos}
                    resultado = normalizar_midecisor_pn(payload)
                    self.assertEqual(resultado.estado, 'ERROR_TECNICO')
                    self.assertFalse(resultado.disponible)
                    self.assertIsNone(resultado.score)

    def test_indicadores_sin_informacion_no_aportan_saldo(self):
        payload = payload_pn()
        payload['content']['respuesta']['comportamientoCrediticio'] = {
            'conInformacion': True,
            'indicadoresValores': {'conInformacion': False, 'saldoMora': '50'},
        }
        self.assertEqual(normalizar_midecisor_pn(payload).estado, 'ERROR_TECNICO')

    def test_comportamiento_sin_informacion_no_aporta_vector(self):
        payload = payload_pn()
        payload['content']['respuesta']['comportamientoCrediticio'] = {
            'conInformacion': True,
            'comportamientoPago': {
                'conInformacion': False,
                'vectorComportamiento': [{'comportamiento': '3'}],
            },
        }
        self.assertEqual(normalizar_midecisor_pn(payload).estado, 'ERROR_TECNICO')

    def test_submodulos_sin_informacion_con_null_siguen_siendo_validos(self):
        payload = payload_pn()
        payload['content']['respuesta'].update({
            'endeudamiento': {'conInformacion': False, 'ingreso': None},
            'comportamientoCrediticio': {
                'conInformacion': True,
                'indicadoresValores': {'conInformacion': False, 'saldoMora': None},
                'comportamientoPago': {'conInformacion': False, 'vectorComportamiento': None},
            },
        })
        resultado = normalizar_midecisor_pn(payload)
        self.assertEqual(resultado.estado, 'EXITOSA_CON_INFORMACION')
        self.assertEqual(resultado.score, 853)
        self.assertIsNone(resultado.saldo_mora)

    def test_adapter_no_entrega_score_invalido_al_motor(self):
        from contractors.datacredito.adapter import consultar_proveedor_datacredito_prestador

        solicitud = SimpleNamespace(apellidos='SINTETICO', tipo_documento='CC', numero_documento='000000')
        for score, flag in (('667.9', True), ('667', False)):
            with self.subTest(score=score, flag=flag):
                payload = payload_pn()
                payload['content']['respuesta']['informacionRiesgo'].update(score=score, conInformacion=flag)
                raw = ResultadoMiDecisorRawSeguro(
                    status_code=200, codigo_funcional='HC13_TX02', raw_sanitizado=payload,
                )
                with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural', return_value=raw):
                    resultado = consultar_proveedor_datacredito_prestador(solicitud, servicio='decisor')
                self.assertEqual(resultado.estado_snapshot, 'ERROR_PERMANENTE')
                self.assertIsNone(resultado.resultado_normalizado.score_externo)
                self.assertEqual(resultado.codigo_http, 200)
                self.assertEqual(resultado.codigo_funcional, 'HC13_TX02')
                self.assertEqual(resultado.error_tipo, 'RESPUESTA_INVALIDA')


@override_settings(**CONFIGURACION_HTTP_PRUEBA)
class MiDecisorClienteClasificacionTest(SimpleTestCase):
    def setUp(self):
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_http_200_y_202_conservan_transporte_y_clasificacion(self):
        for status in (200, 202):
            with self.subTest(status=status):
                session = Mock()
                session.post.return_value.status_code = status
                session.post.return_value.json.return_value = payload_pn()
                with patch('integrations.datacredito.decisor_client.obtener_token_cacheado', return_value=TokenDatacredito('token-sintetico')):
                    resultado = consultar_midecisor_persona_natural(EntradaMiDecisor('CC', '900000001', 'PRUEBA'), session=session)
                self.assertEqual(resultado.status_code, status)
                self.assertEqual(resultado.codigo_funcional, 'HC13_TX02')
                self.assertEqual(normalizar_midecisor_pn(resultado.raw_sanitizado).estado, 'EXITOSA_CON_INFORMACION')
                session.post.assert_called_once()

    def test_http_error_preserva_tx_sin_reintento(self):
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                session = Mock()
                session.post.return_value.status_code = status
                session.post.return_value.json.return_value = payload_pn(hc=None, tx='20', informacion=False)
                with patch('integrations.datacredito.decisor_client.obtener_token_cacheado', return_value=TokenDatacredito('token-sintetico')), patch('integrations.datacredito.decisor_client.invalidar_token') as invalidar:
                    with self.assertRaises(DatacreditoProviderError) as error:
                        consultar_midecisor_persona_natural(EntradaMiDecisor('CC', '900000001', 'PRUEBA'), session=session)
                self.assertEqual(error.exception.http_status, status)
                self.assertEqual(error.exception.codigo_funcional, 'TX20')
                self.assertEqual(invalidar.call_count, 1 if status == 401 else 0)
                session.post.assert_called_once()

    def test_http_error_sin_json_conserva_status(self):
        session = Mock()
        session.post.return_value.status_code = 503
        session.post.return_value.json.side_effect = ValueError('respuesta no JSON sintetica')
        with patch('integrations.datacredito.decisor_client.obtener_token_cacheado', return_value=TokenDatacredito('token-sintetico')):
            with self.assertRaises(DatacreditoProviderError) as error:
                consultar_midecisor_persona_natural(EntradaMiDecisor('CC', '900000001', 'PRUEBA'), session=session)
        self.assertEqual(error.exception.http_status, 503)
        self.assertIsNone(error.exception.codigo_funcional)
        session.post.assert_called_once()

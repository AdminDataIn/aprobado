from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from contractors.datacredito.adapter import consultar_proveedor_datacredito_prestador
from contractors.score.componentes import componente_datacredito
from integrations.datacredito.dto import ResultadoMiDecisorRawSeguro
from integrations.datacredito.midecisor_shadow import diagnosticar_midecisor_pn
from integrations.datacredito.normalizadores import normalizar_midecisor_pn


def fixture_hc14_tx05_sintetico():
    # No provider raw/identity. Debt/indicator values are invented test values.
    return {
        'status': 'ACCEPTED',
        'content': {
            'status': '202 ACCEPTED',
            'infoTransaccion': {'codigosRespuesta': [
                {'clave': 'HC', 'valor': '14'}, {'clave': 'TX', 'valor': '05'},
            ]},
            'respuesta': {
                'validacion': {'conInformacion': True},
                'informacionRiesgo': {
                    'conInformacion': True, 'score': '667', 'viabilidad': 'MEDIA',
                    'ratingRecaudos': 'C', 'montoSugerido': '3997902',
                    'alertas': [{'alerta': 'Alerta sintetica sin identidad'}],
                },
                'endeudamiento': {
                    'conInformacion': True, 'ingreso': '5000000',
                    'porcentajeCuotaVsIngreso': '10',
                },
                'comportamientoCrediticio': {
                    'conInformacion': True,
                    'indicadoresValores': {
                        'conInformacion': True, 'creditosVigentes': '2',
                        'creditosCerrados': '3', 'saldoActual': '2000000',
                        'valorCuota': '500000', 'saldoMora': '0',
                        'porcentajeDeuda': '20',
                    },
                    'comportamientoPago': {
                        'conInformacion': False, 'vectorComportamiento': None,
                    },
                    'evolucionDeuda': {'conInformacion': False, 'trimestres': None},
                },
                'sugerencias': {'conInformacion': True, 'vectorSugerencias': []},
            },
        },
    }


class MiDecisorShadowTest(SimpleTestCase):
    def setUp(self):
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido'))
        guard.start()
        self.addCleanup(guard.stop)
        self.payload = fixture_hc14_tx05_sintetico()
        self.respuesta = self.payload['content']['respuesta']

    def diagnosticar(self):
        return diagnosticar_midecisor_pn(self.payload, shadow=True)

    def test_shadow_debe_ser_explicito(self):
        for valor in (False, None, 1, 'true'):
            with self.subTest(valor=valor), self.assertRaisesMessage(ValueError, 'modo_shadow_explicito_requerido'):
                diagnosticar_midecisor_pn(self.payload, shadow=valor)
        with self.assertRaises(ValueError):
            diagnosticar_midecisor_pn(self.payload)

    def test_fixture_completo_extrae_sin_habilitar_decision(self):
        diagnostico = self.diagnosticar()
        candidato = diagnostico.candidato
        self.assertEqual(candidato.score_midecisor, 667)
        self.assertEqual(candidato.score_normalizado_0_1000, 667)
        self.assertEqual(candidato.viabilidad, 'MEDIA')
        self.assertEqual(candidato.rating_recaudo, 'C')
        self.assertEqual(candidato.monto_sugerido, 3997902)
        self.assertEqual(candidato.ingreso_estimado, Decimal('5000000'))
        self.assertEqual(candidato.porcentaje_cuota_vs_ingreso, Decimal('10'))
        self.assertEqual(candidato.saldo_actual, Decimal('2000000'))
        self.assertEqual(candidato.valor_cuota_total, Decimal('500000'))
        self.assertEqual(candidato.creditos_vigentes, 2)
        self.assertEqual(candidato.creditos_cerrados, 3)
        self.assertEqual(candidato.porcentaje_deuda, Decimal('20'))
        self.assertEqual(candidato.cantidad_alertas, 1)
        self.assertEqual(candidato.alertas_resumen, ('alertas_midecisor:1', 'alerta_no_clasificada'))
        self.assertTrue(candidato.requiere_revision_cumplimiento)
        self.assertEqual(candidato.estado, 'ERROR_TECNICO')
        self.assertEqual(candidato.error_tipo, 'RESPUESTA_INVALIDA')
        self.assertFalse(candidato.disponible)
        self.assertTrue(candidato.requiere_revision_manual)
        self.assertIsNone(candidato.viable)
        self.assertFalse(diagnostico.como_dict_seguro()['utilizable_para_decision'])
        self.assertEqual(diagnostico.como_dict_seguro()['validez_contractual_score'], 'NO_CONFIRMADA')

    def test_oficial_y_payload_permanecen_identicos(self):
        original = deepcopy(self.payload)
        antes = normalizar_midecisor_pn(self.payload)
        self.diagnosticar()
        self.assertEqual(self.payload, original)
        self.assertEqual(normalizar_midecisor_pn(self.payload), antes)
        self.assertEqual(antes.estado, 'ERROR_TECNICO')
        self.assertIsNone(antes.score)

    def test_no_es_input_del_score_prestadores(self):
        diagnostico = self.diagnosticar()
        politica = SimpleNamespace(peso_datacredito=Decimal('1'))
        for resultado in (diagnostico, diagnostico.candidato):
            componente = componente_datacredito(resultado, politica)
            self.assertFalse(componente.disponible)
            self.assertIsNone(componente.score)

    def test_adapter_productivo_sigue_error_sin_score(self):
        raw = ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional='HC14_TX05', raw_sanitizado=self.payload,
        )
        solicitud = SimpleNamespace(apellidos='SINTETICO', tipo_documento='CC', numero_documento='000000')
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural', return_value=raw) as cliente:
            self.diagnosticar()
            resultado = consultar_proveedor_datacredito_prestador(solicitud, servicio='decisor')
        cliente.assert_called_once()
        self.assertEqual(resultado.estado_snapshot, 'ERROR_PERMANENTE')
        self.assertIsNone(resultado.resultado_normalizado.score_externo)
        self.assertEqual(resultado.codigo_http, 200)
        self.assertEqual(resultado.codigo_funcional, 'HC14_TX05')

    def test_tx_no_candidato_nunca_extrae(self):
        for tx in ('01', '02', '08', '09', '16', '17', '20', '21', '22', '23', '24', '25', None, '', '99'):
            with self.subTest(tx=tx):
                self.payload['content']['infoTransaccion']['codigosRespuesta'][1]['valor'] = tx
                diagnostico = self.diagnosticar()
                self.assertIsNone(diagnostico.candidato)
                if tx == '23':
                    self.assertEqual(diagnostico.estado_oficial, 'ERROR_TEMPORAL')

    def test_no_generaliza_a_otros_hc(self):
        for hc in ('13', '09', '99', None):
            with self.subTest(hc=hc):
                self.payload['content']['infoTransaccion']['codigosRespuesta'][0]['valor'] = hc
                self.assertIsNone(self.diagnosticar().candidato)

    def test_con_informacion_false_none_y_ausente(self):
        for modulo in ('validacion', 'informacionRiesgo'):
            for valor in (False, None, '', 'true'):
                with self.subTest(modulo=modulo, valor=valor):
                    payload = fixture_hc14_tx05_sintetico()
                    payload['content']['respuesta'][modulo]['conInformacion'] = valor
                    self.assertIsNone(diagnosticar_midecisor_pn(payload, shadow=True).candidato)
            payload = fixture_hc14_tx05_sintetico()
            del payload['content']['respuesta'][modulo]['conInformacion']
            self.assertIsNone(diagnosticar_midecisor_pn(payload, shadow=True).candidato)

    def test_sin_informacion_no_infiere_score(self):
        self.payload['content']['respuesta'] = {
            'validacion': {'conInformacion': False},
            'informacionRiesgo': {'conInformacion': False, 'score': None},
        }
        self.assertIsNone(self.diagnosticar().candidato)

    def test_scores_invalidos_sentinela_y_fraccionarios(self):
        for valor in (None, '', '-', '-1', -1, 'null', 'None', 'NaN', 'Infinity', 'no-numero',
                      '667.5', '1001', '-2', '6,67', '6,6,7', True, [], {}):
            with self.subTest(valor=valor):
                self.respuesta['informacionRiesgo']['score'] = valor
                self.assertIsNone(self.diagnosticar().candidato)

    def test_rango_interno_no_equivale_a_validez_contractual(self):
        for valor in ('0', '667', '1000', '667.0'):
            with self.subTest(valor=valor):
                self.respuesta['informacionRiesgo']['score'] = valor
                diagnostico = self.diagnosticar()
                self.assertEqual(diagnostico.candidato.score, int(Decimal(valor)))
                self.assertEqual(diagnostico.como_dict_seguro()['validez_contractual_score'], 'NO_CONFIRMADA')

    def test_envelope_incoherente_bloqueado(self):
        for status, content in (('REJECTED', '202 ACCEPTED'), ('ACCEPTED', '200 OK'), (None, None)):
            with self.subTest(status=status, content=content):
                self.payload['status'] = status
                self.payload['content']['status'] = content
                self.assertIsNone(self.diagnosticar().candidato)

    def test_codigos_y_categorias_normalizan_espacios_y_mayusculas(self):
        self.payload['status'] = ' accepted '
        self.payload['content']['status'] = ' 202   accepted '
        self.payload['content']['infoTransaccion']['codigosRespuesta'] = [
            {'clave': ' hc ', 'valor': ' 14 '}, {'clave': 'tx', 'valor': 5},
        ]
        self.respuesta['informacionRiesgo']['viabilidad'] = ' media '
        self.respuesta['informacionRiesgo']['ratingRecaudos'] = ' c '
        candidato = self.diagnosticar().candidato
        self.assertEqual(candidato.viabilidad, 'MEDIA')
        self.assertEqual(candidato.rating_recaudo, 'C')

    def test_submodulos_null_no_son_cero_ni_sin_mora(self):
        for modulo in ('endeudamiento', 'comportamientoCrediticio'):
            self.respuesta[modulo] = {'conInformacion': False}
        candidato = self.diagnosticar().candidato
        for nombre in ('ingreso_estimado', 'saldo_actual', 'saldo_mora', 'valor_cuota_total',
                       'creditos_vigentes', 'creditos_cerrados', 'mora_severa', 'mora_actual'):
            self.assertIsNone(getattr(candidato, nombre), nombre)

    def test_vector_null_sin_informacion_preserva_desconocido(self):
        candidato = self.diagnosticar().candidato
        self.assertIsNone(candidato.mora_severa)
        self.assertIsNone(candidato.mora_actual)

    def test_vector_informado_reutiliza_detector_existente(self):
        self.respuesta['comportamientoCrediticio']['comportamientoPago'] = {
            'conInformacion': True, 'vectorComportamiento': [{'comportamiento': '3'}],
        }
        self.assertTrue(self.diagnosticar().candidato.mora_severa)

    def test_modulos_false_con_datos_o_flag_hijo_true_son_contradictorios(self):
        for modulo in (
            {'conInformacion': False, 'vectorComportamiento': [{'comportamiento': 'N'}]},
            {'conInformacion': None, 'vectorComportamiento': [{'comportamiento': 'N'}]},
            {'conInformacion': False, 'hijo': {'conInformacion': True}},
        ):
            with self.subTest(modulo=modulo):
                self.respuesta['comportamientoCrediticio']['comportamientoPago'] = modulo
                self.assertIsNone(self.diagnosticar().candidato)

    def test_campos_opcionales_ausentes_y_sentinelas_no_se_inventan(self):
        riesgo = self.respuesta['informacionRiesgo']
        for clave in ('viabilidad', 'ratingRecaudos', 'montoSugerido', 'alertas'):
            riesgo.pop(clave)
        self.respuesta.pop('endeudamiento')
        self.respuesta.pop('comportamientoCrediticio')
        candidato = self.diagnosticar().candidato
        for nombre in ('viabilidad', 'rating_recaudo', 'monto_sugerido', 'ingreso_estimado'):
            self.assertIsNone(getattr(candidato, nombre))
        self.assertEqual(candidato.alertas_resumen, ())
        diagnostico = self.diagnosticar()
        self.assertFalse(diagnostico.alertas_disponibles)
        self.assertIsNone(diagnostico.como_dict_seguro()['campos_candidatos']['cantidad_alertas'])
        riesgo.update(viabilidad='-', ratingRecaudos='null', montoSugerido='-1')
        candidato = self.diagnosticar().candidato
        self.assertIsNone(candidato.monto_sugerido)
        self.assertIsNone(candidato.rating_recaudo)

    def test_alertas_vacias_son_distintas_de_ausentes(self):
        self.respuesta['informacionRiesgo']['alertas'] = []
        diagnostico = self.diagnosticar()
        self.assertTrue(diagnostico.alertas_disponibles)
        self.assertEqual(diagnostico.como_dict_seguro()['campos_candidatos']['cantidad_alertas'], 0)

    def test_numeros_y_categorias_invalidas_rechazados(self):
        for clave, valor in (('montoSugerido', '1.5'), ('montoSugerido', 'NaN'),
                             ('viabilidad', 'APROBADO'), ('ratingRecaudos', 'libre'), ('alertas', {})):
            with self.subTest(clave=clave, valor=valor):
                payload = fixture_hc14_tx05_sintetico()
                payload['content']['respuesta']['informacionRiesgo'][clave] = valor
                self.assertIsNone(diagnosticar_midecisor_pn(payload, shadow=True).candidato)
        self.respuesta['endeudamiento']['porcentajeCuotaVsIngreso'] = 'Infinity'
        self.assertIsNone(self.diagnosticar().candidato)

    def test_estructuras_incompletas_o_invalidas_rechazadas(self):
        for payload in ({}, [], None, {'content': []}, {'content': {'respuesta': []}}):
            with self.subTest(payload=payload):
                self.assertIsNone(diagnosticar_midecisor_pn(payload, shadow=True).candidato)
        self.respuesta['comportamientoCrediticio']['comportamientoPago'] = []
        self.assertIsNone(self.diagnosticar().candidato)

    def test_salida_no_retiene_raw_pii_secretos_ni_mensajes_libres(self):
        marcador = 'NO_RETENER_SINTETICO'
        self.payload.update(documento=marcador, token=marcador, headers={'Authorization': marcador})
        self.payload['content']['infoTransaccion']['msjExcepcion'] = marcador
        self.respuesta['validacion']['nombres'] = marcador
        self.respuesta['informacionRiesgo']['txtProbabilidad'] = marcador
        self.respuesta['informacionRiesgo']['alertas'] = [{'alerta': marcador}]
        diagnostico = self.diagnosticar()
        self.assertIsNotNone(diagnostico.candidato)
        self.assertIsNone(diagnostico.candidato.descripcion_respuesta)
        self.assertNotIn(marcador, repr(diagnostico))
        self.assertNotIn(marcador, json.dumps(diagnostico.como_dict_seguro(), default=str))
        self.assertNotIn(marcador, json.dumps(diagnostico.candidato.como_dict(), default=str))

    def test_salida_y_repeticion_no_tienen_efectos(self):
        with patch('logging.Logger._log') as log:
            primero = self.diagnosticar()
            segundo = self.diagnosticar()
        self.assertEqual(primero, segundo)
        log.assert_not_called()

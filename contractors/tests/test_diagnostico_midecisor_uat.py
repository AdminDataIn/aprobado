"""Synthetic financial diagnostics. No response or score belongs to UAT #9."""
from copy import deepcopy
from dataclasses import replace
import json
import logging
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador
from contractors.models import (
    AprobacionInternaPrestador, AprobacionPagadorPrestador, ContractorApplication, PredecisionPrestadorAudit,
)
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from diagnosticos.uat9_shadow import (
    OperacionBloqueada, entorno_diagnostico, evaluar_candidato_sintetico, inspeccionar_uat9,
)
from gestion_creditos.models import Credito
from integrations.datacredito.normalizadores import normalizar_historial_credito, normalizar_midecisor_pn
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from integrations.tests.test_midecisor_shadow import fixture_hc14_tx05_sintetico


@override_settings(
    **CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
    SECURE_SSL_REDIRECT=False, CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class DiagnosticoMiDecisorUATTest(FixturePipelineFinanciero, TestCase):
    def setUp(self):
        super().setUp()
        self.payload = fixture_hc14_tx05_sintetico()
        self.payload['content']['respuesta']['informacionRiesgo']['alertas'] = []
        hdc = normalizar_historial_credito(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())
        self.historial = ResultadoConsultaDatacreditoPrestador(
            estado='EXITOSO', servicio='historial', snapshot_id=str(uuid4()),
            resultado_normalizado=_proyectar_resultado_allowlist(hdc, servicio='historial'),
        )

    def evaluar(self, *, historial=None, payload=None):
        antes = self.estado_bd()
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito') as hdc:
            resultado = evaluar_candidato_sintetico(
                solicitud=self.solicitud, politica=self.politica,
                raw_midecisor=self.payload if payload is None else payload,
                historial=historial or self.historial,
            )
        decisor.assert_not_called()
        hdc.assert_not_called()
        self.assertEqual(self.estado_bd(), antes)
        self.assertFalse(resultado['elegibilidad_crediticia_final'])
        self.assertEqual(resultado['llamadas'], {
            'red_bloqueada': 0, 'publicaciones_bloqueadas': 0, 'sql_bloqueado': 0,
        })
        self.assertTrue(resultado['rollback_obligatorio'])
        return resultado

    def estado_bd(self):
        self.solicitud.refresh_from_db()
        return {
            'solicitud': dict(self.solicitud.__dict__),
            'auditorias': list(PredecisionPrestadorAudit.objects.values()),
            'snapshots': list(ConsultaDatacreditoSnapshot.objects.values()),
            'gates': list(AprobacionInternaPrestador.objects.values()),
            'pagadores': list(AprobacionPagadorPrestador.objects.values()),
            'creditos': Credito.objects.count(),
        }

    def test_candidato_coherente_usa_motor_real_sin_habilitar_decision_oficial(self):
        anterior = deepcopy(self.payload)
        resultado = self.evaluar()
        self.assertTrue(resultado['candidato_utilizable'])
        self.assertTrue(resultado['score_completo_calculado'])
        self.assertTrue(resultado['capacidad_calculable'])
        self.assertEqual(resultado['riesgo'], 'PREAPROBADO_READ_ONLY')
        self.assertEqual(resultado['oferta'], 'OFERTA_CALCULADA')
        self.assertEqual(resultado['estado_oficial'], 'ERROR_TECNICO')
        self.assertEqual(self.payload, anterior)
        self.assertIsNone(normalizar_midecisor_pn(self.payload).score)

    def test_hdc_sin_informacion_no_inventa_deuda_ni_score_completo(self):
        historial = replace(self.historial, estado='SIN_INFORMACION', resultado_normalizado=None)
        resultado = self.evaluar(historial=historial)
        self.assertTrue(resultado['candidato_utilizable'])
        self.assertIn('datacredito_score', resultado['componentes_disponibles'])
        self.assertFalse(resultado['carga_hdc_disponible'])
        self.assertFalse(resultado['capacidad_calculable'])
        self.assertFalse(resultado['score_completo_calculado'])
        self.assertEqual(resultado['riesgo'], 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(resultado['oferta'], 'NO_EVALUABLE')

    def test_sin_informacion_no_usa_dto_de_deuda_adyacente(self):
        resultado = self.evaluar(historial=replace(self.historial, estado='SIN_INFORMACION'))
        self.assertFalse(resultado['carga_hdc_disponible'])

    def test_sin_score_contradicciones_y_fuera_de_rango_rechazados(self):
        for valor in (None, '', '1001', '-1', '667.9', True):
            payload = deepcopy(self.payload)
            payload['content']['respuesta']['informacionRiesgo']['score'] = valor
            with self.subTest(valor=valor):
                resultado = self.evaluar(payload=payload)
                self.assertFalse(resultado['candidato_utilizable'])
                self.assertEqual(resultado['oferta'], 'NO_EVALUABLE')
        for modulo in ('validacion', 'informacionRiesgo', 'endeudamiento'):
            payload = deepcopy(self.payload)
            payload['content']['respuesta'][modulo]['conInformacion'] = False
            with self.subTest(modulo=modulo):
                self.assertFalse(self.evaluar(payload=payload)['candidato_utilizable'])

    def test_tx_fallido_no_admite_candidato(self):
        for tx in ('09', '20', '23', None):
            payload = deepcopy(self.payload)
            payload['content']['infoTransaccion']['codigosRespuesta'][1]['valor'] = tx
            with self.subTest(tx=tx):
                self.assertFalse(self.evaluar(payload=payload)['candidato_utilizable'])

    def test_capacidad_oferta_insuficiente_respeta_ingreso_verificado(self):
        self.verificar_ingreso('100000')
        resultado = self.evaluar()
        self.assertTrue(resultado['score_completo_calculado'])
        self.assertNotEqual(resultado['oferta'], 'OFERTA_CALCULADA')
        self.assertEqual(resultado['oferta'], 'SIN_OFERTA')

    def test_carga_hdc_alta_respeta_limite_de_capacidad_del_motor(self):
        payload = deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())
        for item in payload['ReportHDCplus']['liabilities']:
            item['values'][0]['valueMonthlyPayment'] = '15000000'
        historial = replace(self.historial, resultado_normalizado=_proyectar_resultado_allowlist(
            normalizar_historial_credito(payload), servicio='historial',
        ))
        resultado = self.evaluar(historial=historial)
        self.assertTrue(resultado['capacidad_calculable'])
        self.assertEqual(resultado['riesgo'], 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(resultado['oferta'], 'SIN_OFERTA')

    def test_sin_ingreso_evaluable_no_inventa_oferta(self):
        with patch('contractors.services.ingreso_evaluable.resolver_ingreso_evaluable', return_value=None):
            resultado = self.evaluar()
        self.assertFalse(resultado['ingreso_vigente'])
        self.assertEqual(resultado['oferta'], 'NO_EVALUABLE')

    def test_repeticion_no_duplica_y_no_expone_scores_personales(self):
        primero = self.evaluar()
        segundo = self.evaluar()
        self.assertEqual(primero, segundo)
        serializado = json.dumps(primero)
        for dato in ('667', '3997902', '5000000', 'raw_sanitizado', 'score_externo',
                     'numero_documento', 'access_token', self.solicitud.numero_documento):
            self.assertNotIn(dato, serializado)
        self.assertEqual(primero['tipo'], 'DIAGNOSTICO_SINTETICO_NO_PERSONAL')

    def test_rollback_descarta_on_commit_y_restauracion_flags(self):
        flags = (settings.DATACREDITO_ENABLED, settings.DATACREDITO_REAL_ENABLED)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with entorno_diagnostico():
                transaction.on_commit(lambda: self.fail('No debe ejecutarse on_commit'))
                self.assertFalse(settings.DATACREDITO_ENABLED)
                self.assertFalse(settings.DATACREDITO_REAL_ENABLED)
        self.assertEqual(callbacks, [])
        self.assertEqual((settings.DATACREDITO_ENABLED, settings.DATACREDITO_REAL_ENABLED), flags)

    def test_base_no_aislada_se_rechaza_antes_de_evaluar(self):
        with patch.dict(connection.settings_dict, NAME='base_operativa_no_permitida'):
            with self.assertRaisesMessage(OperacionBloqueada, 'se_requiere_base_sintetica_aislada'):
                evaluar_candidato_sintetico(
                    solicitud=self.solicitud, politica=self.politica,
                    raw_midecisor=self.payload, historial=self.historial,
                )

    def test_no_logs_y_restauracion_de_logging(self):
        nivel = logging.root.manager.disable
        with self.assertNoLogs('uat.diagnostico', level='WARNING'):
            with entorno_diagnostico():
                logging.getLogger('uat.diagnostico').warning('Evidencia sintetica que no debe registrarse')
        self.assertEqual(logging.root.manager.disable, nivel)

    def test_escritura_red_celery_y_correo_bloqueados(self):
        import requests
        from celery import current_app
        with entorno_diagnostico() as contadores:
            with self.assertRaises(OperacionBloqueada):
                requests.get('https://example.invalid')
            with self.assertRaises(OperacionBloqueada):
                current_app.send_task('tarea_sintetica')
            with self.assertRaises(OperacionBloqueada):
                EmailMessage('prueba', 'prueba', to=['test@example.invalid']).send()
            with self.assertRaises(OperacionBloqueada):
                ContractorApplication.objects.filter(pk=self.solicitud.pk).update(estado='ANULADA')
        self.assertEqual(contadores, {
            'red_bloqueada': 1, 'publicaciones_bloqueadas': 2, 'sql_bloqueado': 1,
        })

    def test_inspeccion_no_reconstruye_ni_evalua_desde_resumen_booleano(self):
        resultado = inspeccionar_uat9()
        self.assertFalse(resultado['evaluacion_ejecutada'])
        self.assertEqual(resultado['resultado'], 'BLOCKED_CONTEXTO_NO_VERIFICADO')

    def test_snapshot_error_no_es_custodia_criptografica_de_respuesta(self):
        from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
        from contractors.services.datacredito_evaluacion import construir_fingerprint_datacredito
        from integrations.datacredito.settings import obtener_configuracion_datacredito
        autorizacion = obtener_autorizacion_datacredito_vigente(self.solicitud)
        ingreso = self.solicitud.ingresos_netos_verificados.get()
        for servicio, pk in (('historial', '87dbae55-e1b9-41d3-80e6-5687e89d8f8c'),
                             ('decisor', 'edf4c084-7f7a-4dbc-bce6-06dcb420a8d5')):
            ConsultaDatacreditoSnapshot.objects.create(
                pk=pk, ambiente='uat', servicio=servicio, documento_hash='f' * 64,
                documento_enmascarado='***',
                fingerprint=construir_fingerprint_datacredito(
                    solicitud=self.solicitud, servicio=servicio, autorizacion=autorizacion,
                    configuracion=obtener_configuracion_datacredito(),
                ),
                estado='SIN_INFORMACION' if servicio == 'historial' else 'ERROR_PERMANENTE',
                resultado_normalizado={}, codigo_http=200,
                codigo_funcional='14' if servicio == 'historial' else 'HC14_TX05',
                consultado_en=timezone.now(), vigente_hasta=timezone.now(),
                autorizacion_referencia=str(autorizacion.pk), creado_por=self.staff,
            )
        antes = self.estado_bd()
        with patch.multiple('diagnosticos.uat9_shadow', SOLICITUD_ID=self.solicitud.pk,
                            PROPIETARIO_ID=self.usuario.pk, CONSENTIMIENTO_ID=autorizacion.pk,
                            INGRESO_ID=ingreso.pk):
            resultado = inspeccionar_uat9()
        self.assertEqual(resultado['resultado'], 'EVIDENCIA_REAL_NO_RECUPERABLE')
        self.assertTrue(all(resultado['controles'].values()))
        self.assertFalse(resultado['snapshots']['decisor']['normalizado_presente'])
        self.assertFalse(resultado['snapshots']['decisor']['hash_respuesta_conservado_por_modelo'])
        self.assertFalse(resultado['evaluacion_ejecutada'])
        self.assertEqual(self.estado_bd(), antes)

    def test_error_de_motor_conserva_rollback_y_flags(self):
        antes = self.estado_bd()
        flags = (settings.DATACREDITO_ENABLED, settings.DATACREDITO_REAL_ENABLED)
        with patch('diagnosticos.uat9_shadow.evaluar_score_prestador', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.evaluar()
        self.assertEqual(self.estado_bd(), antes)
        self.assertEqual((settings.DATACREDITO_ENABLED, settings.DATACREDITO_REAL_ENABLED), flags)

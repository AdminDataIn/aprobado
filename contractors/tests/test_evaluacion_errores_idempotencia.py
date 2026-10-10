"""Repeated reads of failed evaluations must not become new financial attempts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import PredecisionPrestadorAudit, RevisionManualPrestador
from contractors.services import datacredito_evaluacion, evaluacion_formal
from contractors.services.aprobacion_interna import crear_o_reutilizar_aprobacion_interna
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.revision_manual import reintentar_evaluacion
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from integrations.datacredito.dto import ResultadoHistorialCreditoRawSeguro, ResultadoMiDecisorRawSeguro
from integrations.datacredito.exceptions import DatacreditoTimeoutError
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from integrations.tests.test_midecisor_clasificacion import payload_pn


CONFIGURACION = {
    **CONFIGURACION_DATACREDITO_PRUEBA,
    'ALLOWED_HOSTS': ['testserver', 'localhost', 'contratistas.localhost'],
    'SECURE_SSL_REDIRECT': False,
    'CONTRACTORS_CONTRACT_AI_ENABLED': False,
    'OPENAI_API_KEY': '',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
}


class ComprobacionesRepeticion:
    def repetir_sin_consulta(self, auditoria, **kwargs):
        historicos = list(Snapshot.objects.order_by('pk').values())
        auditorias = list(PredecisionPrestadorAudit.objects.order_by('pk').values())
        with patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            resultado = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff, **kwargs,
            )
        proveedor.assert_not_called()
        self.assertTrue(resultado.reutilizada)
        self.assertEqual(resultado.auditoria.pk, auditoria.pk)
        self.assertEqual(auditorias, list(PredecisionPrestadorAudit.objects.order_by('pk').values()))
        self.assertEqual(historicos, list(Snapshot.objects.order_by('pk').values()))
        self.assert_sin_originacion()
        return resultado

    def evaluar_contexto_cambiado(self, anterior):
        historico = PredecisionPrestadorAudit.objects.values().get(pk=anterior.pk)
        fuentes = list(Snapshot.objects.order_by('pk').values())
        with override_settings(DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False), \
             patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            nueva = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff,
            )
        proveedor.assert_not_called()
        self.assertFalse(nueva.reutilizada)
        self.assertNotEqual(nueva.auditoria.pk, anterior.pk)
        self.assertEqual(nueva.auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(historico, PredecisionPrestadorAudit.objects.values().get(pk=anterior.pk))
        self.assertEqual(fuentes, list(Snapshot.objects.order_by('pk').values()))
        self.repetir_sin_consulta(nueva.auditoria)
        return nueva


@override_settings(**CONFIGURACION)
class EvaluacionErroresIdempotenciaTest(ComprobacionesRepeticion, FixturePipelineFinanciero, TestCase):
    def test_exito_repetido_conserva_evidencia_p1a_y_control_oferta_p0(self):
        self.verificar_ingreso('100000')
        auditoria = self.evaluar()
        self.assertEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(len(auditoria.snapshot_salida['usos_snapshots']), 2)
        self.assertEqual(self.oferta(auditoria).estado, 'SIN_OFERTA')
        self.repetir_sin_consulta(auditoria)
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)

    def test_error_transitorio_repetido_no_crea_auditoria_artificial(self):
        auditoria = self.evaluar(timeout=True)
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(auditoria.snapshot_midecisor.estado, 'ERROR_TRANSITORIO')
        self.assertEqual(auditoria.snapshot_midecisor.error_tipo, 'TIMEOUT')
        self.repetir_sin_consulta(auditoria)
        self.assert_no_aprueba(auditoria)

    def test_auditoria_legacy_sin_contexto_no_se_reescribe(self):
        auditoria = self.evaluar(timeout=True)
        entrada = dict(auditoria.snapshot_entrada)
        entrada.pop('contexto_consulta')
        # Recreate the legacy JSON shape only in the isolated test fixture.
        PredecisionPrestadorAudit.objects.filter(pk=auditoria.pk).update(snapshot_entrada=entrada)
        self.repetir_sin_consulta(auditoria)
        auditoria.refresh_from_db()
        self.assertNotIn('contexto_consulta', auditoria.snapshot_entrada)

    def test_fingerprint_modificado_durante_evaluacion_impide_cierre_favorable(self):
        finalizar = evaluacion_formal._finalizar_evaluacion

        def cerrar_con_contexto_distinto(**kwargs):
            with override_settings(DATACREDITO_DECISOR_CLIENT_ID='otra-cuenta-sintetica'):
                return finalizar(**kwargs)

        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                   return_value=ResultadoMiDecisorRawSeguro(
                       status_code=200, codigo_funcional='HC13_TX02', raw_sanitizado=payload_pn(),
                   )) as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito',
                   return_value=ResultadoHistorialCreditoRawSeguro(
                       status_code=200, response_code='13',
                       raw_sanitizado=hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc(),
                   )) as hdc, \
             patch.object(evaluacion_formal, '_finalizar_evaluacion',
                          side_effect=cerrar_con_contexto_distinto):
            resultado = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff,
            )
        decisor.assert_called_once()
        hdc.assert_called_once()
        self.assertEqual(resultado.auditoria.resultado, 'ERROR_CONTROLADO')
        self.assertEqual(resultado.auditoria.error_codigo, 'datos_modificados_durante_evaluacion')
        self.assertIsNone(resultado.auditoria.score)
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(resultado.auditoria, actor=self.staff)
        self.assert_sin_originacion()

    def test_error_permanente_preserva_http_tx_y_fallo_cerrado(self):
        auditoria = self.evaluar(tx='20')
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(auditoria.snapshot_midecisor.estado, 'ERROR_PERMANENTE')
        self.assertEqual(auditoria.snapshot_midecisor.codigo_http, 200)
        self.assertEqual(auditoria.snapshot_midecisor.codigo_funcional, 'HC13_TX20')
        self.assertEqual(auditoria.snapshot_midecisor.resultado_normalizado, {})
        self.assertIsNone(auditoria.score)
        self.repetir_sin_consulta(auditoria)
        self.assert_no_aprueba(auditoria)

    def test_hdc_sin_informacion_conserva_revision_manual(self):
        auditoria = self.evaluar(hdc_sin_informacion=True)
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(auditoria.snapshot_hdcplus.estado, 'SIN_INFORMACION')
        self.repetir_sin_consulta(auditoria)

    def test_hc14_tx05_no_conecta_shadow_ni_cambia_clasificacion(self):
        auditoria = self.evaluar(hc='14', tx='05', score=667)
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertIsNone(auditoria.score)
        self.assertEqual(auditoria.snapshot_midecisor.codigo_funcional, 'HC14_TX05')
        self.assertEqual(auditoria.snapshot_midecisor.resultado_normalizado, {})
        self.repetir_sin_consulta(auditoria)

    def test_retiro_consentimiento_no_reutiliza_evaluacion(self):
        anterior = self.evaluar(timeout=True)
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales'])
        nueva = self.evaluar_contexto_cambiado(anterior)
        self.assertIsNone(nueva.auditoria.snapshot_entrada['contexto_consulta']['autorizacion_id'])

    def test_nueva_version_consentimiento_no_reutiliza_evaluacion(self):
        anterior = self.evaluar(timeout=True)
        with patch('contractors.consentimiento_centrales.VERSION_CONSENTIMIENTO_CENTRALES', 'futura-v2'), \
             override_settings(DATACREDITO_AUTHORIZATION_TEXT_VERSION='futura-v2'):
            self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))
            self.evaluar_contexto_cambiado(anterior)

    def test_cambio_fingerprint_no_reutiliza_auditoria_error(self):
        anterior = self.evaluar(timeout=True)
        with override_settings(DATACREDITO_DECISOR_CLIENT_ID='otra-cuenta-sintetica'):
            nueva = self.evaluar_contexto_cambiado(anterior)
        self.assertNotEqual(
            nueva.auditoria.snapshot_entrada['contexto_consulta']['fingerprints']['decisor'],
            anterior.snapshot_entrada['contexto_consulta']['fingerprints']['decisor'],
        )

    def test_fingerprint_cambiado_tampoco_reutiliza_preaprobacion(self):
        anterior = self.evaluar()
        with override_settings(DATACREDITO_DECISOR_CLIENT_ID='otra-cuenta-sintetica'):
            self.evaluar_contexto_cambiado(anterior)

    def test_evidencia_financiera_cambiada_exige_nueva_evaluacion(self):
        anterior = self.evaluar(timeout=True)
        self.verificar_ingreso('100000')
        nueva = self.evaluar_contexto_cambiado(anterior)
        self.assertNotEqual(anterior.version_datos, nueva.auditoria.version_datos)

    def test_version_politica_cambiada_no_reutiliza_resultado(self):
        anterior = self.evaluar(timeout=True)
        self.politica.version_politica = 'politica-sintetica-v2'
        self.politica.save(update_fields=['version_politica'])
        nueva = self.evaluar_contexto_cambiado(anterior)
        self.assertNotEqual(anterior.version_politica, nueva.auditoria.version_politica)

    def test_nueva_solicitud_no_reutiliza_auditoria_de_la_anterior(self):
        anterior = self.evaluar(timeout=True)
        otra = self.nueva_solicitud_autorizada()
        self.solicitud = otra
        nueva = self.evaluar_contexto_cambiado(anterior)
        self.assertEqual(nueva.auditoria.solicitud_id, otra.pk)

    def test_reintento_operativo_autorizado_registra_intento_sin_repetir_http(self):
        anterior = self.evaluar(timeout=True)
        historico = PredecisionPrestadorAudit.objects.values().get(pk=anterior.pk)
        fuentes = list(Snapshot.objects.order_by('pk').values())
        revision = RevisionManualPrestador.objects.filter(solicitud=self.solicitud).first()
        with patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            nueva = reintentar_evaluacion(revision, actor=self.staff)
        proveedor.assert_not_called()
        self.assertNotEqual(nueva.auditoria.pk, anterior.pk)
        self.assertEqual(nueva.auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 2)
        self.assertEqual(historico, PredecisionPrestadorAudit.objects.values().get(pk=anterior.pk))
        self.assertEqual(fuentes, list(Snapshot.objects.order_by('pk').values()))
        self.repetir_sin_consulta(nueva.auditoria)

    def test_reconsulta_tecnica_autorizada_preserva_origen_y_historico(self):
        anterior = self.evaluar(timeout=True)
        historicos = list(Snapshot.objects.order_by('pk').values())
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                   return_value=ResultadoMiDecisorRawSeguro(
                       status_code=200, codigo_funcional='HC13_TX02', raw_sanitizado=payload_pn(),
                   )) as proveedor:
            nueva_fuente = datacredito_evaluacion.obtener_evaluacion_datacredito_prestador(
                self.solicitud, servicio='decisor', modo='FORZAR_CONSULTA',
                solicitado_por=self.staff, justificacion='Reconsulta sintetica autorizada',
            )
        proveedor.assert_called_once()
        self.assertEqual(nueva_fuente.estado, 'EXITOSO')
        with patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            nueva = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff, nuevo_intento=True,
            )
        proveedor.assert_not_called()
        self.assertNotEqual(nueva.auditoria.pk, anterior.pk)
        self.assertEqual(str(nueva.auditoria.snapshot_midecisor_id), nueva_fuente.snapshot_id)
        self.assertEqual(historicos, list(Snapshot.objects.filter(
            pk__in=[s['id'] for s in historicos],
        ).order_by('pk').values()))
        self.repetir_sin_consulta(nueva.auditoria)

    def test_forzar_consulta_repite_intento_fallido_no_lo_convierte_en_lectura(self):
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                   side_effect=DatacreditoTimeoutError('Timeout sintetico')) as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito',
                   return_value=ResultadoHistorialCreditoRawSeguro(
                       status_code=200, response_code='13',
                       raw_sanitizado=hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc(),
                   )) as hdc:
            anterior = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff, modo_datacredito='FORZAR_CONSULTA',
                justificacion='Reconsulta tecnica sintetica autorizada',
            )
            historico = PredecisionPrestadorAudit.objects.values().get(pk=anterior.auditoria.pk)
            fuentes = list(Snapshot.objects.order_by('pk').values())
            nueva = evaluacion_formal.evaluar_solicitud_prestador(
                self.solicitud, solicitado_por=self.staff, modo_datacredito='FORZAR_CONSULTA',
                justificacion='Nuevo reintento tecnico sintetico autorizado',
            )
        self.assertEqual(decisor.call_count, 2)
        self.assertEqual(hdc.call_count, 2)
        self.assertFalse(nueva.reutilizada)
        self.assertNotEqual(nueva.auditoria.pk, anterior.auditoria.pk)
        self.assertEqual(nueva.auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 2)
        self.assertEqual(Snapshot.objects.count(), 4)
        self.assertEqual(historico, PredecisionPrestadorAudit.objects.values().get(pk=anterior.auditoria.pk))
        self.assertEqual(fuentes, list(Snapshot.objects.filter(
            pk__in=[s['id'] for s in fuentes],
        ).order_by('pk').values()))
        self.assert_sin_originacion()

    def test_actor_sin_permiso_no_repite_ni_fuerza_consulta(self):
        anterior = self.evaluar(timeout=True)
        with patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            with self.assertRaises(PermissionDenied):
                evaluacion_formal.evaluar_solicitud_prestador(
                    self.solicitud, solicitado_por=self.usuario,
                    modo_datacredito='FORZAR_CONSULTA', nuevo_intento=True,
                    justificacion='Intento no autorizado sintetico',
                )
        proveedor.assert_not_called()
        self.repetir_sin_consulta(anterior)

    def test_error_no_oculta_vencimiento_real_de_otra_fuente(self):
        anterior = self.evaluar(timeout=True)
        Snapshot.objects.filter(pk=anterior.snapshot_hdcplus_id).update(
            vigente_hasta=timezone.now() - timedelta(seconds=1),
        )
        fuentes = list(Snapshot.objects.order_by('pk').values())
        with patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor:
            nueva = evaluacion_formal.evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        proveedor.assert_not_called()
        self.assertNotEqual(nueva.auditoria.pk, anterior.pk)
        self.assertEqual(nueva.auditoria.error_codigo, 'fuentes_evaluacion_vencidas')
        self.assertEqual(nueva.auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(fuentes, list(Snapshot.objects.order_by('pk').values()))
        self.repetir_sin_consulta(nueva.auditoria)


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
@override_settings(**CONFIGURACION)
class EvaluacionErrorConcurrenciaPostgresTest(ComprobacionesRepeticion, FixturePipelineFinanciero,
                                            TransactionTestCase):
    def test_doble_evaluacion_inicial_en_error_crea_un_solo_intento(self):
        barrera = threading.Barrier(2)
        iniciar = evaluacion_formal._iniciar_evaluacion

        def inicio_simultaneo(**kwargs):
            barrera.wait(timeout=20)
            return iniciar(**kwargs)

        def evaluar():
            close_old_connections()
            try:
                return evaluacion_formal.evaluar_solicitud_prestador(
                    self.solicitud, solicitado_por=self.staff,
                )
            finally:
                connections.close_all()

        with patch.object(evaluacion_formal, '_iniciar_evaluacion', side_effect=inicio_simultaneo), \
             patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                   side_effect=DatacreditoTimeoutError('Timeout sintetico')) as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito',
                   return_value=ResultadoHistorialCreditoRawSeguro(
                       status_code=200, response_code='13',
                       raw_sanitizado=hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc(),
                   )) as hdc, ThreadPoolExecutor(max_workers=2) as pool:
            resultados = [f.result(timeout=40) for f in [pool.submit(evaluar) for _ in range(2)]]
        decisor.assert_called_once()
        hdc.assert_called_once()
        self.assertEqual(len({r.auditoria.pk for r in resultados}), 1)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)
        auditoria = PredecisionPrestadorAudit.objects.get()
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(auditoria.snapshot_midecisor.estado, 'ERROR_TRANSITORIO')
        self.assertEqual(Snapshot.objects.count(), 2)
        self.repetir_sin_consulta(auditoria)

    def test_doble_lectura_con_error_no_crea_auditorias_ni_consultas(self):
        anterior = self.evaluar(timeout=True)
        historicos = list(Snapshot.objects.order_by('pk').values())
        auditorias = list(PredecisionPrestadorAudit.objects.order_by('pk').values())
        barrera = threading.Barrier(2)
        iniciar = evaluacion_formal._iniciar_evaluacion

        def inicio_simultaneo(**kwargs):
            barrera.wait(timeout=20)
            return iniciar(**kwargs)

        def evaluar():
            close_old_connections()
            try:
                return evaluacion_formal.evaluar_solicitud_prestador(
                    self.solicitud, solicitado_por=self.staff,
                )
            finally:
                connections.close_all()

        with patch.object(evaluacion_formal, '_iniciar_evaluacion', side_effect=inicio_simultaneo), \
             patch.object(datacredito_evaluacion, 'consultar_proveedor_datacredito_prestador') as proveedor, \
             ThreadPoolExecutor(max_workers=2) as pool:
            resultados = [f.result(timeout=40) for f in [pool.submit(evaluar) for _ in range(2)]]
        proveedor.assert_not_called()
        self.assertTrue(all(r.reutilizada and r.auditoria.pk == anterior.pk for r in resultados))
        self.assertEqual(auditorias, list(PredecisionPrestadorAudit.objects.order_by('pk').values()))
        self.assertEqual(historicos, list(Snapshot.objects.order_by('pk').values()))
        self.assert_sin_originacion()

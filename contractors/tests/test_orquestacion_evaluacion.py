"""Synthetic PostgreSQL tests. Provider transport and broker are always replaced."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import EvaluacionPrestadorJob, PredecisionPrestadorAudit
from contractors.services.confirmacion_simulacion import confirmar_condiciones_simulacion
from contractors.services.estado_evaluacion import construir_seguimiento_evaluacion
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.services.orquestacion_evaluacion import (
    construir_contexto_job, despachar_pendientes, ejecutar_job, publicar_job, reservar_evaluacion_automatica,
)
from contractors.tasks import evaluar_solicitud_prestador_task, publicar_evaluaciones_pendientes_task
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from integrations.datacredito.dto import ResultadoHistorialCreditoRawSeguro, ResultadoMiDecisorRawSeguro
from integrations.datacredito.exceptions import DatacreditoTimeoutError
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures
from integrations.tests.test_midecisor_clasificacion import payload_pn
from usuarios.models import PerfilPagador


CONFIGURACION = {
    **CONFIGURACION_DATACREDITO_PRUEBA,
    'ALLOWED_HOSTS': ['testserver', 'contratistas.localhost'],
    'SECURE_SSL_REDIRECT': False, 'CONTRACTORS_CONTRACT_AI_ENABLED': False,
    'OPENAI_API_KEY': '', 'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'DATACREDITO_HDC_PRODUCT_ID_EXPLICIT': True, 'DATACREDITO_HDC_INFO_ACCOUNT_TYPE_EXPLICIT': True,
    'DATACREDITO_HDC_CHANNEL_NAME': 'CONEXRED-01', 'DATACREDITO_HDC_CHANNEL_TYPE': '42',
    'DATACREDITO_HDC_PARAMETERS_JSON': '',
    'PRESTADORES_AUTO_EVALUACION_ENABLED': False,
}


class FixtureOrquestacion(FixturePipelineFinanciero):
    def setUp(self):
        super().setUp()
        self.activacion = override_settings(
            PRESTADORES_AUTO_EVALUACION_ENABLED=True, PRESTADORES_EVALUACION_ACTOR_ID=self.staff.pk,
        )
        self.activacion.enable()
        self.addCleanup(self.activacion.disable)
        self.broker = patch('celery.app.task.Task.apply_async')
        self.publicar = self.broker.start()
        self.addCleanup(self.broker.stop)

    @contextmanager
    def proveedores(self, *, hc='13', tx='02', timeout=False):
        decisor = payload_pn(hc=hc, tx=tx, informacion=True)
        decisor['content']['respuesta']['informacionRiesgo']['score'] = '900'
        hdc = deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural', return_value=ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional=f'HC{hc}_TX{tx}', raw_sanitizado=decisor,
        )) as midecisor, patch('contractors.datacredito.adapter.consultar_historial_credito', return_value=ResultadoHistorialCreditoRawSeguro(
            status_code=200, response_code='13', raw_sanitizado=hdc,
        )) as historial:
            if timeout:
                midecisor.side_effect = DatacreditoTimeoutError('synthetic timeout')
            yield midecisor, historial

    def reservar(self):
        return reservar_evaluacion_automatica(self.solicitud)


@override_settings(**CONFIGURACION)
class OrquestacionEvaluacionTest(FixtureOrquestacion, TestCase):
    def test_confirmacion_nueva_reserva_y_publica_solo_despues_commit(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            confirmar_condiciones_simulacion(solicitud=self.solicitud, usuario=self.usuario,
                                            monto='3500000', plazo_meses=6)
            self.publicar.assert_not_called()
            self.assertEqual(EvaluacionPrestadorJob.objects.count(), 1)
        self.assertEqual(len(callbacks), 1)
        job = EvaluacionPrestadorJob.objects.get()
        self.publicar.assert_called_once_with(args=[job.pk], retry=False)
        self.assertEqual(job.estado, 'ENCOLADA')
        evidencia = self.solicitud.__class__.objects.values().get(pk=self.solicitud.pk)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            confirmar_condiciones_simulacion(solicitud=self.solicitud, usuario=self.usuario,
                                            monto='3500000.00', plazo_meses=6)
        self.assertEqual(callbacks, [])
        self.assertEqual(evidencia, self.solicitud.__class__.objects.values().get(pk=self.solicitud.pk))
        self.assertEqual(EvaluacionPrestadorJob.objects.count(), 1)
        self.assertEqual(self.publicar.call_count, 1)

    def test_flags_y_readiness_no_crean_jobs_ni_auditorias(self):
        for config in ({'PRESTADORES_AUTO_EVALUACION_ENABLED': False},
                       {'DATACREDITO_ENABLED': False}, {'DATACREDITO_REAL_ENABLED': False},
                       {'PRESTADORES_EVALUACION_ACTOR_ID': ''}, {'DATACREDITO_DECISOR_CLIENT_SECRET': ''}):
            with self.subTest(config=list(config)), override_settings(**config):
                self.assertIsNone(self.reservar())
        self.assertFalse(EvaluacionPrestadorJob.objects.exists())
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())
        self.publicar.assert_not_called()

    def test_actor_no_autorizado_o_pagador_no_reserva(self):
        actor = get_user_model().objects.create_user(username='actor-sintetico', is_staff=True)
        with override_settings(PRESTADORES_EVALUACION_ACTOR_ID=actor.pk):
            self.assertIsNone(self.reservar())
        PerfilPagador.objects.create(usuario=self.staff, empresa=self.empresa)
        self.assertIsNone(self.reservar())
        self.assertFalse(EvaluacionPrestadorJob.objects.exists())

    def test_staff_inactivo_no_reserva(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertIsNone(self.reservar())

    def test_politica_y_simulacion_deben_seguir_vigentes(self):
        self.politica.activa = False
        self.politica.save(update_fields=['activa'])
        self.assertIsNone(self.reservar())

    def test_broker_caido_no_revierte_confirmacion_y_dispatcher_recupera(self):
        self.publicar.side_effect = RuntimeError('synthetic credential must not be logged')
        with patch('contractors.services.orquestacion_evaluacion.logger.warning') as log, \
                self.captureOnCommitCallbacks(execute=True):
            confirmar_condiciones_simulacion(solicitud=self.solicitud, usuario=self.usuario,
                                            monto='3500000', plazo_meses=6)
        log.assert_called_once_with('prestadores_publicacion_pendiente')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'EVALUACION_PENDIENTE')
        job = EvaluacionPrestadorJob.objects.get()
        self.assertEqual(job.estado, 'PENDIENTE_PUBLICACION')
        self.assertEqual(job.intentos_publicacion, 1)
        self.assertEqual(despachar_pendientes(), 0)
        self.publicar.side_effect = None
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(proxima_publicacion=timezone.now() - timedelta(seconds=1))
        self.assertEqual(despachar_pendientes(), 1)
        self.assertEqual(despachar_pendientes(), 0)
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_publicacion_no_retrocede_worker_que_gano_al_ack(self):
        job = self.reservar()
        def avanzar(**kwargs):
            EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(estado='FINALIZADA')
        self.publicar.side_effect = avanzar
        self.assertTrue(publicar_job(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.estado, 'FINALIZADA')

    def test_publicacion_revalida_readiness_tras_commit(self):
        job = self.reservar()
        with override_settings(DATACREDITO_ENABLED=False):
            self.assertFalse(publicar_job(job.pk))
        self.publicar.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.estado, 'SUSPENDIDA_OPERATIVA')

    def test_fallo_callback_no_hace_fallar_confirmacion_persistida(self):
        with patch('contractors.services.orquestacion_evaluacion.publicar_job', side_effect=RuntimeError('synthetic')), \
                self.captureOnCommitCallbacks(execute=True):
            confirmar_condiciones_simulacion(solicitud=self.solicitud, usuario=self.usuario,
                                            monto='3500000', plazo_meses=6)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.monto_solicitado, 3500000)
        self.assertEqual(EvaluacionPrestadorJob.objects.get().estado, 'PENDIENTE_PUBLICACION')

    def test_rollback_no_publica_ni_deja_reserva(self):
        from django.db import transaction
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.reservar()
                raise RuntimeError('synthetic rollback')
        self.assertEqual(callbacks, [])
        self.assertFalse(EvaluacionPrestadorJob.objects.exists())

    def test_entrega_duplicada_reutiliza_un_job_y_una_evaluacion(self):
        job = self.reservar()
        self.assertEqual(self.reservar().pk, job.pk)
        with self.proveedores() as (decisor, hdc):
            evaluar_solicitud_prestador_task.run(job.pk)
            anterior = list(PredecisionPrestadorAudit.objects.values())
            evaluar_solicitud_prestador_task.run(job.pk)
        self.assertEqual(decisor.call_count, 1)
        self.assertEqual(hdc.call_count, 1)
        self.assertEqual(anterior, list(PredecisionPrestadorAudit.objects.values()))
        job.refresh_from_db()
        self.assertEqual(job.estado, 'FINALIZADA')
        self.assertIsNotNone(job.auditoria_id)
        self.assert_sin_originacion()

    def comprobar_flag_worker(self, flag):
        job = self.reservar()
        with override_settings(**{flag: False}), self.proveedores() as (decisor, hdc):
            self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        self.assertEqual(despachar_pendientes(), 0)
        self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        decisor.assert_not_called()
        hdc.assert_not_called()
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_worker_revalida_enabled(self):
        self.comprobar_flag_worker('DATACREDITO_ENABLED')

    def test_worker_revalida_real_enabled(self):
        self.comprobar_flag_worker('DATACREDITO_REAL_ENABLED')

    def test_worker_revalida_auto_enabled(self):
        self.comprobar_flag_worker('PRESTADORES_AUTO_EVALUACION_ENABLED')

    def test_worker_revalida_permiso_actor(self):
        job = self.reservar()
        get_user_model().objects.filter(pk=self.staff.pk).update(is_staff=False)
        self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_worker_revalida_actor_tecnico_configurado(self):
        job = self.reservar()
        with override_settings(PRESTADORES_EVALUACION_ACTOR_ID=''):
            self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_consentimiento_revocado_suspendido_sin_consumo(self):
        job = self.reservar()
        self.solicitud.autorizaciones_datacredito.update(autorizada=False)
        self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())

    def test_consentimiento_incompatible_no_reserva(self):
        self.solicitud.autorizaciones_datacredito.update(version_texto='incompatible')
        self.assertIsNone(self.reservar())

    def test_contexto_cambiado_antes_claim_es_obsoleto(self):
        job = self.reservar()
        self.solicitud.__class__.objects.filter(pk=self.solicitud.pk).update(monto_solicitado='4000000')
        self.assertEqual(ejecutar_job(job.pk), 'OBSOLETA')
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_fencing_en_lock_evaluador_impide_toctou(self):
        contexto = construir_contexto_job(self.solicitud, self.staff)
        self.solicitud.__class__.objects.filter(pk=self.solicitud.pk).update(plazo_meses=5)
        with self.proveedores() as (decisor, hdc), self.assertRaises(ValidationError):
            evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff, contexto_esperado=contexto)
        decisor.assert_not_called()
        hdc.assert_not_called()
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_reserva_operativa_vencida_no_inicia_auditoria(self):
        job = self.reservar()
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(
            estado='EN_EJECUCION', generacion=1, lease_hasta=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(ValidationError):
            evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff,
                                       contexto_esperado=job.contexto, reserva_operativa=(job.pk, 1))
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_cambio_politica_antes_ejecucion_suspendido(self):
        job = self.reservar()
        self.politica.activa = False
        self.politica.save(update_fields=['activa'])
        self.assertEqual(ejecutar_job(job.pk), 'SUSPENDIDA_OPERATIVA')
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_lease_expirado_es_incierto_y_nunca_republica(self):
        job = self.reservar()
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(estado='EN_EJECUCION', lease_hasta=timezone.now() - timedelta(seconds=1))
        self.assertEqual(despachar_pendientes(), 0)
        self.assertEqual(ejecutar_job(job.pk), 'REQUIERE_REVISION')
        self.publicar.assert_not_called()

    def test_worker_ausente_republica_mismo_id_sin_nueva_reserva(self):
        job = self.reservar()
        self.assertTrue(publicar_job(job.pk))
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(proxima_publicacion=timezone.now() - timedelta(seconds=1))
        self.assertEqual(despachar_pendientes(), 1)
        self.assertEqual(self.publicar.call_count, 2)
        self.assertEqual(EvaluacionPrestadorJob.objects.count(), 1)
        for llamada in self.publicar.call_args_list:
            self.assertEqual(llamada.kwargs, {'args': [job.pk], 'retry': False})

    def comprobar_error(self, **opciones):
        job = self.reservar()
        with self.proveedores(**opciones) as (decisor, hdc):
            ejecutar_job(job.pk)
            ejecutar_job(job.pk)
            auditoria = job.__class__.objects.get(pk=job.pk).auditoria
            antes = list(PredecisionPrestadorAudit.objects.values())
            repetida = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        job.refresh_from_db()
        self.assertEqual(job.estado, 'REQUIERE_REVISION')
        self.assertNotEqual(job.auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(decisor.call_count, 1)
        self.assertEqual(hdc.call_count, 1)
        self.assertTrue(repetida.reutilizada)
        self.assertEqual(repetida.auditoria.pk, auditoria.pk)
        self.assertEqual(antes, list(PredecisionPrestadorAudit.objects.values()))
        self.assert_sin_originacion()

    def test_hc14_no_habilita_decision_ni_reintento(self):
        self.comprobar_error(hc='14', tx='05')

    def test_timeout_no_habilita_decision_ni_reintento(self):
        self.comprobar_error(timeout=True)

    def test_tx_fallido_no_habilita_decision_ni_reintento(self):
        self.comprobar_error(hc='13', tx='20')

    def test_sin_oferta_no_puede_avanzar_gates(self):
        from contractors.services.aprobacion_interna import crear_o_reutilizar_aprobacion_interna
        self.verificar_ingreso('100000')
        job = self.reservar()
        with self.proveedores():
            ejecutar_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(self.oferta(job.auditoria).estado, 'SIN_OFERTA')
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(job.auditoria, actor=self.staff)
        self.assertFalse(self.solicitud.aprobaciones_internas.exists())
        self.assert_sin_originacion()

    def test_reutilizacion_destino_consentido_no_reescribe_origen_ni_http(self):
        job = self.reservar()
        with self.proveedores() as (decisor, hdc):
            ejecutar_job(job.pk)
            originales = list(ConsultaDatacreditoSnapshot.objects.values())
            otra = self.nueva_solicitud_autorizada()
            destino = reservar_evaluacion_automatica(otra)
            ejecutar_job(destino.pk)
        self.assertEqual(decisor.call_count, 1)
        self.assertEqual(hdc.call_count, 1)
        self.assertEqual(originales, list(ConsultaDatacreditoSnapshot.objects.values()))
        destino.refresh_from_db()
        self.assertEqual(set(destino.auditoria.snapshot_salida['usos_snapshots']), {'decisor', 'historial'})
        self.assert_sin_originacion()

    def test_endpoint_solo_lectura_privado_ownership_y_metodo(self):
        job = self.reservar()
        antes = (list(EvaluacionPrestadorJob.objects.values()), list(self.solicitud.timeline_operativo.values()))
        url = f'/mi-credito/solicitud/{self.solicitud.pk}/estado/'
        respuesta = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(set(respuesta.json()), {'estado', 'etapa', 'mensaje', 'etiqueta', 'detalle', 'actualizado_en', 'continuar_seguimiento', 'procesando'})
        self.assertEqual(respuesta.json()['estado'], 'ENVIADA')
        self.assertFalse(respuesta.json()['procesando'])
        self.assertEqual(antes, (list(EvaluacionPrestadorJob.objects.values()), list(self.solicitud.timeline_operativo.values())))
        self.assertEqual(self.client.post(url, HTTP_HOST=self.host).status_code, 405)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url, HTTP_HOST=self.host).status_code, 404)
        self.publicar.assert_not_called()
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_cerrar_pagina_no_cancela_job_y_reload_muestra_resultado_publico(self):
        job = self.reservar()
        self.client.logout()
        with self.proveedores():
            ejecutar_job(job.pk)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(f'/mi-credito/solicitud/{self.solicitud.pk}/estado/', HTTP_HOST=self.host)
        self.assertEqual(respuesta.json()['estado'], 'EVALUACION_INICIAL_FAVORABLE')
        self.assertFalse(respuesta.json()['continuar_seguimiento'])
        self.assertFalse(respuesta.json()['procesando'])
        self.assertNotIn('score', respuesta.content.decode().lower())
        self.assertNotIn('datacredito', respuesta.content.decode().lower())
        self.assertNotIn('midecisor', respuesta.content.decode().lower())

    def test_solicitud_historica_get_y_dispatcher_no_inician_evaluacion(self):
        self.assertEqual(despachar_pendientes(), 0)
        self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.client.get(f'/mi-credito/solicitud/{self.solicitud.pk}/estado/', HTTP_HOST=self.host)
        with self.captureOnCommitCallbacks(execute=True):
            confirmar_condiciones_simulacion(solicitud=self.solicitud, usuario=self.usuario,
                                            monto=self.solicitud.monto_solicitado, plazo_meses=self.solicitud.plazo_meses)
        self.assertFalse(EvaluacionPrestadorJob.objects.exists())

    def test_loader_solo_ejecucion_real_y_flags_encendidos(self):
        job = self.reservar()
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(estado='EN_EJECUCION', lease_hasta=timezone.now() + timedelta(minutes=5))
        self.solicitud.estado = 'EN_EVALUACION'
        self.solicitud.save(update_fields=['estado'])
        self.assertTrue(construir_seguimiento_evaluacion(self.solicitud)['procesando'])
        with override_settings(DATACREDITO_ENABLED=False):
            estado = construir_seguimiento_evaluacion(self.solicitud)
            self.assertFalse(estado['procesando'])
            self.assertFalse(estado['continuar_seguimiento'])

    def test_evaluacion_manual_real_se_sigue_con_automatizacion_apagada(self):
        job = self.reservar()
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(estado='SUSPENDIDA_OPERATIVA')
        self.solicitud.estado = 'EN_EVALUACION'
        self.solicitud.save(update_fields=['estado'])
        PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud, version_datos='synthetic', clave_idempotencia='d' * 64,
            version_politica='synthetic', iniciada_en=timezone.now(), estado_ejecucion='EN_PROCESO',
        )
        with override_settings(PRESTADORES_AUTO_EVALUACION_ENABLED=False):
            estado = construir_seguimiento_evaluacion(self.solicitud)
        self.assertTrue(estado['procesando'])
        self.assertTrue(estado['continuar_seguimiento'])

    def test_cola_normal_y_dispatcher_no_programado(self):
        from aprobado_web.celery import app
        for task in (evaluar_solicitud_prestador_task, publicar_evaluaciones_pendientes_task):
            ruta = app.amqp.router.route({}, task.name)
            self.assertEqual(ruta['queue'].name, 'celery')
            self.assertFalse(any(e['task'] == task.name for e in app.conf.beat_schedule.values()))


@override_settings(**CONFIGURACION)
@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
class OrquestacionConcurrenciaPostgresTest(FixtureOrquestacion, TransactionTestCase):
    def concurrentes(self, funcion):
        barrier = Barrier(2)
        def ejecutar(_):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return funcion()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(ejecutar, range(2)))

    def test_reserva_concurrente_un_job_una_publicacion(self):
        ids = self.concurrentes(lambda: reservar_evaluacion_automatica(self.solicitud).pk)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(EvaluacionPrestadorJob.objects.count(), 1)
        self.publicar.assert_called_once_with(args=[ids[0]], retry=False)

    def test_confirmaciones_concurrentes_reservan_un_job(self):
        resultados = self.concurrentes(lambda: confirmar_condiciones_simulacion(
            solicitud=self.solicitud, usuario=self.usuario, monto='3500000', plazo_meses=6,
        ).reutilizada)
        self.assertEqual(sorted(resultados), [False, True])
        self.assertEqual(EvaluacionPrestadorJob.objects.count(), 1)
        self.publicar.assert_called_once()

    def test_publicacion_concurrente_un_claim(self):
        job = self.reservar()
        self.publicar.reset_mock()
        EvaluacionPrestadorJob.objects.filter(pk=job.pk).update(proxima_publicacion=timezone.now() - timedelta(seconds=1))
        self.assertEqual(sorted(self.concurrentes(lambda: publicar_job(job.pk))), [False, True])
        self.publicar.assert_called_once()

    def test_dos_workers_solo_un_consumo_sin_atomic_en_http(self):
        job = self.reservar()
        with self.proveedores() as (decisor, hdc):
            original = decisor.return_value
            def transporte(*args, **kwargs):
                self.assertFalse(connection.in_atomic_block)
                return original
            decisor.side_effect = transporte
            self.concurrentes(lambda: ejecutar_job(job.pk))
        self.assertEqual(decisor.call_count, 1)
        self.assertEqual(hdc.call_count, 1)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 2)
        self.assert_sin_originacion()

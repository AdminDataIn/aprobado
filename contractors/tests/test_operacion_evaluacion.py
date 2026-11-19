"""Committed PostgreSQL fixtures and real Celery consumers with an in-memory transport."""
from datetime import timedelta
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from celery.contrib.testing.app import TestApp, setup_default_app
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from contractors.management.commands.operar_evaluacion_prestadores import CUENTA, estado, perfil
from contractors.models import EvaluacionPrestadorJob as Job
from contractors.politica_decision import VERSION_POLITICA, definicion_decision
from contractors.services.decision_definitiva import TIPO_EVENTO
from contractors.services.politica_ingreso_publicacion import publicar_politica_ingreso, adoptar_politica_ingreso
from contractors.tasks import evaluar_solicitud_prestador_task
from contractors.tests.test_orquestacion_evaluacion import CONFIGURACION, FixtureOrquestacion
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero


def operar(accion, operador, **kw):
    return call_command('operar_evaluacion_prestadores', accion, operador_id=operador.pk,
                        motivo='Fixture aislado', aplicar=True, stdout=StringIO(), **kw)


class OperacionControlTest(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='op-fixture', email='op@example.invalid', password=None)

    def test_perfiles_y_retiro_auditados(self):
        operar('provisionar', self.admin)
        u = get_user_model().objects.get(username=CUENTA)
        self.assertEqual(perfil(u), 'v1')
        operar('provisionar', self.admin)
        operar('provisionar', self.admin, perfil='v3')
        self.assertEqual(perfil(get_user_model().objects.get(pk=u.pk)), 'v3')
        operar('retirar', self.admin)
        u.refresh_from_db()
        self.assertFalse(u.is_active or u.is_staff or u.user_permissions.exists())
        self.assertEqual(LogEntry.objects.filter(change_message__startswith='P0C:').count(), 3)
        operar('retirar', self.admin)
        operar('provisionar', self.admin)
        self.assertEqual(perfil(get_user_model().objects.get(pk=u.pk)), 'v1')

    def test_no_reutiliza_cuenta_ni_acepta_operador_sin_permiso(self):
        u = get_user_model().objects.create_user(username=CUENTA)
        for operador in (u, self.admin):
            with self.assertRaises(CommandError):
                operar('provisionar', operador)
        u.refresh_from_db()
        self.assertFalse(u.is_staff or u.user_permissions.exists())

    def test_tick_sin_autorizacion_o_sin_actor_no_publica(self):
        with patch('contractors.tasks.publicar_evaluaciones_pendientes_task.apply_async') as enviar:
            for habilitado in (False, True):
                with override_settings(PRESTADORES_RECUPERACION_ENABLED=habilitado), self.assertRaises(CommandError):
                    call_command('operar_evaluacion_prestadores', 'tick')
            enviar.assert_not_called()

    @override_settings(PRESTADORES_RECUPERACION_ENABLED=True, PRESTADORES_AUTO_EVALUACION_ENABLED=True,
                       DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True)
    def test_tick_no_compite_con_beat(self):
        operar('provisionar', self.admin)
        actor = get_user_model().objects.get(username=CUENTA)
        t = PeriodicTask.objects.create(name='fixture', enabled=True,
            task='contractors.tasks.publicar_evaluaciones_pendientes_task',
            interval=IntervalSchedule.objects.create(every=1, period='minutes'))
        with override_settings(PRESTADORES_EVALUACION_ACTOR_ID=actor.pk), patch(
                'contractors.tasks.publicar_evaluaciones_pendientes_task.apply_async') as enviar:
            with self.assertRaises(CommandError):
                call_command('operar_evaluacion_prestadores', 'tick')
            enviar.assert_not_called()
        t.refresh_from_db()
        self.assertTrue(t.enabled)


@skipUnless(connection.vendor == 'postgresql', 'Requires committed PostgreSQL state')
@override_settings(**CONFIGURACION)
class OperacionWorkerTest(FixturePipelineFinanciero, TransactionTestCase):
    ingreso_automatico = False
    confirmar_en_fixture = False
    proveedores = FixtureOrquestacion.proveedores

    def setUp(self):
        # Unrelated OCR dispatch must never leave the fixture.
        ocr = patch('gestion_creditos.tasks.procesar_ocr_documental_task.apply_async')
        ocr.start()
        self.addCleanup(ocr.stop)
        super().setUp()
        self.app = TestApp(fixups=[], config={'task_default_queue': 'celery', 'task_always_eager': False})
        self.enterContext(setup_default_app(self.app))
        self.app.set_current()
        self.app.set_default()
        self.assertEqual(self.app.connection_for_write().transport.driver_type, 'memory')

    def confirmar(self):
        r = self.client.post('/simular/', {'solicitud_id': self.solicitud.pk, 'monto': '3000000',
                                          'plazo_meses': 6}, HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 302)
        self.solicitud.refresh_from_db()
        self.assertIsNotNone(self.solicitud.simulada_en)

    def activar_fixture(self, version):
        operar('provisionar', self.staff, perfil=version)
        self.tecnico = get_user_model().objects.get(username=CUENTA)
        self.enterContext(override_settings(PRESTADORES_EVALUACION_ACTOR_ID=self.tecnico.pk,
                                            PRESTADORES_AUTO_EVALUACION_ENABLED=True))

    def publicar_v3(self, rechazo=False):
        if rechazo:
            reglas = {**definicion_decision(), 'exclusiones': ['BANDA_REVISION'],
                      'fuente_normativa': 'POLITICA_SINTETICA_SOLO_BASE_TEST'}
            self.enterContext(patch('contractors.politica_decision.definicion_decision', return_value=reglas))
        return publicar_politica_ingreso(origen_id=self.politica.pk, actor=self.staff, motivo='Solo fixture',
            vigente_desde=timezone.localdate(), version_destino=VERSION_POLITICA)

    def adoptar(self, politica):
        return adoptar_politica_ingreso(solicitud_id=self.solicitud.pk, politica_id=politica.pk,
                                        actor=self.staff, motivo='Piloto sintetico', reservar_al_adoptar=True)

    def esperar_entrega(self):
        evento = Event()
        uid = uuid4().hex
        def terminada(sender, **kw):
            if sender.name == evaluar_solicitud_prestador_task.name:
                evento.set()
        task_postrun.connect(terminada, weak=False, dispatch_uid=uid)
        self.addCleanup(task_postrun.disconnect, dispatch_uid=uid)
        return evento

    def circuito(self, publico, *, v3=True, rechazo=False, recuperar=False, **fuentes):
        if v3:
            self.confirmar()  # Explicit individual adoption cannot displace a V1 job.
            self.assertFalse(Job.objects.exists())
            politica = self.publicar_v3(rechazo)
        self.activar_fixture('v3' if v3 else 'v1')
        if rechazo:
            self.tecnico.user_permissions.add(Permission.objects.get(content_type__app_label='contractors',
                                                                     codename='can_resolve_contractor_review'))
        evento = self.esperar_entrega()
        if recuperar:
            with start_worker(self.app, perform_ping_check=False):
                pass
        if v3:
            self.adoptar(politica)
            self.adoptar(politica)
        else:
            self.confirmar()
        self.confirmar()  # Identical replay does not reserve or republish.
        job = Job.objects.get()
        self.assertEqual(job.estado, 'ENCOLADA')
        if recuperar:
            # Explicitly lose the first broker delivery while the consumer is down.
            with self.app.connection_for_read() as con:
                msg = con.SimpleQueue('celery').get(block=False)
                self.assertEqual(msg.payload[0], [job.pk])
                msg.ack()
            Job.objects.filter(pk=job.pk).update(proxima_publicacion=timezone.now()-timedelta(minutes=3))
            self.assertEqual(estado()['publicacion_atrasada'], 1)
            with override_settings(PRESTADORES_RECUPERACION_ENABLED=True):
                call_command('operar_evaluacion_prestadores', 'tick', stdout=StringIO())
        with self.proveedores(**fuentes) as mock_fuentes, start_worker(self.app, perform_ping_check=False):
            self.assertTrue(evento.wait(25), 'No se recibio la tarea real')
            evento.clear()
            evaluar_solicitud_prestador_task.apply_async(args=[job.pk], retry=False)
            self.assertTrue(evento.wait(25), 'No se recibio la segunda entrega')
            for fuente in mock_fuentes:
                fuente.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.estado, 'REQUIERE_REVISION' if publico == 'REVISION' else 'FINALIZADA')
        self.assertEqual(job.generacion, 1)
        self.assertEqual(job.intentos_publicacion, 2 if recuperar else 1)
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 1)
        self.assertEqual(self.solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO).count(), int(v3))
        r = self.client.get(f'/mi-credito/solicitud/{self.solicitud.pk}/estado/', HTTP_HOST=self.host)
        self.assertEqual(r.json()['estado'], publico)
        self.assertFalse(r.json()['procesando'])
        self.assert_sin_originacion()

    def test_v1_confirmacion_y_reinicio_sin_doble_consumo(self):
        self.circuito('EVALUACION_INICIAL_FAVORABLE', v3=False, recuperar=True)

    def test_v3_favorable(self):
        self.circuito('PENDIENTE_CONFIRMACION_EMPRESA')

    def test_v3_rechazo_solo_en_fixture(self):
        self.circuito('NO_APROBADO', rechazo=True, score=0)

    def test_v3_indeterminado_no_rechaza(self):
        self.circuito('REVISION', hc='14', tx='05')

    def test_muerte_del_proceso_tras_claim_no_repite_ejecucion(self):
        from contractors.services.orquestacion_evaluacion import despachar_pendientes, ejecutar_job
        from integrations.models import ConsultaDatacreditoSnapshot
        db = connection.settings_dict
        url = urlsplit(os.environ.get('DATABASE_URL', ''))
        if not str(db['NAME']).startswith('test_') or url.hostname not in {'localhost', '127.0.0.1'} or url.hostname != db['HOST']:
            self.skipTest('Prueba destructiva solo en una base test_ PostgreSQL loopback.')
        self.activar_fixture('v1')
        self.confirmar()
        job = Job.objects.get()
        with self.app.connection_for_read() as con:
            msg = con.SimpleQueue('celery').get(block=False)
            self.assertEqual(msg.payload[0], [job.pk])
            msg.ack()
        env = dict(os.environ, DATABASE_URL=urlunsplit(url._replace(path='/' + db['NAME'])),
                   REDIS_URL='memory://', CELERY_BROKER_URL='memory://', USE_SQLITE='false',
                   MEDIA_ROOT=str(settings.MEDIA_ROOT), PRIVATE_DOCUMENTS_ROOT=str(settings.PRIVATE_DOCUMENTS_ROOT),
                   P0C_FIXTURE_ACTOR=str(self.tecnico.pk), P0C_FIXTURE_JOB=str(job.pk))
        # Terminate the actual solo consumer at the boundary after the durable claim,
        # before the (mocked) financial service. Nothing in this process may contact a provider.
        programa = '''
import os, time
from unittest.mock import patch
from django.test import override_settings
from celery.contrib.testing.app import TestApp
from celery.contrib.testing.worker import start_worker
from contractors.tests.test_orquestacion_evaluacion import CONFIGURACION
from contractors.tasks import evaluar_solicitud_prestador_task
app = TestApp(fixups=[], config={'task_default_queue':'celery', 'task_always_eager':False})
app.set_current()
app.set_default()
assert app.connection_for_write().transport.driver_type == 'memory'
with override_settings(**dict(CONFIGURACION, PRESTADORES_AUTO_EVALUACION_ENABLED=True, PRESTADORES_EVALUACION_ACTOR_ID=int(os.environ['P0C_FIXTURE_ACTOR']))), patch('contractors.services.orquestacion_evaluacion.evaluar_solicitud_prestador', side_effect=lambda *a, **k: os._exit(77)), start_worker(app, perform_ping_check=False):
    evaluar_solicitud_prestador_task.apply_async(args=[int(os.environ['P0C_FIXTURE_JOB'])], retry=False)
    time.sleep(15)
'''
        root = Path(__file__).resolve().parents[2]
        resultado = subprocess.run([sys.executable, 'manage.py', 'shell', '-c', programa],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        job.refresh_from_db()
        self.assertEqual(resultado.returncode, 77, (job.estado, job.codigo_operativo, job.generacion))
        self.assertEqual((job.estado, job.generacion), ('EN_EJECUCION', 1))
        Job.objects.filter(pk=job.pk).update(lease_hasta=timezone.now()-timedelta(seconds=1))
        with self.proveedores() as fuentes:
            self.assertEqual(despachar_pendientes(), 0)
            self.assertEqual(ejecutar_job(job.pk), 'REQUIERE_REVISION')
            for fuente in fuentes:
                fuente.assert_not_called()
        self.assertEqual(estado()['inciertos'], 1)
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assert_sin_originacion()

    def test_reserva_no_disponible_revierte_adopcion(self):
        self.confirmar()
        politica = self.publicar_v3()
        with self.assertRaises(ValidationError):
            self.adoptar(politica)
        self.activar_fixture('v1')
        with self.assertRaises(ValidationError):
            self.adoptar(politica)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.version_politica_simulacion, self.politica.version_politica)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(self.solicitud.timeline_operativo.filter(metadata__tipo='ADOPCION_INGRESO').exists())

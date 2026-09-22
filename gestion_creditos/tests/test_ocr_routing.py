"""Central routing and real async publication using only an in-memory broker."""
from datetime import timedelta
from io import StringIO
from queue import Empty
import os
from unittest.mock import patch

from celery import Celery
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from aprobado_web.celery import app as production_app
from gestion_creditos import tasks
from gestion_creditos.services import ocr_documental as servicio
from gestion_creditos.tests.captura_fixtures import sesion_finalizada
from gestion_creditos.tests.test_ocr_documental import OCRFixture


OCR_TASK = 'gestion_creditos.tasks.procesar_ocr_documental_task'


class OCRRoutingTest(SimpleTestCase):
    def test_solo_ocr_tiene_ruta_dedicada(self):
        self.assertEqual(settings.CELERY_TASK_ROUTES, {OCR_TASK: {'queue': 'ocr_documental'}})
        route = production_app.amqp.router.route({}, OCR_TASK)
        self.assertEqual(route['queue'].name, 'ocr_documental')

    def test_tarea_comun_conserva_cola_default(self):
        route = production_app.amqp.router.route({}, tasks.marcar_creditos_en_mora_task.name)
        self.assertEqual(route['queue'].name, production_app.conf.task_default_queue)
        self.assertNotEqual(route['queue'].name, 'ocr_documental')


class OCRPublicacionRoutingTest(OCRFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.publisher_patch.stop()
        memory_environment = patch.dict(os.environ, {
            'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://',
        })
        memory_environment.start()
        self.addCleanup(memory_environment.stop)
        self.app = Celery('ocr-routing-test', set_as_current=False, fixups=[])
        self.addCleanup(self.app.close)
        self.app.config_from_object('django.conf:settings', namespace='CELERY')
        self.app.conf.update(broker_url='memory://', result_backend='cache+memory://', task_always_eager=False)
        # Refuse to open a connection unless test-only transports are effective.
        self.assertEqual(self.app.conf.broker_url, 'memory://')
        self.assertEqual(self.app.conf.result_backend, 'cache+memory://')
        self.task = self.app.task(name=OCR_TASK, ignore_result=True)(tasks.procesar_ocr_documental_task.run)
        task_patch = patch.object(tasks, 'procesar_ocr_documental_task', self.task)
        task_patch.start()
        self.addCleanup(task_patch.stop)
        self.connection = self.app.connection_for_read()
        self.addCleanup(self.connection.close)
        self.queue = self.connection.SimpleQueue('ocr_documental')
        self.addCleanup(self.queue.close)
        self.queue.clear()
        self.addCleanup(self.queue.clear)
        self.default_queue = self.connection.SimpleQueue(self.app.conf.task_default_queue)
        self.addCleanup(self.default_queue.close)
        self.default_queue.clear()
        self.addCleanup(self.default_queue.clear)

    def assert_publicado(self, procesamiento_id):
        message = self.queue.get(block=False)
        try:
            self.assertEqual(message.headers['task'], OCR_TASK)
            self.assertEqual(message.payload[0], [procesamiento_id])
            self.assertEqual(message.payload[1], {})
        finally:
            message.ack()
        with self.assertRaises(Empty):
            self.queue.get(block=False)
        with self.assertRaises(Empty):
            self.default_queue.get(block=False)

    def test_finalizacion_publica_en_ocr_sin_duplicar(self):
        with patch.object(self.task, 'apply_async', wraps=self.task.apply_async) as publish:
            with self.captureOnCommitCallbacks(execute=True):
                sesion = sesion_finalizada(self.user)
                procesamiento = sesion.procesamientos_ocr.get()
                self.assertEqual(servicio.asegurar_procesamiento(sesion.pk).pk, procesamiento.pk)
            publish.assert_called_once_with(args=[procesamiento.pk], retry=False)
        self.assert_publicado(procesamiento.pk)
        self.assertEqual(sesion.procesamientos_ocr.count(), 1)

    def test_comando_recupera_pendiente_usando_mismo_routing(self):
        with patch.object(self.task, 'apply_async', wraps=self.task.apply_async) as publish:
            with self.captureOnCommitCallbacks(execute=True):
                call_command('reencolar_ocr_documental', limite=1, stdout=StringIO())
            publish.assert_called_once_with(args=[self.p.pk], retry=False)
        self.assert_publicado(self.p.pk)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(servicio.recuperar(), 0)
        with self.assertRaises(Empty):
            self.queue.get(block=False)

    def test_reserva_vencida_recupera_y_terminal_no_republica(self):
        self.p.estado = 'PROCESANDO'
        self.p.reservado_hasta = timezone.now() - timedelta(seconds=1)
        self.p.save(update_fields=['estado', 'reservado_hasta'])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(servicio.recuperar(), 1)
        self.assert_publicado(self.p.pk)
        self.run_ocr()
        with self.captureOnCommitCallbacks(execute=True), patch.object(self.task, 'apply_async') as publish:
            self.assertEqual(servicio.recuperar(), 0)
            self.assertEqual(servicio.asegurar_procesamiento(self.sesion.pk).pk, self.p.pk)
        publish.assert_not_called()
        self.assertEqual(self.p.numero_intentos, 1)

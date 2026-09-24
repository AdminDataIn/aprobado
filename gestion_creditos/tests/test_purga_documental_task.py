from datetime import timedelta
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from aprobado_web.celery import app
from gestion_creditos import tasks
from gestion_creditos.models import EventoCapturaDocumental, SesionCapturaDocumental
from gestion_creditos.services import captura_documental as servicio
from gestion_creditos.tests.captura_fixtures import sesion_finalizada
from gestion_creditos.tests.test_captura_documental import CapturaFixture


TASK = 'gestion_creditos.tasks.purgar_capturas_expiradas_task'
ENTRY = 'purgar-capturas-documentales-expiradas'


class PurgaDocumentalConfiguracionTest(SimpleTestCase):
    def test_beat_diario_0300_bogota_unica_entrada(self):
        entries = [entry for entry in app.conf.beat_schedule.values() if entry['task'] == TASK]
        self.assertEqual(len(entries), 1)
        schedule = app.conf.beat_schedule[ENTRY]['schedule']
        self.assertEqual(schedule.hour, {3})
        self.assertEqual(schedule.minute, {0})
        self.assertEqual(schedule.day_of_week, set(range(7)))
        self.assertEqual(schedule.day_of_month, set(range(1, 32)))
        self.assertEqual(schedule.month_of_year, set(range(1, 13)))
        self.assertEqual(settings.TIME_ZONE, 'America/Bogota')
        self.assertEqual(app.conf.timezone, settings.CELERY_TIMEZONE)
        self.assertEqual(str(schedule.tz), 'America/Bogota')
        self.assertEqual(settings.CAPTURA_DOCUMENTAL_FINALIZADA_RETENTION_HOURS, 72)

    def test_cola_normal_ocr_permanece_aislado(self):
        route = app.amqp.router.route({}, TASK)
        self.assertEqual(route['queue'].name, 'celery')
        self.assertEqual(route['queue'].name, app.conf.task_default_queue)
        self.assertNotIn('queue', app.conf.beat_schedule[ENTRY].get('options', {}))
        ocr = app.amqp.router.route({}, tasks.procesar_ocr_documental_task.name)
        self.assertEqual(ocr['queue'].name, 'ocr_documental')
        self.assertEqual(tasks.purgar_capturas_expiradas_task.name, TASK)

    def test_task_delega_y_registra_solo_resumen(self):
        resumen = dict(candidatos=5, purgados=3, omitidos=1, archivos_purgados=6, errores=1)
        with patch.object(servicio, 'purgar_sesiones_expiradas', return_value=resumen) as purgar:
            with self.assertLogs('gestion_creditos.tasks', level='WARNING') as logs:
                self.assertEqual(tasks.purgar_capturas_expiradas_task.run(), resumen)
        purgar.assert_called_once_with(con_resumen=True)
        self.assertEqual(logs.output, ['WARNING:gestion_creditos.tasks:PURGA_DOCUMENTAL '
                                      'candidatos=5 purgados=3 omitidos=1 archivos_purgados=6 errores=1'])

    def test_comando_manual_delega_mismo_servicio_y_conserva_salida(self):
        from gestion_creditos.management.commands import purgar_capturas_expiradas as comando
        self.assertIs(comando.purgar_sesiones_expiradas, servicio.purgar_sesiones_expiradas)
        with patch.object(comando, 'purgar_sesiones_expiradas', return_value=2) as purgar:
            output = StringIO()
            call_command('purgar_capturas_expiradas', stdout=output)
        purgar.assert_called_once_with()
        self.assertEqual(output.getvalue(), '2\n')

    def test_error_global_no_expone_excepcion_a_celery(self):
        with patch.object(servicio, 'purgar_sesiones_expiradas', side_effect=OSError('ruta-privada-token-PII')):
            with self.assertLogs('gestion_creditos.tasks', level='ERROR') as logs:
                with self.assertRaises(RuntimeError) as error:
                    tasks.purgar_capturas_expiradas_task.run()
        self.assertTrue(error.exception.__suppress_context__)
        self.assertNotIn('ruta-privada-token-PII', str(error.exception) + str(logs.output))
        self.assertEqual(logs.output, ['ERROR:gestion_creditos.tasks:PURGA_DOCUMENTAL_INTERRUMPIDA errores=1'])


class PurgaDocumentalTaskTest(CapturaFixture, TestCase):
    def finalizar_antigua(self):
        sesion = sesion_finalizada(self.usuario)
        SesionCapturaDocumental.objects.filter(pk=sesion.pk).update(
            finalizado_en=timezone.now() - timedelta(hours=73),
            expira_en=timezone.now() - timedelta(hours=72))
        return sesion

    def test_doble_task_y_comando_idempotentes(self):
        sesion = self.finalizar_antigua()
        primera = tasks.purgar_capturas_expiradas_task.run()
        self.assertEqual(primera, dict(candidatos=1, purgados=1, omitidos=0, archivos_purgados=2, errores=0))
        eventos = list(sesion.eventos.values_list('pk', flat=True))
        marcas = list(sesion.capturas.values_list('pk', 'purgado_en'))
        segunda = tasks.purgar_capturas_expiradas_task.run()
        self.assertEqual(segunda, dict(candidatos=1, purgados=0, omitidos=1, archivos_purgados=0, errores=0))
        output = StringIO()
        call_command('purgar_capturas_expiradas', stdout=output)
        self.assertEqual(output.getvalue(), '0\n')
        self.assertCountEqual(list(sesion.eventos.values_list('pk', flat=True)), eventos)
        self.assertCountEqual(list(sesion.capturas.values_list('pk', 'purgado_en')), marcas)

    def test_conservada_cuenta_como_omitida_sin_pii(self):
        sesion = self.finalizar_antigua()
        EventoCapturaDocumental.objects.create(sesion=sesion, actor=self.usuario, evento='VINCULACION')
        with self.assertLogs('gestion_creditos.tasks', level='INFO') as logs:
            resumen = tasks.purgar_capturas_expiradas_task.run()
        self.assertEqual(resumen, dict(candidatos=1, purgados=0, omitidos=1, archivos_purgados=0, errores=0))
        self.assertNotIn(str(sesion.pk), str(logs.output))
        self.assertFalse(sesion.capturas.filter(purgado_en__isnull=False).exists())

    def test_fallo_individual_rollback_continua_y_reintenta_sin_pii(self):
        fallida, buena = self.finalizar_antigua(), self.finalizar_antigua()
        archivo = fallida.capturas.first().archivo
        borrar = archivo.storage.delete
        def falla_solo_un_archivo(nombre):
            if nombre == archivo.name:
                raise OSError('PII-SECRETA ' + nombre)
            return borrar(nombre)
        with patch.object(archivo.storage, 'delete', side_effect=falla_solo_un_archivo):
            with self.assertLogs('gestion_creditos.tasks', level='WARNING') as logs:
                resumen = tasks.purgar_capturas_expiradas_task.run()
        self.assertEqual(resumen, dict(candidatos=2, purgados=1, omitidos=0, archivos_purgados=2, errores=1))
        self.assertNotIn('PII-SECRETA', str(logs.output))
        self.assertNotIn(archivo.name, str(logs.output))
        self.assertNotIn(str(fallida.pk), str(logs.output))
        fallida.refresh_from_db()
        self.assertEqual(fallida.estado, 'FINALIZADA')
        self.assertFalse(fallida.eventos.filter(evento='PURGA_TEMPORALES').exists())
        self.assertFalse(fallida.capturas.filter(purgado_en__isnull=False).exists())
        self.assertEqual(buena.capturas.filter(purgado_en__isnull=False).count(), 2)
        self.assertEqual(tasks.purgar_capturas_expiradas_task.run()['archivos_purgados'], 2)
        self.assertEqual(tasks.purgar_capturas_expiradas_task.run()['archivos_purgados'], 0)

    @skipUnless('django_celery_beat' in settings.INSTALLED_APPS, 'DatabaseScheduler opcional no instalado.')
    def test_database_scheduler_importa_entrada_sin_duplicarla(self):
        from django_celery_beat.models import PeriodicTask
        from django_celery_beat.schedulers import ModelEntry
        for _ in range(2):
            ModelEntry.from_entry(ENTRY, app=app, **app.conf.beat_schedule[ENTRY])
        self.assertEqual(PeriodicTask.objects.filter(name=ENTRY).count(), 1)
        periodic = PeriodicTask.objects.get(name=ENTRY)
        self.assertTrue(periodic.enabled)
        self.assertEqual(periodic.task, TASK)
        self.assertEqual(periodic.crontab.hour, '3')
        self.assertEqual(periodic.crontab.minute, '0')
        self.assertEqual(str(periodic.crontab.timezone), 'America/Bogota')

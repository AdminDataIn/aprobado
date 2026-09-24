import hashlib
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import ContractorApplication
from gestion_creditos.models import (ProcesamientoOCRDocumental as OCR, ComparacionOCRDocumental as Comparacion,
    SesionCapturaDocumental as Sesion, EventoCapturaDocumental as Evento, Credito, CreditoLibranza, Empresa)
from gestion_creditos.services import captura_documental as captura, ocr_documental as servicio
from gestion_creditos.services.ocr import parser_documento_colombia as parser, tesseract_adapter as adapter
from gestion_creditos.tests.captura_fixtures import sesion_finalizada, imagen_documental


def documento(lines):
    return {'version': 'test-1', 'tokens': [{'text': text, 'confidence': 95,
        'line': [1, 1, 1, i + 1], 'box': [10, 20 * i, 500, 18]} for i, text in enumerate(lines)]}


FRONTAL = documento(['REPUBLICA DE COLOMBIA', 'CEDULA DE CIUDADANIA', 'NUMERO: 99.000.123',
                      'APELLIDOS: PRUEBA SINTETICA', 'NOMBRES: PERSONA FICTICIA'])
TRASERA = documento(['REPUBLICA DE COLOMBIA', 'CEDULA DE CIUDADANIA',
                    'FECHA DE NACIMIENTO: 1990-01-20', 'FECHA DE EXPEDICION: 2010-02-22',
                    'LUGAR DE EXPEDICION: CIUDAD FICTICIA'])


class ParserOCRTest(SimpleTestCase):
    def test_frontal_trasera_campos_y_confianza(self):
        result = parser.parsear(FRONTAL, TRASERA)
        self.assertEqual(result['estado'], 'COMPLETADO')
        self.assertEqual(result['numero_documento_normalizado'], '99000123')
        self.assertEqual(result['nombres_bruto'], 'PERSONA FICTICIA')
        self.assertEqual(result['lado_trasera_detectado'], 'TRASERA')
        self.assertEqual(result['tipo_documento_detectado'], 'CC_COLOMBIA')
        self.assertEqual(result['confianza']['numero_documento'], 95)
        self.assertNotIn('texto', result)

    def test_normalizacion_sin_inventar_digitos(self):
        self.assertEqual(parser.normalizar_texto('  Jos\u00e9\n  P\u00e9rez '), 'JOSE PEREZ')
        self.assertEqual(parser.normalizar_numero(' 99.000-123 '), '99000123')
        for raw in ('I234', '12O3', '123/4'):
            self.assertEqual(parser.normalizar_numero(raw), '')

    def test_fechas_ambiguas_y_invalidas(self):
        for value in ('01/02/1990', '1990-99-01', ''):
            self.assertIsNone(parser.fecha_inequivoca(value))
        back = documento(['REPUBLICA DE COLOMBIA', 'CEDULA DE CIUDADANIA', 'FECHA DE NACIMIENTO: 01/02/1990'])
        result = parser.parsear(FRONTAL, back)
        self.assertEqual(result['estado'], 'REQUIERE_REVISION')
        self.assertIn('FECHA_NO_INTERPRETABLE', result['evidencia']['codigos'])

    def test_lados_intercambiados(self):
        result = parser.parsear(TRASERA, FRONTAL)
        self.assertEqual(result['codigo_error'], 'LADO_INCORRECTO')
        self.assertEqual(result['numero_documento_normalizado'], '')

    def test_ilegible_desconocido_ce(self):
        for doc, code in [(documento([]), 'TEXTO_ILEGIBLE'), (documento(['OTRO DOCUMENTO']), 'DOCUMENTO_DESCONOCIDO'),
                          (documento(['CEDULA DE EXTRANJERIA']), 'DOCUMENTO_NO_SOPORTADO')]:
            with self.subTest(code=code):
                result = parser.parsear(doc, doc)
                self.assertEqual(result['codigo_error'], code)
                self.assertEqual(result['estado'], 'REQUIERE_REVISION')

    def test_numero_ausente_y_anclas_duplicadas(self):
        for lines in ([t['text'] for t in FRONTAL['tokens'] if not t['text'].startswith('NUMERO')],
                      [t['text'] for t in FRONTAL['tokens']] + ['NUMERO: 1234']):
            result = parser.parsear(documento(lines), TRASERA)
            self.assertEqual(result['numero_documento_normalizado'], '')
            self.assertIn('NUMERO_NO_ENCONTRADO', result['evidencia']['codigos'])

    def test_baja_confianza_no_se_acepta(self):
        front = documento([t['text'] for t in FRONTAL['tokens']])
        front['tokens'][2]['confidence'] = 10
        self.assertEqual(parser.parsear(front, TRASERA)['numero_documento_normalizado'], '')


class OCRFixture:
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = override_settings(PRIVATE_DOCUMENTS_ROOT=temp.name,
            OCR_DOCUMENTAL_VERSION_MOTOR='test-1', OCR_DOCUMENTAL_MODELO_SHA256='fixture',
            ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'])
        config.enable()
        self.addCleanup(config.disable)
        self.private_root = Path(temp.name)
        self.publisher_patch = patch.object(servicio, 'encolar', return_value=True)
        self.enqueue = self.publisher_patch.start()
        self.addCleanup(self.publisher_patch.stop)
        self.user = get_user_model().objects.create_user('ocr-synthetic')
        self.empresa = Empresa.objects.create(nombre='OCR sintetico')
        self.sesion = sesion_finalizada(self.user)
        self.p = OCR.objects.get(sesion=self.sesion)

    def run_ocr(self, pk=None):
        with patch.object(adapter, 'extraer', side_effect=[FRONTAL, TRASERA]) as mocked:
            servicio.procesar(pk or self.p.pk)
        self.p.refresh_from_db()
        return mocked

    def bind(self, sesion=None, documento='99000123', nombres='Persona ficticia'):
        sesion = sesion or self.sesion
        credito = Credito.objects.create(usuario=self.user, linea='LIBRANZA', estado='EN_REVISION',
            monto_solicitado=100000, plazo_solicitado=3)
        detail = CreditoLibranza.objects.create(credito=credito, empresa=self.empresa,
            cedula=documento, nombres=nombres, apellidos='Prueba sintetica')
        with transaction.atomic():
            captura.consumir_documentos(sesion=sesion, actor=self.user, credito=credito)
        return detail


class OCRServicioTest(OCRFixture, TestCase):
    def test_finalizacion_crea_pendiente_sin_motor_ni_credito(self):
        self.assertEqual(self.p.estado, 'PENDIENTE')
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(Comparacion.objects.exists())
        self.assertEqual(self.p.captura_frontal.sesion_id, self.sesion.pk)
        self.assertEqual(self.p.captura_trasera.lado, 'TRASERA')
        self.assertEqual(self.p.eventos.get().evento, 'OCR_CREADO')

    def test_mismos_inputs_no_duplican_y_tarea_duplicada_no_ejecuta(self):
        self.assertEqual(servicio.asegurar_procesamiento(self.sesion.pk).pk, self.p.pk)
        self.run_ocr()
        with patch.object(adapter, 'extraer') as mock:
            servicio.procesar(self.p.pk)
        mock.assert_not_called()
        self.assertEqual(OCR.objects.count(), 1)
        self.assertEqual(self.p.numero_intentos, 1)

    def test_ocr_primero_comparacion_al_consumir(self):
        self.run_ocr()
        self.assertFalse(Comparacion.objects.exists())
        detail = self.bind()
        comp = Comparacion.objects.get()
        self.assertEqual(comp.libranza, detail)
        self.assertEqual(set(comp.resultados.values()), {'COINCIDE'})
        self.assertFalse(comp.requiere_revision)
        servicio.sincronizar_comparacion_ocr(self.sesion.pk)
        self.assertEqual(Comparacion.objects.count(), 1)

    def test_contexto_primero_comparacion_al_terminar(self):
        self.bind()
        self.run_ocr()
        self.assertEqual(set(Comparacion.objects.get().resultados.values()), {'COINCIDE'})

    def test_discrepancia_no_modifica_solicitante_credito_ni_usuario(self):
        detail = self.bind(documento='88000123', nombres='Otro nombre')
        before_credit = list(Credito.objects.values())
        self.run_ocr()
        comp = Comparacion.objects.get()
        self.assertEqual(comp.resultados['numero_documento'], 'NO_COINCIDE')
        self.assertEqual(comp.resultados['nombres'], 'NO_COINCIDE')
        self.assertTrue(comp.requiere_revision)
        detail.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(detail.cedula, '88000123')
        self.assertEqual(self.user.username, 'ocr-synthetic')
        self.assertEqual(before_credit, list(Credito.objects.values()))

    def test_version_datos_preserva_comparacion_anterior(self):
        detail = self.bind()
        self.run_ocr()
        old = Comparacion.objects.get()
        detail.nombres = 'Nombre diferente'
        detail.save()
        servicio.sincronizar_comparacion_ocr(self.sesion.pk)
        old.refresh_from_db()
        self.assertFalse(old.vigente)
        self.assertEqual(old.snapshot['nombres'], 'Persona ficticia')
        self.assertEqual(Comparacion.objects.count(), 2)

    def test_normalizacion_nombres_no_fuzzy(self):
        detail = self.bind(nombres='  P\u00e9rsona   ficticia ')
        self.run_ocr()
        self.assertEqual(Comparacion.objects.get().resultados['nombres'], 'COINCIDE')

    def test_prestadores_ce_no_disponible_y_subsanacion_preserva_historico(self):
        solicitud = ContractorApplication.objects.create(usuario=self.user, empresa=self.empresa,
            tipo_documento='CE', numero_documento='99000123', nombres='PERSONA FICTICIA', apellidos='PRUEBA SINTETICA',
            tipo_contrato='PRESTACION_SERVICIOS', estado='DOCUMENTOS_PENDIENTES')
        first = sesion_finalizada(self.user, 'PRESTADORES', solicitud)
        with transaction.atomic():
            captura.consumir_documentos(sesion=first, actor=self.user, solicitud=solicitud)
        first_p = OCR.objects.get(sesion=first)
        self.run_ocr(first_p.pk)
        old = Comparacion.objects.get(procesamiento=first_p)
        self.assertEqual(set(old.resultados.values()), {'NO_DISPONIBLE'})
        second = sesion_finalizada(self.user, 'PRESTADORES', solicitud)
        with transaction.atomic():
            captura.consumir_documentos(sesion=second, actor=self.user, solicitud=solicitud)
        self.run_ocr(OCR.objects.get(sesion=second).pk)
        old.refresh_from_db()
        self.assertFalse(old.vigente)
        self.assertEqual(Comparacion.objects.filter(solicitud=solicitud).count(), 2)
        self.assertEqual(Comparacion.objects.filter(solicitud=solicitud, vigente=True).count(), 1)

    def test_reserva_abandonada_recuperacion_y_token_obsoleto(self):
        OCR.objects.filter(pk=self.p.pk).update(estado='PROCESANDO', reservado_hasta=timezone.now() - timedelta(seconds=1),
                                              token_ejecucion=uuid.uuid4(), numero_intentos=1)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(servicio.recuperar(), 1)
        self.assertEqual(servicio.recuperar(), 0)
        self.run_ocr()
        self.assertEqual(self.p.estado, 'COMPLETADO')
        self.assertEqual(self.p.numero_intentos, 2)

    def test_resultado_worker_obsoleto_no_publica(self):
        def recognize(c, config):
            OCR.objects.filter(pk=self.p.pk).update(token_ejecucion=uuid.uuid4())
            return FRONTAL if c.lado == 'FRONTAL' else TRASERA
        with patch.object(adapter, 'extraer', side_effect=recognize):
            servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.numero_documento_normalizado, '')
        self.assertEqual(self.p.estado, 'PROCESANDO')

    def test_reintentos_tecnicos_limitados(self):
        with patch.object(adapter, 'extraer', side_effect=adapter.ErrorOCR('OCR_TIMEOUT')):
            for _ in range(4):
                servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'FALLIDO')
        self.assertEqual(self.p.numero_intentos, 3)
        self.assertEqual(self.p.codigo_error, 'OCR_TIMEOUT')

    def test_motor_ausente_no_reintenta(self):
        with patch.object(adapter, 'extraer', side_effect=adapter.ErrorOCR('OCR_MOTOR_NO_DISPONIBLE')):
            servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'FALLIDO')
        self.assertEqual(servicio.recuperar(), 0)

    def test_recuperacion_no_terminales_limite_y_comando(self):
        with self.captureOnCommitCallbacks(execute=True):
            output = StringIO()
            call_command('reencolar_ocr_documental', limite=1, stdout=output)
        self.assertIn('1', output.getvalue())
        self.run_ocr()
        self.assertEqual(servicio.recuperar(), 0)

    def test_rollback_no_ocr_ni_publicacion(self):
        self.enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    sesion_finalizada(self.user)
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertEqual(OCR.objects.count(), 1)
        self.enqueue.assert_not_called()

    def test_broker_falla_solo_log_seguro_y_trabajo_pendiente(self):
        # Call the real publisher despite the fixture's isolation patch.
        from gestion_creditos.services.ocr_documental import logger
        self.publisher_patch.stop()
        with patch('gestion_creditos.tasks.procesar_ocr_documental_task.apply_async', side_effect=RuntimeError('PII-SECRETA')):
            with self.assertLogs(logger, level='WARNING') as logs:
                self.assertFalse(servicio.encolar(self.p.pk))
        self.assertNotIn('PII-SECRETA', str(logs.output))
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'PENDIENTE')

    def test_purga_abandonada_borra_pii_no_eventos(self):
        self.run_ocr()
        captura.revocar_sesion(sesion_id=self.sesion.pk, actor=self.user, producto='LIBRANZA')
        Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(seconds=1))
        captura.purgar_sesiones_expiradas()
        self.p.refresh_from_db()
        self.assertEqual(self.p.numero_documento_bruto, '')
        self.assertEqual(self.p.nombres_bruto, '')
        self.assertIsNone(self.p.fecha_nacimiento)
        self.assertEqual(self.p.confianza, {})
        self.assertIsNotNone(self.p.purgado_en)
        self.assertTrue(Evento.objects.filter(procesamiento_ocr=self.p).exists())

    def test_utilizada_no_purga_por_ttl_grant(self):
        self.bind()
        Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(days=1))
        self.run_ocr()
        captura.purgar_sesiones_expiradas()
        self.p.refresh_from_db()
        self.assertEqual(self.p.numero_documento_normalizado, '99000123')
        self.assertTrue(Comparacion.objects.exists())

    def test_revocacion_mientras_worker_no_publica(self):
        def recognize(c, config):
            captura.revocar_sesion(sesion_id=self.sesion.pk, actor=self.user, producto='LIBRANZA')
            return FRONTAL if c.lado == 'FRONTAL' else TRASERA
        with patch.object(adapter, 'extraer', side_effect=recognize):
            servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.codigo_error, 'OCR_INPUT_NO_VIGENTE')
        self.assertEqual(self.p.numero_documento_bruto, '')

    def test_eventos_y_storage_sin_pii(self):
        self.run_ocr()
        with self.assertRaises(ValueError):
            _ = self.p.captura_frontal.archivo.url
        events = str(list(Evento.objects.filter(procesamiento_ocr=self.p).values()))
        self.assertNotIn('99000123', events)
        self.assertNotIn('PERSONA FICTICIA', events)
        self.assertNotIn('identidad/', events)
        self.assertEqual(len(list(self.private_root.rglob('*.jpg'))), 2)

    def test_config_version_distinta_preserva_resultado(self):
        self.run_ocr()
        with override_settings(OCR_DOCUMENTAL_VERSION_MOTOR='test-2'):
            nuevo = servicio.asegurar_procesamiento(self.sesion.pk)
        self.p.refresh_from_db()
        self.assertNotEqual(nuevo.pk, self.p.pk)
        self.assertFalse(self.p.vigente)
        self.assertEqual(self.p.numero_documento_normalizado, '99000123')

    def test_commit_publica_solo_id(self):
        self.enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            sesion = sesion_finalizada(self.user)
        self.enqueue.assert_called_once_with(OCR.objects.get(sesion=sesion).pk)

    def test_worker_fuera_de_transaccion_de_reserva(self):
        # TestCase holds its own transaction; adapter must not add service savepoints.
        expected = len(connection.savepoint_ids)
        def recognize(c, config):
            self.assertEqual(len(connection.savepoint_ids), expected)
            return FRONTAL if c.lado == 'FRONTAL' else TRASERA
        with patch.object(adapter, 'extraer', side_effect=recognize):
            servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'COMPLETADO')

    def test_purga_durante_ocr_impide_republicar_pii(self):
        def recognize(c, config):
            captura.revocar_sesion(sesion_id=self.sesion.pk, actor=self.user, producto='LIBRANZA')
            Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(seconds=1))
            captura.purgar_sesiones_expiradas()
            return FRONTAL if c.lado == 'FRONTAL' else TRASERA
        with patch.object(adapter, 'extraer', side_effect=recognize):
            servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.codigo_error, 'OCR_ARCHIVOS_PURGADOS')
        self.assertEqual(self.p.numero_documento_bruto, '')
        self.assertFalse(Comparacion.objects.exists())

    def test_finalizada_procesa_ocr_despues_ttl_y_no_se_purga(self):
        Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(days=1))
        self.run_ocr()
        captura.purgar_sesiones_expiradas()
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'COMPLETADO')
        self.assertEqual(self.p.numero_documento_normalizado, '99000123')
        self.assertIsNone(self.p.purgado_en)

    @override_settings(CAPTURA_DOCUMENTAL_FINALIZADA_RETENTION_HOURS=72)
    def test_retencion_finalizada_purga_archivos_y_pii_ocr_idempotente(self):
        self.run_ocr()
        Sesion.objects.filter(pk=self.sesion.pk).update(finalizado_en=timezone.now() - timedelta(hours=73),
                                                       expira_en=timezone.now() - timedelta(hours=72))
        self.assertEqual(captura.purgar_sesiones_expiradas(), 2)
        self.p.refresh_from_db()
        self.assertEqual(self.p.numero_documento_bruto, '')
        self.assertEqual(self.p.numero_documento_normalizado, '')
        self.assertEqual(self.p.nombres_normalizado, '')
        self.assertEqual(self.p.evidencia, {})
        self.assertEqual(self.p.confianza, {})
        self.assertFalse(self.p.vigente)
        self.assertEqual(self.p.codigo_error, 'OCR_ARCHIVOS_PURGADOS')
        self.assertFalse(Comparacion.objects.filter(procesamiento=self.p).exists())
        self.assertFalse(list(self.private_root.rglob('*.jpg')))
        marca = self.p.purgado_en
        self.assertIsNotNone(marca)
        self.assertEqual(captura.purgar_sesiones_expiradas(), 0)
        servicio.procesar(self.p.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.purgado_en, marca)
        self.assertEqual(self.p.numero_documento_normalizado, '')
        eventos = str(list(Evento.objects.filter(sesion=self.sesion).values()))
        self.assertIn('RETENCION_FINALIZADA_VENCIDA', eventos)
        self.assertNotIn('99000123', eventos)
        self.assertNotIn('PERSONA FICTICIA', eventos)

    def test_comparacion_numero_no_leible(self):
        self.bind()
        front = documento([t['text'] for t in FRONTAL['tokens'] if not t['text'].startswith('NUMERO')])
        with patch.object(adapter, 'extraer', side_effect=[front, TRASERA]):
            servicio.procesar(self.p.pk)
        self.assertEqual(Comparacion.objects.get().resultados['numero_documento'], 'NO_LEIBLE')

    def test_errores_inesperados_no_llegan_a_logs_celery(self):
        from gestion_creditos.tasks import procesar_ocr_documental_task
        with patch.object(servicio, 'procesar', side_effect=RuntimeError('PII-SECRETA')):
            with self.assertLogs('gestion_creditos.tasks', level='ERROR') as logs:
                procesar_ocr_documental_task(self.p.pk)
        self.assertNotIn('PII-SECRETA', str(logs.output))

    def test_reserva_viva_no_se_roba(self):
        OCR.objects.filter(pk=self.p.pk).update(estado='PROCESANDO',
            reservado_hasta=timezone.now() + timedelta(minutes=2), token_ejecucion=uuid.uuid4())
        with patch.object(adapter, 'extraer') as mock:
            servicio.procesar(self.p.pk)
        mock.assert_not_called()
        self.assertEqual(servicio.recuperar(), 0)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real y select_for_update')
class OCRConcurrenciaPostgresTest(OCRFixture, TransactionTestCase):
    def race(self, operation):
        barrier, results = Barrier(2), Queue()
        def work():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                results.put(None)
            except Exception as exc:
                results.put(type(exc).__name__)
            finally:
                close_old_connections()
        threads = [Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
            self.assertFalse(t.is_alive())
        self.assertEqual([results.get_nowait() for _ in threads], [None, None])

    def test_doble_creacion_un_procesamiento(self):
        self.race(lambda: servicio.asegurar_procesamiento(self.sesion.pk))
        self.assertEqual(OCR.objects.count(), 1)

    def test_doble_tarea_reserva_unica(self):
        with patch.object(adapter, 'extraer', side_effect=lambda c, cfg: FRONTAL if c.lado == 'FRONTAL' else TRASERA) as mock:
            self.race(lambda: servicio.procesar(self.p.pk))
        self.p.refresh_from_db()
        self.assertEqual(self.p.numero_intentos, 1)
        self.assertEqual(mock.call_count, 2)

    def test_recuperacion_concurrente_publicacion_unica(self):
        self.enqueue.reset_mock()
        self.race(lambda: servicio.recuperar())
        self.assertEqual(self.enqueue.call_count, 1)

    def test_ocr_y_vinculacion_simultaneos_comparacion_unica(self):
        operations = Queue()
        operations.put(lambda: servicio.procesar(self.p.pk))
        operations.put(self.bind)
        with patch.object(adapter, 'extraer', side_effect=lambda c, cfg: FRONTAL if c.lado == 'FRONTAL' else TRASERA):
            self.race(lambda: operations.get_nowait()())
        self.assertEqual(Comparacion.objects.count(), 1)
        self.assertEqual(set(Comparacion.objects.get().resultados.values()), {'COINCIDE'})

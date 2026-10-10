from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador, ConfiguracionSimuladorPrestador, ContractorApplication, ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR, PredecisionPrestadorAudit,
)
from contractors.services.confirmacion_simulacion import confirmar_condiciones_simulacion
from contractors.services.evaluacion_versionado import construir_version_datos
from gestion_creditos.models import Credito, Empresa


class FixtureConfirmacion:
    host = 'contratistas.localhost'

    def setUp(self):
        super().setUp()
        temporal = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporal.cleanup)
        storage = override_settings(
            MEDIA_ROOT=temporal.name + '/media', PRIVATE_DOCUMENTS_ROOT=temporal.name + '/private',
            ALLOWED_HOSTS=['testserver', self.host], DATACREDITO_ENABLED=False,
            DATACREDITO_REAL_ENABLED=False,
        )
        storage.enable()
        self.addCleanup(storage.disable)
        self.usuario = get_user_model().objects.create_user(username='confirmacion-sintetica')
        self.empresa = Empresa.objects.create(nombre='Convenio sintetico', convenio_activo=True)
        self.configuracion = ConfiguracionSimuladorPrestador.objects.create(
            version='confirmacion-tests-v1', monto_minimo=Decimal('1000000'),
            monto_maximo=Decimal('10000000'), plazo_minimo_meses=3, plazo_maximo_meses=12,
        )
        self.solicitud = ContractorApplication.objects.create(
            usuario=self.usuario, empresa=self.empresa, numero_documento='000000017',
            nombres='Persona', apellidos='Sintetica', correo='synthetic@example.invalid',
            estado='DOCUMENTOS_CARGADOS', estado_analisis_contractual='COMPLETADO',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=400),
            valor_total_contrato=Decimal('12000000'), valor_pendiente_cobrar=Decimal('12000000'),
            forma_pago='MENSUAL', valor_mensual_contractual=Decimal('1000000'),
        )
        for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR:
            ContractorApplicationDocument.objects.create(
                solicitud=self.solicitud, tipo_documento=tipo, uploaded_by=self.usuario,
                archivo=SimpleUploadedFile('sintetico.pdf', b'%PDF-1.4 synthetic confirmation'),
            )
        self.payload = {'solicitud_id': self.solicitud.pk, 'monto': '3000000', 'plazo_meses': '4'}
        self.client.force_login(self.usuario)

    def confirmar(self, **kwargs):
        return confirmar_condiciones_simulacion(
            solicitud=self.solicitud, usuario=kwargs.pop('usuario', self.usuario),
            monto=kwargs.pop('monto', '3000000'), plazo_meses=kwargs.pop('plazo_meses', '4'),
        )

    def evidencia(self):
        self.solicitud.refresh_from_db()
        return (
            ContractorApplication.objects.filter(pk=self.solicitud.pk).values().get(),
            construir_version_datos(self.solicitud)[0],
            list(self.solicitud.timeline_operativo.values()),
            list(self.solicitud.auditorias_predecision.values()),
        )


class ConfirmacionSimulacionTest(FixtureConfirmacion, TestCase):
    def test_doble_post_no_modifica_ninguna_evidencia(self):
        with patch('celery.app.task.Task.apply_async') as publicar, \
                patch('contractors.views_admin.evaluar_solicitud_prestador') as evaluar:
            primero = self.client.post('/simular/', self.payload, HTTP_HOST=self.host)
            anterior = self.evidencia()
            segundo = self.client.post('/simular/', self.payload, HTTP_HOST=self.host)
            self.assertEqual(self.evidencia(), anterior)
            publicar.assert_not_called()
            evaluar.assert_not_called()
        self.assertEqual((primero.status_code, segundo.status_code), (302, 302))
        self.assertEqual(segundo.url, '/mi-credito/')
        self.assertEqual(self.solicitud.estado, 'EVALUACION_PENDIENTE')
        self.assertEqual(self.solicitud.timeline_operativo.count(), 1)
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())
        self.assertFalse(Credito.objects.exists())

    def test_reenvio_equivalente_no_recalcula_ni_consulta_configuracion(self):
        self.confirmar()
        anterior = self.evidencia()
        self.configuracion.activo = False
        self.configuracion.save(update_fields=['activo'])
        with patch('contractors.services.confirmacion_simulacion.obtener_configuracion_simulador_prestador') as config, \
                patch('contractors.services.confirmacion_simulacion.simular_credito_prestador_informativo') as simular:
            self.assertTrue(self.confirmar(monto='3000000.00', plazo_meses='04').reutilizada)
        config.assert_not_called()
        simular.assert_not_called()
        self.assertEqual(self.evidencia(), anterior)
        response = self.client.post('/simular/', self.payload, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.evidencia(), anterior)

    def test_reenvio_durante_evaluacion_conserva_auditoria_y_contexto(self):
        self.confirmar()
        ContractorApplication.objects.filter(pk=self.solicitud.pk).update(estado='EN_EVALUACION')
        PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud, version_datos='v', clave_idempotencia='a' * 64,
            version_politica='test', iniciada_en=timezone.now(), estado_ejecucion='EN_PROCESO',
            snapshot_entrada={'contexto_consulta': {'autorizacion_id': 17}},
        )
        anterior = self.evidencia()
        response = self.client.post('/simular/', self.payload, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.evidencia(), anterior)

    def test_cambio_legitimo_pendiente_versiona_una_vez(self):
        self.confirmar()
        anterior = self.evidencia()
        self.assertFalse(self.confirmar(monto='3500000', plazo_meses='5').reutilizada)
        actual = self.evidencia()
        self.assertNotEqual(actual[0]['simulada_en'], anterior[0]['simulada_en'])
        self.assertNotEqual(actual[1], anterior[1])
        self.assertEqual(self.solicitud.estado, 'EVALUACION_PENDIENTE')
        self.assertEqual(self.solicitud.timeline_operativo.count(), 2)
        self.assertTrue(self.confirmar(monto='3500000', plazo_meses='5').reutilizada)
        self.assertEqual(self.evidencia(), actual)

    def test_cambio_bloqueado_en_etapas_no_editables(self):
        self.confirmar()
        for estado in ['EN_EVALUACION', 'EVALUACION_COMPLETADA', 'PENDIENTE_APROBACION_PAGADOR',
                       'APROBADO_POR_PAGADOR', 'PENDIENTE_FIRMA', 'FIRMADO', 'NO_APROBADO']:
            with self.subTest(estado=estado):
                ContractorApplication.objects.filter(pk=self.solicitud.pk).update(estado=estado)
                anterior = self.evidencia()
                with self.assertRaisesMessage(ValidationError, 'etapa actual'):
                    self.confirmar(monto='3500000')
                self.assertEqual(self.evidencia(), anterior)
                self.assertTrue(self.confirmar().reutilizada)

    def test_documentos_y_analisis_se_revalidan_bajo_lock(self):
        self.solicitud.documentos.filter(tipo_documento='CONTRATO').delete()
        with self.assertRaisesMessage(ValidationError, 'documentos obligatorios'):
            self.confirmar()
        self.assertIsNone(self.evidencia()[0]['simulada_en'])

    def test_gate_abierto_impide_cambio_incluso_con_estado_pendiente(self):
        self.confirmar()
        auditoria = PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud, version_datos='v', clave_idempotencia='b' * 64,
            version_politica='test', iniciada_en=timezone.now(),
        )
        campos = {
            k.name: (1 if k.get_internal_type() == 'PositiveSmallIntegerField' else Decimal('1000000'))
            for k in AprobacionInternaPrestador._meta.fields
            if not k.null and (k.get_internal_type() == 'DecimalField' or k.get_internal_type() == 'PositiveSmallIntegerField')
        }
        campos['tasa_mensual_snapshot'] = Decimal('1.9')
        AprobacionInternaPrestador.objects.create(
            solicitud=self.solicitud, auditoria_predecision=auditoria,
            version_datos='v', version_politica='test', version_configuracion_financiera='test',
            **campos,
        )
        anterior = self.evidencia()
        with self.assertRaisesMessage(ValidationError, 'etapa actual'):
            self.confirmar(monto='3500000')
        self.assertEqual(self.evidencia(), anterior)
        self.assertTrue(self.confirmar().reutilizada)

    def test_analisis_bloqueado_no_confirma(self):
        ContractorApplication.objects.filter(pk=self.solicitud.pk).update(estado_analisis_contractual='BLOQUEADO')
        with self.assertRaisesMessage(ValidationError, 'analisis contractual'):
            self.confirmar()
        self.assertIsNone(self.evidencia()[0]['simulada_en'])

    def test_rangos_invalidos_no_persisten(self):
        response = self.client.post('/simular/', {**self.payload, 'monto': '999999999', 'plazo_meses': '99'}, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El monto debe estar entre')
        self.assertContains(response, 'El plazo debe estar entre')
        self.assertIsNone(self.evidencia()[0]['simulada_en'])

    def test_otro_usuario_no_puede_reutilizar_confirmacion(self):
        self.confirmar()
        otro = get_user_model().objects.create_user(username='otro-sintetico')
        anterior = self.evidencia()
        with self.assertRaises(PermissionDenied):
            self.confirmar(usuario=otro)
        self.assertEqual(self.evidencia(), anterior)

    def test_rollback_si_falla_evento(self):
        anterior = self.evidencia()
        with patch('contractors.services.evaluacion_audit.registrar_evento_timeline_prestador', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.confirmar()
        self.assertEqual(self.evidencia(), anterior)

    def test_cta_unica(self):
        response = self.client.get(f'/simular/?solicitud_id={self.solicitud.pk}', HTTP_HOST=self.host)
        self.assertContains(response, '>Confirmar condiciones y enviar solicitud</button>', count=1)
        self.assertNotContains(response, '>Continuar</button>')
        self.assertNotContains(response, '>Continuar con esta simulaci')


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL row locks')
class ConfirmacionConcurrenciaPostgresTest(FixtureConfirmacion, TransactionTestCase):
    def test_dos_posts_concurrentes_confirman_una_vez(self):
        barrier = Barrier(2)

        def enviar(_):
            close_old_connections()
            try:
                client = Client()
                usuario = get_user_model().objects.get(pk=self.usuario.pk)
                client.force_login(usuario)
                barrier.wait(timeout=15)
                response = client.post('/simular/', self.payload, HTTP_HOST=self.host)
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(enviar, range(2))), [302, 302])
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.monto_simulado, Decimal('3000000'))
        self.assertEqual(self.solicitud.timeline_operativo.count(), 1)
        self.assertFalse(self.solicitud.auditorias_predecision.exists())
        self.assertEqual(ContractorApplication.objects.count(), 1)

    def test_reenvio_concurrente_no_escribe_timestamps_ni_eventos(self):
        self.confirmar()
        anterior = self.evidencia()
        self.test_dos_posts_concurrentes_confirman_una_vez()
        self.assertEqual(self.evidencia(), anterior)

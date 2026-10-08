import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from contractors.models import ContractorApplication, PredecisionPrestadorAudit
from contractors.services import evaluacion_formal as servicio
from integrations.models import ConsultaDatacreditoSnapshot
from gestion_creditos.models import Empresa


class FixtureEvaluacion:
    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_superuser('lease-test', 'lease@example.com', 'test')
        self.solicitud = ContractorApplication.objects.create(usuario=self.actor,
            empresa=Empresa.objects.create(nombre='Empresa sintetica'), tipo_documento='CC',
            numero_documento='123456789', nombres='Persona', apellidos='Prueba', celular='3000000000',
            correo='persona@example.com', direccion='Direccion de prueba', cargo='Consultoria',
            estado='EVALUACION_PENDIENTE')
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def iniciar(self, modo=servicio.REUTILIZAR_SI_VIGENTE):
        return servicio._iniciar_evaluacion(solicitud=self.solicitud, usuario=self.actor,
            version_politica='politica_lease_test', version_score='score_lease_test',
            configuracion_financiera=None, version_configuracion_financiera='', modo_datacredito=modo)


class EvaluacionLeaseTest(FixtureEvaluacion, TestCase):
    def test_lease_reciente_vencido_recuperado_y_cierre_tardio_descartado(self):
        inicial = self.iniciar()
        self.assertTrue(self.iniciar().en_proceso)
        self.assertTrue(self.iniciar(servicio.FORZAR_CONSULTA).en_proceso)
        PredecisionPrestadorAudit.objects.filter(pk=inicial.auditoria.pk).update(
            iniciada_en=timezone.now()-timedelta(hours=1))
        recuperada = self.iniciar()
        self.assertEqual(recuperada.auditoria.error_codigo, 'evaluacion_en_proceso_expirada')
        self.assertTrue(recuperada.reutilizada)
        self.assertEqual(self.iniciar().auditoria.pk, inicial.auditoria.pk)
        tardia = servicio._finalizar_sin_decision(inicial.auditoria, resultado='NO_EVALUABLE',
            razones=('resultado viejo',), error_codigo='viejo', usuario=self.actor)
        self.assertEqual(tardia.auditoria.error_codigo, 'evaluacion_en_proceso_expirada')
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)

    def test_error_manual_crea_otro_intento_y_preserva_auditoria(self):
        inicial = self.iniciar(servicio.FORZAR_CONSULTA)
        servicio._finalizar_sin_decision(inicial.auditoria, resultado='ERROR_CONTROLADO',
            razones=('Error de prueba',), error_codigo='TIMEOUT', usuario=self.actor,
            estado_ejecucion='ERROR_CONTROLADO')
        nueva = self.iniciar(servicio.FORZAR_CONSULTA)
        self.assertNotEqual(inicial.auditoria.pk, nueva.auditoria.pk)
        self.assertTrue(self.iniciar(servicio.FORZAR_CONSULTA).en_proceso)
        inicial.auditoria.refresh_from_db()
        self.assertEqual(inicial.auditoria.error_codigo, 'TIMEOUT')
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 2)

    def test_fuentes_vencidas_no_reutilizan_resultado_como_vigente(self):
        inicial = self.iniciar()
        snapshot = ConsultaDatacreditoSnapshot.objects.create(ambiente='uat', servicio='decisor',
            documento_hash='0'*64, documento_enmascarado='****6789', fingerprint='0'*64, estado='EXITOSO',
            consultado_en=timezone.now()-timedelta(days=31), vigente_hasta=timezone.now()-timedelta(days=1),
            autorizacion_referencia='test')
        inicial.auditoria.snapshot_midecisor = snapshot
        inicial.auditoria.save(update_fields=['snapshot_midecisor'])
        completada = servicio._finalizar_sin_decision(inicial.auditoria, resultado='NO_EVALUABLE',
            razones=('Fuente ya no vigente',), error_codigo='', usuario=self.actor)
        nuevo = self.iniciar()
        self.assertTrue(nuevo.requiere_reconfirmacion)
        self.assertNotEqual(completada.auditoria.pk, nuevo.auditoria.pk)
        completada.auditoria.refresh_from_db()
        self.assertEqual(completada.auditoria.estado_ejecucion, 'COMPLETADA')

    def test_version_tecnica_cambia_clave_sin_reescribir_auditoria_historica(self):
        with patch.object(servicio, 'VERSION_NORMALIZADOR', 'normalizador-anterior'):
            inicial = self.iniciar()
            completada = servicio._finalizar_sin_decision(inicial.auditoria, resultado='NO_EVALUABLE',
                razones=('Fixture historico',), error_codigo='', usuario=self.actor)
        nueva = self.iniciar()
        self.assertNotEqual(completada.auditoria.clave_idempotencia, nueva.auditoria.clave_idempotencia)
        completada.auditoria.refresh_from_db()
        self.assertEqual(completada.auditoria.estado_ejecucion, 'COMPLETADA')


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
class EvaluacionLeaseConcurrenciaPostgresTest(FixtureEvaluacion, TransactionTestCase):
    def test_dos_recuperadores_cierran_una_sola_auditoria(self):
        inicial = self.iniciar()
        PredecisionPrestadorAudit.objects.filter(pk=inicial.auditoria.pk).update(
            iniciada_en=timezone.now()-timedelta(hours=1))
        barrera = threading.Barrier(2)
        def recuperar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return self.iniciar()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(recuperar) for _ in range(2)]
            resultados = [f.result(timeout=20) for f in futures]
        self.assertEqual({r.auditoria.pk for r in resultados}, {inicial.auditoria.pk})
        self.assertEqual({r.auditoria.error_codigo for r in resultados}, {'evaluacion_en_proceso_expirada'})
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)

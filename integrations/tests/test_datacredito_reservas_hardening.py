import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.services import datacredito_evaluacion as servicio
from integrations.datacredito.exceptions import DatacreditoProviderError, DatacreditoTimeoutError
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot
from integrations.tests import test_datacredito_snapshot_v2 as fixtures


class FixtureSnapshot:
    def setUp(self):
        super().setUp()
        fixtures.DatacreditoSnapshotV2Test.setUp(self)
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def proveedor(self):
        return fixtures.DatacreditoSnapshotV2Test._resultado_proveedor(self)

    def reservar(self):
        configuracion = obtener_configuracion_datacredito()
        fingerprint = servicio.construir_fingerprint_datacredito(
            solicitud=self.solicitud, servicio='decisor', autorizacion=self.autorizacion,
            configuracion=configuracion,
        )
        return servicio._reservar_consulta(
            solicitud=self.solicitud, autorizacion=self.autorizacion, configuracion=configuracion,
            servicio='decisor', documento_hash=servicio._hmac_documento(
                self.solicitud.numero_documento, configuracion.document_hash_secret,
            ), fingerprint=fingerprint, solicitado_por=self.usuario,
        )


@override_settings(**fixtures.CONFIGURACION_DATACREDITO_PRUEBA)
class ReservaHardeningTest(FixtureSnapshot, TestCase):
    def test_cache_se_revalida_al_reservar_despues_de_cache_miss(self):
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', return_value=self.proveedor()) as proveedor:
            primero = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
            buscar = servicio._buscar_snapshot_reutilizable
            with patch.object(servicio, '_buscar_snapshot_reutilizable', side_effect=[None, buscar(Snapshot.objects.get(pk=primero.snapshot_id).fingerprint)]):
                segundo = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
        self.assertEqual(primero.snapshot_id, segundo.snapshot_id)
        self.assertTrue(segundo.reutilizado)
        self.assertEqual(proveedor.call_count, 1)

    def test_lease_reciente_no_consulta_y_vencido_se_recupera_sin_replay(self):
        snapshot = self.reservar()
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador') as proveedor:
            reciente = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
            self.assertEqual(reciente.estado, 'EN_PROCESO')
            Snapshot.objects.filter(pk=snapshot.pk).update(vigente_hasta=timezone.now()-timedelta(seconds=1))
            vencido = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
            repetido = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
        proveedor.assert_not_called()
        self.assertEqual(vencido.estado, 'ERROR_TRANSITORIO')
        self.assertEqual(vencido.error_codigo, 'consulta_en_proceso_expirada')
        self.assertEqual(vencido.snapshot_id, repetido.snapshot_id)
        self.assertEqual(Snapshot.objects.count(), 1)

    def test_timeout_error_y_429_exigen_reintento_manual(self):
        for indice, error in enumerate((DatacreditoTimeoutError(), DatacreditoProviderError(http_status=429),
                      DatacreditoProviderError(http_status=401), DatacreditoProviderError(http_status=503))):
            with self.subTest(error=type(error).__name__, status=error.http_status):
                self.solicitud.apellidos = f'Prueba{indice}'
                with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', side_effect=error) as proveedor:
                    primero = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
                    segundo = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
                self.assertEqual(primero.estado, 'ERROR_TRANSITORIO')
                self.assertEqual(primero.snapshot_id, segundo.snapshot_id)
                self.assertEqual(proveedor.call_count, 1)

    def test_forzar_recuperacion_con_permiso_conserva_snapshot_anterior(self):
        snapshot = self.reservar()
        Snapshot.objects.filter(pk=snapshot.pk).update(vigente_hasta=timezone.now()-timedelta(seconds=1))
        staff = get_user_model().objects.create_superuser('refresh-test', 'refresh@example.com', 'test')
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', return_value=self.proveedor()) as proveedor:
            nuevo = servicio.obtener_evaluacion_datacredito_prestador(
                self.solicitud, modo=servicio.FORZAR_CONSULTA, solicitado_por=staff,
                justificacion='Recuperacion manual autorizada de prueba',
            )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.error_tipo, 'PROCESO_EXPIRADO')
        self.assertNotEqual(nuevo.snapshot_id, str(snapshot.pk))
        self.assertEqual(nuevo.estado, 'EXITOSO')
        proveedor.assert_called_once()
        tardio = servicio._finalizar_error(snapshot, solicitud=self.solicitud, usuario=staff,
            estado='ERROR_PERMANENTE', codigo='cierre_tardio', tipo='TARDIO')
        self.assertEqual(tardio.error_codigo, 'consulta_en_proceso_expirada')

    def test_fingerprint_versionado_invalida_solo_payload_semantico(self):
        configuracion = obtener_configuracion_datacredito()
        def fingerprint(config=configuracion):
            return servicio.construir_fingerprint_datacredito(solicitud=self.solicitud,
                servicio='decisor', autorizacion=self.autorizacion, configuracion=config)
        original = fingerprint()
        antiguo = {'ambiente':configuracion.environment, 'servicio':'decisor',
            'documento_hash':servicio._hmac_documento(self.solicitud.numero_documento, configuracion.document_hash_secret),
            'autorizacion_version':self.autorizacion.version_texto, 'autorizacion_texto_hash':self.autorizacion.texto_hash,
            'tipo_documento':self.solicitud.tipo_documento}
        antiguo_hash = hashlib.sha256(json.dumps(antiguo, sort_keys=True, separators=(',',':')).encode()).hexdigest()
        self.assertNotEqual(original, antiguo_hash)
        self.solicitud.apellidos = 'P\u00c9REZ OTRO'
        self.assertEqual(original, fingerprint())
        self.solicitud.apellidos = 'OTRO'
        self.assertNotEqual(original, fingerprint())
        self.assertNotEqual(original, fingerprint(replace(configuracion, environment='prod')))
        self.assertNotEqual(fingerprint(), fingerprint(replace(configuracion,
            credenciales_decisor=replace(configuracion.credenciales_decisor, username='otra-cuenta-sintetica'))))
        previo = fingerprint()
        with patch.object(servicio, 'VERSION_NORMALIZADOR', 'otra-version'):
            self.assertNotEqual(previo, fingerprint())
        self.assertEqual(len(original), 64)

    def test_respuesta_tardia_no_reescribe_lease_recuperado(self):
        def proveedor(*args, **kwargs):
            snapshot = Snapshot.objects.get(estado='EN_PROCESO')
            Snapshot.objects.filter(pk=snapshot.pk).update(vigente_hasta=timezone.now()-timedelta(seconds=1))
            recuperacion = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
            self.assertEqual(recuperacion.estado, 'ERROR_TRANSITORIO')
            return self.proveedor()
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', side_effect=proveedor) as consulta:
            tardia = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
        consulta.assert_called_once()
        self.assertEqual(tardia.error_codigo, 'consulta_en_proceso_expirada')
        self.assertFalse(Snapshot.objects.get().resultado_normalizado)

    def test_fingerprint_legacy_no_se_reinterpreta(self):
        legacy = Snapshot.objects.create(ambiente='uat', servicio='decisor', documento_hash='0'*64,
            documento_enmascarado='*****6789', fingerprint='1'*64, estado='EXITOSO',
            consultado_en=timezone.now(), vigente_hasta=timezone.now()+timedelta(days=30),
            autorizacion_referencia=str(self.autorizacion.pk), resultado_normalizado={'score_externo':600})
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', return_value=self.proveedor()) as consulta:
            nueva = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
        consulta.assert_called_once()
        legacy.refresh_from_db()
        self.assertEqual(legacy.fingerprint, '1'*64)
        self.assertEqual(legacy.resultado_normalizado, {'score_externo':600})
        self.assertNotEqual(nueva.snapshot_id, str(legacy.pk))


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks/conexiones PostgreSQL reales')
@override_settings(**fixtures.CONFIGURACION_DATACREDITO_PRUEBA)
class ReservaConcurrenciaPostgresTest(FixtureSnapshot, TransactionTestCase):
    def paralelo(self, fn):
        barrera = threading.Barrier(2)
        def ejecutar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ejecutar) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_consultas_equivalentes_una_reserva_http_fuera_de_atomic(self):
        self.consultas_paralelas()

    def consultas_paralelas(self, modo=servicio.REUTILIZAR_SI_VIGENTE):
        cantidad_inicial = Snapshot.objects.count()
        liberar = threading.Event()
        reutilizada = threading.Event()
        def proveedor(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(liberar.wait(timeout=10))
            return self.proveedor()
        def llamada():
            resultado = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud,
                modo=modo, solicitado_por=getattr(self, 'staff', None),
                justificacion='Recuperacion manual sintetica')
            if resultado.estado == 'EN_PROCESO':
                reutilizada.set()
                liberar.set()
            return resultado
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', side_effect=proveedor) as consulta:
            resultados = self.paralelo(llamada)
        self.assertTrue(reutilizada.is_set())
        self.assertEqual(len({r.snapshot_id for r in resultados}), 1)
        self.assertEqual(consulta.call_count, 1)
        self.assertEqual(Snapshot.objects.count(), cantidad_inicial+1)

    def test_dos_reintentos_manuales_recuperan_un_lease_una_sola_vez(self):
        self.staff = get_user_model().objects.create_superuser('refresh-pg', 'refresh-pg@example.com', 'test')
        anterior = self.reservar()
        Snapshot.objects.filter(pk=anterior.pk).update(vigente_hasta=timezone.now()-timedelta(seconds=1))
        self.consultas_paralelas(servicio.FORZAR_CONSULTA)
        anterior.refresh_from_db()
        self.assertEqual(anterior.error_tipo, 'PROCESO_EXPIRADO')
        self.assertEqual(Snapshot.objects.filter(estado='EXITOSO').count(), 1)

    def test_recuperacion_lease_vencido_un_solo_cierre(self):
        anterior = self.reservar()
        Snapshot.objects.filter(pk=anterior.pk).update(vigente_hasta=timezone.now()-timedelta(seconds=1))
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador') as consulta:
            resultados = self.paralelo(lambda: servicio.obtener_evaluacion_datacredito_prestador(self.solicitud))
        consulta.assert_not_called()
        self.assertEqual({r.snapshot_id for r in resultados}, {str(anterior.pk)})
        self.assertEqual({r.estado for r in resultados}, {'ERROR_TRANSITORIO'})
        self.assertEqual(Snapshot.objects.count(), 1)

    def test_cache_miss_antes_de_finalizacion_no_genera_segunda_consulta(self):
        cache_miss = threading.Event()
        finalizada = threading.Event()
        local = threading.local()
        original = servicio._buscar_snapshot_reutilizable
        def buscar(fingerprint):
            if getattr(local, 'segundo', False) and not getattr(local, 'ya_busco', False):
                local.ya_busco = True
                cache_miss.set()
                self.assertTrue(finalizada.wait(timeout=10))
                return None
            return original(fingerprint)
        def ejecutar(segundo):
            close_old_connections()
            local.segundo = segundo
            try:
                if not segundo:
                    self.assertTrue(cache_miss.wait(timeout=10))
                resultado = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
                if not segundo:
                    finalizada.set()
                return resultado
            finally:
                connections.close_all()
        with patch.object(servicio, '_buscar_snapshot_reutilizable', side_effect=buscar), patch.object(
            servicio, 'consultar_proveedor_datacredito_prestador', return_value=self.proveedor(),
        ) as consulta, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ejecutar, segundo) for segundo in (False, True)]
            resultados = [f.result(timeout=20) for f in futures]
        self.assertEqual(consulta.call_count, 1)
        self.assertEqual(len({r.snapshot_id for r in resultados}), 1)

"""Consent-bound reuse, with no network and immutable source snapshots."""
from copy import deepcopy
from datetime import timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services import datacredito_evaluacion as servicio
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.models import PredecisionPrestadorAudit
from contractors.services import evaluacion_formal
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot
from integrations.tests.test_datacredito_reservas_hardening import FixtureSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA)
class ReutilizacionSnapshotTest(FixtureSnapshot, TestCase):
    def setUp(self):
        super().setUp()
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador', return_value=self.proveedor()):
            resultado = servicio.obtener_evaluacion_datacredito_prestador(self.solicitud)
        self.snapshot = Snapshot.objects.get(pk=resultado.snapshot_id)
        self.historico = Snapshot.objects.values().get(pk=self.snapshot.pk)
        self.autorizacion_historica = type(self.autorizacion).objects.values().get(pk=self.autorizacion.pk)
        self.otra = deepcopy(self.solicitud)
        self.otra.pk = None
        self.otra.save()

    def aceptar(self):
        return registrar_autorizacion_datacredito_desde_solicitud(self.otra, usuario=self.otra.usuario)

    def consultar(self, modo=servicio.SOLO_CACHE):
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador') as proveedor:
            resultado = servicio.obtener_evaluacion_datacredito_prestador(self.otra, modo=modo)
        proveedor.assert_not_called()
        self.assertEqual(self.historico, Snapshot.objects.values().get(pk=self.snapshot.pk))
        self.assertEqual(self.autorizacion_historica,
                         type(self.autorizacion).objects.values().get(pk=self.autorizacion.pk))
        return resultado

    def test_misma_solicitud_y_autorizacion(self):
        self.otra = self.solicitud
        resultado = self.consultar()
        self.assertEqual(resultado.snapshot_id, str(self.snapshot.pk))
        self.assertTrue(resultado.reutilizado)

    def test_nueva_solicitud_mismo_titular_consentimiento_propio(self):
        autorizacion = self.aceptar()
        resultado = self.consultar()
        self.assertEqual(resultado.snapshot_id, str(self.snapshot.pk))
        evidencia = servicio.evidencia_uso_snapshot(
            self.snapshot, solicitud=self.otra, autorizacion=autorizacion,
        )
        self.assertEqual(evidencia['autorizacion_id'], autorizacion.pk)
        self.assertEqual(evidencia['autorizacion_origen_id'], self.autorizacion.pk)
        self.assertNotEqual(autorizacion.pk, self.autorizacion.pk)

    def test_sin_autorizacion_no_reutiliza(self):
        self.assertEqual(self.consultar().estado, 'AUTORIZACION_REQUERIDA')

    def test_consentimiento_retirado_no_reutiliza(self):
        self.aceptar()
        self.otra.autoriza_consulta_centrales = False
        self.otra.save(update_fields=['autoriza_consulta_centrales'])
        self.assertEqual(self.consultar().estado, 'AUTORIZACION_REQUERIDA')

    def test_version_juridica_no_vigente_no_reutiliza(self):
        self.aceptar()
        with patch('contractors.consentimiento_centrales.VERSION_CONSENTIMIENTO_CENTRALES', 'futura-v2'), \
             override_settings(DATACREDITO_AUTHORIZATION_TEXT_VERSION='futura-v2'):
            self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.otra))
            self.assertEqual(self.consultar().estado, 'AUTORIZACION_REQUERIDA')

    def test_otro_usuario_misma_identidad_no_intercambia_autorizaciones(self):
        self.otra.usuario = get_user_model().objects.create_user(username='otro-titular-sintetico')
        self.otra.save(update_fields=['usuario'])
        self.aceptar()
        resultado = self.consultar(modo=servicio.REUTILIZAR_SI_VIGENTE)
        self.assertEqual(resultado.error_codigo, 'reutilizacion_no_autorizada')
        self.assertIsNone(resultado.resultado_normalizado)

    def test_origen_retirado_bloquea_reutilizacion_sin_reconsulta_automatica(self):
        self.aceptar()
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales'])
        self.assertEqual(self.consultar().error_codigo, 'reutilizacion_no_autorizada')

    def test_reconsulta_manual_autorizada_crea_otro_origen_sin_mutar_el_anterior(self):
        autorizacion = self.aceptar()
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales'])
        self.assertEqual(self.consultar().error_codigo, 'reutilizacion_no_autorizada')
        staff = get_user_model().objects.create_superuser(username='reconsulta-autorizada-sintetica')
        with patch.object(servicio, 'consultar_proveedor_datacredito_prestador',
                          return_value=self.proveedor()) as proveedor:
            resultado = servicio.obtener_evaluacion_datacredito_prestador(
                self.otra, modo=servicio.FORZAR_CONSULTA, solicitado_por=staff,
                justificacion='Nueva consulta sintetica explicitamente autorizada',
            )
        proveedor.assert_called_once()
        self.assertEqual(resultado.estado, 'EXITOSO')
        self.assertNotEqual(resultado.snapshot_id, str(self.snapshot.pk))
        nuevo = Snapshot.objects.get(pk=resultado.snapshot_id)
        self.assertEqual(nuevo.autorizacion_referencia, str(autorizacion.pk))
        self.assertEqual(self.historico, Snapshot.objects.values().get(pk=self.snapshot.pk))

    def test_fingerprint_distinto_no_reutiliza(self):
        self.aceptar()
        for campo, valor in (('numero_documento', '900000097'), ('apellidos', 'OTRO'),
                             ('tipo_documento', 'CE')):
            with self.subTest(campo=campo):
                original = getattr(self.otra, campo)
                setattr(self.otra, campo, valor)
                self.otra.save(update_fields=[campo])
                self.assertEqual(self.consultar().estado, 'SIN_CACHE')
                setattr(self.otra, campo, original)
                self.otra.save(update_fields=[campo])

    def test_configuracion_producto_distinta_no_reutiliza(self):
        self.aceptar()
        with override_settings(DATACREDITO_ENVIRONMENT='prod'):
            self.assertEqual(self.consultar().estado, 'SIN_CACHE')

    def test_snapshot_vencido_no_reutiliza(self):
        self.aceptar()
        Snapshot.objects.filter(pk=self.snapshot.pk).update(vigente_hasta=timezone.now() - timedelta(seconds=1))
        self.historico = Snapshot.objects.values().get(pk=self.snapshot.pk)
        self.assertEqual(self.consultar().estado, 'SIN_CACHE')

    def test_carrera_cache_miss_reserva_valida_el_mismo_consentimiento(self):
        self.aceptar()
        with patch.object(servicio, '_buscar_snapshot_reutilizable', side_effect=[None, self.snapshot]):
            resultado = self.consultar(modo=servicio.REUTILIZAR_SI_VIGENTE)
        self.assertEqual(resultado.snapshot_id, str(self.snapshot.pk))

    def test_carrera_cache_miss_no_permite_otro_titular(self):
        self.otra.usuario = get_user_model().objects.create_user(username='ajeno-carrera-sintetico')
        self.otra.save(update_fields=['usuario'])
        self.aceptar()
        with patch.object(servicio, '_buscar_snapshot_reutilizable', side_effect=[None, self.snapshot]):
            resultado = self.consultar(modo=servicio.REUTILIZAR_SI_VIGENTE)
        self.assertEqual(resultado.error_codigo, 'reutilizacion_no_autorizada')

    def test_version_evaluacion_cambia_con_consentimiento_sin_cambiar_fingerprint(self):
        antes, _ = construir_version_datos(self.otra)
        self.aceptar()
        despues, entrada = construir_version_datos(self.otra)
        self.assertNotEqual(antes, despues)
        self.assertEqual(entrada['autorizaciones']['consulta_centrales_version'],
                         obtener_autorizacion_datacredito_vigente(self.otra).version_texto)
        self.assertEqual(self.consultar().snapshot_id, str(self.snapshot.pk))


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
@override_settings(
    **CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
    SECURE_SSL_REDIRECT=False, CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class ReutilizacionConcurrenciaPostgresTest(FixturePipelineFinanciero, TransactionTestCase):
    def test_doble_evaluacion_reutiliza_fuentes_y_un_solo_vinculo_auditado(self):
        inicial = self.evaluar()
        historicos = list(Snapshot.objects.order_by('pk').values())
        otra = self.nueva_solicitud_autorizada()
        barrera = threading.Barrier(2)
        iniciar = evaluacion_formal._iniciar_evaluacion

        def inicio_simultaneo(**kwargs):
            barrera.wait(timeout=20)
            return iniciar(**kwargs)

        def evaluar():
            close_old_connections()
            try:
                return evaluacion_formal.evaluar_solicitud_prestador(
                    otra, solicitado_por=self.staff, modo_datacredito='SOLO_CACHE',
                )
            finally:
                connections.close_all()

        with patch.object(evaluacion_formal, '_iniciar_evaluacion', side_effect=inicio_simultaneo), \
             patch.object(servicio, 'consultar_proveedor_datacredito_prestador') as proveedor, \
             ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(evaluar) for _ in range(2)]
            resultados = [f.result(timeout=40) for f in futuros]
        proveedor.assert_not_called()
        self.assertEqual(len({r.auditoria.pk for r in resultados}), 1)
        self.assertEqual(sum(not r.en_proceso and not r.reutilizada for r in resultados), 1)
        nueva = PredecisionPrestadorAudit.objects.get(pk=resultados[0].auditoria.pk)
        self.assertEqual(nueva.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(str(nueva.snapshot_midecisor_id), str(inicial.snapshot_midecisor_id))
        self.assertEqual(len(nueva.snapshot_salida['usos_snapshots']), 2)
        self.assertEqual(otra.auditorias_predecision.count(), 1)
        self.assertEqual(historicos, list(Snapshot.objects.order_by('pk').values()))
        self.assert_sin_originacion()

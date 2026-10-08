import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from contractors.models import (
    BandaScorePrestador, CambioPoliticaScorePrestadorAudit,
    ConfiguracionScorePrestador, ConfiguracionSimuladorPrestador,
)
from contractors.services.politica_financiera_prestador import PARAMETROS, VERSION
from contractors.services.preparacion_score_prod import (
    ARCHIVO_DEFINICION, definicion_score_prod, extraer_parametros_score_prod,
    preparar_politica_score_prod,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from usuarios.models import PerfilPagador


def crear_actor_preparacion():
    actor = get_user_model().objects.create_user(username='preparacion-inactiva', is_staff=True)
    actor.user_permissions.add(Permission.objects.get(
        content_type__app_label='contractors', codename='can_activate_contractor_score_policy',
    ))
    return actor


@override_settings(DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False)
class PreparacionScoreProdOperativaTest(TestCase):
    def setUp(self):
        self.financiera = ConfiguracionSimuladorPrestador.objects.create(version=VERSION, **PARAMETROS)
        self.actor = crear_actor_preparacion()
        self.documento = definicion_score_prod()
        self.parametros = {'fecha_vigencia_desde': '2026-10-08'}
        for target in ('requests.sessions.Session.request', 'httpx.Client.send', 'httpx.AsyncClient.send',
                       'contractors.services.politica_score.activar_politica_score_prestador'):
            guard = patch(target, side_effect=AssertionError('Operacion externa/activacion prohibida'))
            guard.start()
            self.addCleanup(guard.stop)

    def preparar(self, **kwargs):
        datos = dict(parametros=self.parametros, persistir=True, actor=self.actor,
                     motivo='Preparacion sintetica inactiva')
        datos.update(kwargs)
        return preparar_politica_score_prod(**datos)

    def comando(self, **kwargs):
        salida = StringIO()
        call_command('preparar_politica_score_prestadores_prod', stdout=salida, **kwargs)
        return json.loads(salida.getvalue())

    def comando_persistir(self, **kwargs):
        datos = dict(parametros=str(ARCHIVO_DEFINICION), fecha_vigencia='2026-10-08',
                     persistir_inactiva=True, actor_id=self.actor.pk, motivo='Preparacion sintetica inactiva')
        datos.update(kwargs)
        return self.comando(**datos)

    def archivo_parametros(self, documento):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        archivo = Path(temporal.name) / 'parametros.json'
        archivo.write_text(json.dumps(documento), encoding='utf-8')
        return str(archivo)

    def assert_sin_persistencia(self):
        self.assertFalse(ConfiguracionScorePrestador.objects.exists())
        self.assertFalse(BandaScorePrestador.objects.exists())
        self.assertFalse(CambioPoliticaScorePrestadorAudit.objects.exists())
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

    def test_json_oficial_extrae_solo_parametros_permitidos(self):
        esperados = {**self.documento['parametros_aprobados'], 'fecha_vigencia_desde': None}
        self.assertEqual(extraer_parametros_score_prod(self.documento), esperados)
        self.assertNotIn('bandas_aprobadas', esperados)
        datos = self.comando(parametros=str(ARCHIVO_DEFINICION))
        self.assertEqual(datos['parametros'], esperados)
        self.assertEqual(datos['pendientes'], ['fecha_vigencia_desde'])
        self.assertFalse(datos['activa'])
        self.assertFalse(datos['persistida'])
        self.assert_sin_persistencia()

    def test_json_oficial_con_fecha_dry_run_no_escribe_ni_altera_archivo(self):
        archivo = ARCHIVO_DEFINICION.read_bytes()
        financiera = list(ConfiguracionSimuladorPrestador.objects.values())
        datos = self.comando(parametros=str(ARCHIVO_DEFINICION), fecha_vigencia='2026-10-08')
        self.assertEqual(datos['pendientes'], [])
        self.assertEqual(datos['parametros']['fecha_vigencia_desde'], '2026-10-08')
        self.assertEqual(datos['peso_total'], '1.00')
        self.assertEqual(datos['configuracion_financiera_id'], self.financiera.pk)
        self.assertFalse(datos['persistida'])
        self.assertFalse(datos['activa'])
        self.assertEqual(ARCHIVO_DEFINICION.read_bytes(), archivo)
        self.assertEqual(list(ConfiguracionSimuladorPrestador.objects.values()), financiera)
        self.assert_sin_persistencia()

    def test_compatibilidad_json_plano_y_fecha_explicita(self):
        plano = {**self.documento['parametros_aprobados'], **self.parametros}
        datos = self.comando(parametros=self.archivo_parametros(plano))
        self.assertEqual(datos['parametros'], plano)
        self.assertFalse(datos['persistida'])
        self.assert_sin_persistencia()

    def test_metadata_contradictoria_no_se_ignora(self):
        casos = {
            'version': 'otra-version', 'configuracion_financiera_version': 'demo', 'activa': True,
            'estado_definicion': 'ACTIVA', 'bandas_aprobadas': [],
            'reglas_informativas': [], 'nota': 'Reglas diferentes',
        }
        for campo, valor in casos.items():
            with self.subTest(campo=campo):
                documento = deepcopy(self.documento)
                documento[campo] = valor
                with self.assertRaises(ValidationError):
                    preparar_politica_score_prod(parametros=documento)
                with self.assertRaises(CommandError):
                    self.comando(parametros=self.archivo_parametros(documento), fecha_vigencia='2026-10-08')
        self.assert_sin_persistencia()

    def test_version_documento_debe_coincidir_con_version_politica(self):
        self.documento['parametros_aprobados']['version_politica'] = 'otra-version'
        with self.assertRaisesMessage(ValidationError, 'version_politica'):
            extraer_parametros_score_prod(self.documento)

    def test_estructura_invalida_rechazada(self):
        for documento in ([], None, {'version': self.documento['version']},
                          {**self.documento, 'activar': True},
                          {**self.documento, 'parametros_aprobados': []},
                          {**self.documento, 'parametros_pendientes_aprobacion': {}},
                          {'bandas': []}):
            with self.subTest(documento=documento), self.assertRaises(ValidationError):
                preparar_politica_score_prod(parametros=documento if documento is not None else [])
        self.assert_sin_persistencia()

    def test_sin_fecha_no_persiste(self):
        with self.assertRaisesMessage(CommandError, 'fecha_vigencia_desde'):
            self.comando_persistir(fecha_vigencia=None)
        with self.assertRaises(ValidationError):
            self.preparar(parametros=self.documento)
        self.assert_sin_persistencia()

    def test_fecha_invalida_no_persiste(self):
        for fecha in ('no-fecha', '2026-02-30', ''):
            with self.subTest(fecha=fecha), self.assertRaises(CommandError):
                self.comando_persistir(fecha_vigencia=fecha)
        self.assert_sin_persistencia()

    def test_persistencia_cli_exige_actor_existente_y_motivo(self):
        for kwargs in ({'actor_id': None}, {'actor_id': -1}, {'motivo': ''}, {'motivo': '   '}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CommandError):
                self.comando_persistir(**kwargs)
        self.assert_sin_persistencia()

    def test_actor_y_motivo_no_implican_persistencia(self):
        with self.assertRaises(CommandError):
            self.comando(actor_id=self.actor.pk, motivo='No debe implicar escritura')
        self.assert_sin_persistencia()

    def test_servicio_exige_actor_autenticado_y_motivo(self):
        for kwargs in ({'actor': None}, {'actor': AnonymousUser()}, {'motivo': ''}, {'motivo': None},
                       {'motivo': '   '}, {'persistir': 'true'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionDenied):
                self.preparar(**kwargs)
        self.assert_sin_persistencia()

    def test_no_staff_rechazado_en_servicio_y_cli(self):
        self.actor.is_staff = False
        self.actor.save(update_fields=['is_staff'])
        with self.assertRaises(PermissionDenied):
            self.preparar()
        with self.assertRaises(CommandError):
            self.comando_persistir()
        self.assert_sin_persistencia()

    def test_actor_inactivo_rechazado(self):
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.preparar()
        with self.assertRaises(CommandError):
            self.comando_persistir()
        self.assert_sin_persistencia()

    def test_sin_permiso_rechazado_en_servicio_y_cli(self):
        self.actor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.preparar()
        with self.assertRaises(CommandError):
            self.comando_persistir()
        self.assert_sin_persistencia()

    def test_pagador_con_permiso_rechazado_en_servicio_y_cli(self):
        PerfilPagador.objects.create(usuario=self.actor, empresa=Empresa.objects.create(nombre='Empresa test'))
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.assertTrue(self.actor.has_perm('contractors.can_activate_contractor_score_policy'))
        with self.assertRaises(PermissionDenied):
            self.preparar()
        with self.assertRaises(CommandError):
            self.comando_persistir()
        self.assert_sin_persistencia()

    @override_settings(RUNNING_TESTS=False)
    def test_persistencia_explicita_autorizada_inactiva_y_auditada(self):
        financiera = list(ConfiguracionSimuladorPrestador.objects.values())
        datos = self.comando_persistir()
        politica = ConfiguracionScorePrestador.objects.get(pk=datos['politica_id'])
        self.assertTrue(datos['persistida'])
        self.assertFalse(datos['activa'])
        self.assertFalse(politica.activa)
        self.assertEqual(datos['bandas'], 5)
        self.assertEqual(politica.fecha_vigencia_desde, date(2026, 10, 8))
        cursor = 0
        for banda in politica.bandas.order_by('score_min'):
            self.assertEqual(banda.score_min, cursor)
            cursor = banda.score_max + 1
        self.assertEqual(cursor, 1001)
        audit = CambioPoliticaScorePrestadorAudit.objects.get()
        self.assertEqual(audit.actor, self.actor)
        self.assertEqual(audit.motivo, 'Preparacion sintetica inactiva')
        self.assertEqual(audit.accion, 'SIN_CAMBIO')
        self.assertTrue(audit.snapshot_nuevo['preparacion_inactiva'])
        self.assertEqual(list(ConfiguracionSimuladorPrestador.objects.values()), financiera)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

    def test_repeticion_equivalente_no_duplica_ni_modifica_auditoria(self):
        datos = self.comando_persistir()
        anterior = list(CambioPoliticaScorePrestadorAudit.objects.values())
        politica_anterior = list(ConfiguracionScorePrestador.objects.values())
        bandas_anteriores = list(BandaScorePrestador.objects.values())
        self.preparar(parametros={**self.parametros, 'peso_midecisor': Decimal('0.45000'),
                                 'fecha_vigencia_desde': date(2026, 10, 8)}, motivo='Otro reintento')
        self.assertEqual(self.comando_persistir(), datos)
        self.assertEqual(list(CambioPoliticaScorePrestadorAudit.objects.values()), anterior)
        self.assertEqual(list(ConfiguracionScorePrestador.objects.values()), politica_anterior)
        self.assertEqual(list(BandaScorePrestador.objects.values()), bandas_anteriores)

    def test_parametros_distintos_no_redefinen_politica(self):
        with self.assertRaises(ValidationError):
            self.preparar(parametros={**self.parametros, 'peso_midecisor': '0.40'})
        self.assert_sin_persistencia()

    def test_version_existente_distinta_falla_cerrada(self):
        politica = self.preparar()
        for campo, valor in (('fecha_vigencia_desde', date(2026, 10, 9)),
                             ('score_premium_min', 860), ('activa', True)):
            with self.subTest(campo=campo):
                original = getattr(politica, campo)
                ConfiguracionScorePrestador.objects.filter(pk=politica.pk).update(**{campo: valor})
                antes = list(CambioPoliticaScorePrestadorAudit.objects.values())
                with self.assertRaises(ValidationError):
                    self.preparar()
                self.assertEqual(list(CambioPoliticaScorePrestadorAudit.objects.values()), antes)
                self.assertEqual(ConfiguracionScorePrestador.objects.get().activa, campo == 'activa')
                ConfiguracionScorePrestador.objects.filter(pk=politica.pk).update(**{campo: original})

    def test_banda_existente_distinta_no_se_sobrescribe(self):
        politica = self.preparar()
        banda = politica.bandas.get(nombre='PREMIUM')
        BandaScorePrestador.objects.filter(pk=banda.pk).update(monto_maximo=Decimal('9999999'))
        with self.assertRaises(ValidationError):
            self.preparar()
        banda.refresh_from_db()
        self.assertEqual(banda.monto_maximo, Decimal('9999999'))
        self.assertEqual(CambioPoliticaScorePrestadorAudit.objects.count(), 1)

    def test_bandas_incompletas_no_se_reparan_implicitamente(self):
        politica = self.preparar()
        politica.bandas.filter(nombre='PREMIUM').delete()
        with self.assertRaises(ValidationError):
            self.preparar()
        self.assertEqual(politica.bandas.count(), 4)
        self.assertEqual(CambioPoliticaScorePrestadorAudit.objects.count(), 1)

    def test_error_de_auditoria_revierte_toda_la_preparacion(self):
        with patch('contractors.services.preparacion_score_prod.CambioPoliticaScorePrestadorAudit.objects.get_or_create',
                   side_effect=RuntimeError('Fallo sintetico')), self.assertRaises(RuntimeError):
            self.preparar()
        self.assert_sin_persistencia()


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks reales PostgreSQL; SQLite no los representa.')
class PreparacionScoreProdConcurrenciaPostgresTest(TransactionTestCase):
    def setUp(self):
        ConfiguracionSimuladorPrestador.objects.create(version=VERSION, **PARAMETROS)
        self.actor = crear_actor_preparacion()

    def ejecutar_concurrentes(self, fechas):
        barrera = Barrier(2)
        resultados = []

        def preparar(fecha):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrera.wait(timeout=15)
                politica = preparar_politica_score_prod(parametros={'fecha_vigencia_desde': fecha},
                    persistir=True, actor=actor, motivo='Concurrencia sintetica')
                resultados.append(politica.pk)
            except Exception as exc:
                resultados.append(exc)
            finally:
                connections.close_all()

        hilos = [Thread(target=preparar, args=(fecha,)) for fecha in fechas]
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP prohibido')):
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join(timeout=30)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(len(resultados), 2)
        return resultados

    def test_doble_preparacion_equivalente_una_politica_y_auditoria(self):
        resultados = self.ejecutar_concurrentes(['2026-10-08', '2026-10-08'])
        self.assertTrue(all(isinstance(r, int) for r in resultados), resultados)
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(ConfiguracionScorePrestador.objects.count(), 1)
        self.assertFalse(ConfiguracionScorePrestador.objects.get().activa)
        self.assertEqual(BandaScorePrestador.objects.count(), 5)
        self.assertEqual(CambioPoliticaScorePrestadorAudit.objects.count(), 1)

    def test_preparaciones_incompatibles_una_gana_otra_falla_cerrada(self):
        resultados = self.ejecutar_concurrentes(['2026-10-08', '2026-10-09'])
        self.assertEqual(sum(isinstance(r, int) for r in resultados), 1, resultados)
        self.assertEqual(sum(isinstance(r, ValidationError) for r in resultados), 1, resultados)
        self.assertEqual(ConfiguracionScorePrestador.objects.count(), 1)
        self.assertFalse(ConfiguracionScorePrestador.objects.get().activa)
        self.assertEqual(BandaScorePrestador.objects.count(), 5)
        self.assertEqual(CambioPoliticaScorePrestadorAudit.objects.count(), 1)

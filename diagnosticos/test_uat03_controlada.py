"""Operational bundle tests: all provider HTTP terminates at a synthetic adapter."""
from contextlib import ExitStack
from copy import deepcopy
import builtins
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import gettempdir
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.db import connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from contractors.datacredito.dto import ResultadoNormalizadoDatacreditoPrestador
from contractors.models import AprobacionInternaPrestador, PredecisionPrestadorAudit
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from gestion_creditos.models import Credito
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from integrations.tests.test_midecisor_shadow import fixture_hc14_tx05_sintetico
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures


with patch.dict(os.environ, {'APROBADO_UAT_HELPERS_DIR': os.environ.get('APROBADO_UAT_HELPERS_DIR', gettempdir())}):
    spec = importlib.util.spec_from_file_location('uat03_operativa_test', Path(__file__).with_name('aprobado_uat03_controlada.py'))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
REAL_REQUEST = requests.sessions.Session.request
RELEASE_PRUEBA = 'a123456789abcdef0123456789abcdef01234567'


def git_sintetico(comando, **kwargs):
    respuestas = {
        ('rev-parse', '--verify', 'HEAD^{commit}'): RELEASE_PRUEBA + '\n',
        ('rev-parse', '--verify', runner.RELEASE_TRACKING + '^{commit}'): RELEASE_PRUEBA + '\n',
        ('merge-base', '--is-ancestor', runner.RELEASE_BASE, 'HEAD'): '',
        ('diff', '--quiet', 'HEAD', '--'): '',
        ('ls-remote', '--exit-code', 'origin', runner.RELEASE_REF): RELEASE_PRUEBA + '\t' + runner.RELEASE_REF + '\n',
    }
    if comando[0] != 'git' or tuple(comando[1:]) not in respuestas:
        raise AssertionError('Comando Git inesperado; red real prohibida')
    return SimpleNamespace(stdout=respuestas[tuple(comando[1:])], returncode=0)


def integracion_alias(solicitud, politica):
    from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable as resolver
    return resolver(solicitud, politica)


def integracion_solo_nombre(solicitud, politica):
    from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable
    return 'resolver_ingreso_evaluable(solicitud, politica)'


class PreflightFailClosedTest(SimpleTestCase):
    def setUp(self):
        runner._release_remoto.cache_clear()
        self.addCleanup(runner._release_remoto.cache_clear)
        self.entorno = patch.dict(os.environ, {'APROBADO_UAT_RELEASE_SHA': RELEASE_PRUEBA})
        self.entorno.start()
        self.addCleanup(self.entorno.stop)
        git_patch = patch.object(runner.subprocess, 'run', side_effect=git_sintetico)
        self.git = git_patch.start()
        self.addCleanup(git_patch.stop)

    def gate(self):
        ruta = Mock(side_effect=lambda path: Path(path).resolve())
        ruta.cwd.return_value = Path('/root/aprobado_web').resolve()
        with patch.object(runner.perfiles, 'Path', ruta), patch.object(connection, 'vendor', 'postgresql'), \
             override_settings(DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False,
                               PRESTADORES_AUTO_EVALUACION_ENABLED=False), \
             patch('requests.sessions.Session.send', side_effect=AssertionError('HTTP prohibido')):
            return runner.preflight(10, 310, 1)

    def test_release_compatible_y_remoto_se_verifica_una_vez_por_proceso(self):
        self.assertEqual(runner.validar_release(), RELEASE_PRUEBA)
        self.assertEqual(runner.validar_release(), RELEASE_PRUEBA)
        comandos = [c.args[0][1:] for c in self.git.call_args_list]
        self.assertEqual(comandos.count(['ls-remote', '--exit-code', 'origin', runner.RELEASE_REF]), 1)
        self.assertEqual(comandos.count(['diff', '--quiet', 'HEAD', '--']), 2)
        self.assertEqual(comandos.count(['rev-parse', '--verify', 'HEAD^{commit}']), 2)

    def test_release_requiere_pin_sha40_sin_default(self):
        for valor in ('', '4c', 'a' * 39, 'a' * 41, 'z' * 40, RELEASE_PRUEBA + '\n'):
            with self.subTest(valor=valor), patch.dict(os.environ, {'APROBADO_UAT_RELEASE_SHA': valor}):
                with self.assertRaisesRegex(runner.base.StopUAT, 'release_sha40_explicito_requerido'):
                    self.gate()
        self.git.assert_not_called()

    def test_release_base_no_pasa_aunque_el_pin_y_git_coincidan(self):
        with patch.dict(os.environ, {'APROBADO_UAT_RELEASE_SHA': runner.RELEASE_BASE}):
            with self.assertRaisesRegex(runner.base.StopUAT, 'release_base_sin_ingreso'):
                self.gate()
        self.git.assert_not_called()

    def test_head_y_tracking_incorrectos_bloquean_antes_de_red(self):
        for ref in ('HEAD', runner.RELEASE_TRACKING):
            def distinto(comando, **kwargs):
                if comando[1:] == ['rev-parse', '--verify', ref + '^{commit}']:
                    return SimpleNamespace(stdout=runner.RELEASE_BASE, returncode=0)
                return git_sintetico(comando, **kwargs)
            with self.subTest(ref=ref), patch.object(runner.subprocess, 'run', side_effect=distinto) as git:
                with self.assertRaisesRegex(runner.base.StopUAT, 'release_incorrecto'):
                    self.gate()
                self.assertFalse(any(c.args[0][1] == 'ls-remote' for c in git.call_args_list))

    def test_release_remoto_distinto_o_ambiguo_no_se_cachea(self):
        for salida in (runner.RELEASE_BASE + '\t' + runner.RELEASE_REF, '',
                       RELEASE_PRUEBA + '\trefs/heads/otra',
                       (RELEASE_PRUEBA + '\t' + runner.RELEASE_REF + '\n') * 2):
            def remoto_distinto(comando, **kwargs):
                if comando[1] == 'ls-remote':
                    return SimpleNamespace(stdout=salida, returncode=0)
                return git_sintetico(comando, **kwargs)
            with self.subTest(salida=salida), patch.object(runner.subprocess, 'run', side_effect=remoto_distinto):
                with self.assertRaisesRegex(runner.base.StopUAT, 'release_remoto_no_coincide'):
                    self.gate()
        self.assertEqual(runner._release_remoto.cache_info().currsize, 0)

    def test_ancestria_y_codigo_sucio_o_error_git_bloquean(self):
        for operacion, codigo in (('merge-base', 'release_no_desciende_de_base'),
                                  ('diff', 'codigo_rastreado_modificado'),
                                  ('ls-remote', 'release_remoto_no_coincide')):
            def error_git(comando, **kwargs):
                if comando[1] == operacion:
                    return SimpleNamespace(stdout='', returncode=1)
                return git_sintetico(comando, **kwargs)
            with self.subTest(operacion=operacion), patch.object(runner.subprocess, 'run', side_effect=error_git):
                with self.assertRaisesRegex(runner.base.StopUAT, codigo):
                    self.gate()
        with patch.object(runner.subprocess, 'run', side_effect=OSError('git no disponible')):
            with self.assertRaisesRegex(runner.base.StopUAT, 'git_no_verificable'):
                self.gate()

    def test_modulos_ingreso_ausentes_bloquean_antes_de_consultar_solicitud(self):
        original = builtins.__import__
        for modulo in ('contractors.services.ingreso_evaluable', 'contractors.services.politica_ingreso_publicacion'):
            def sin_modulo(nombre, *args, **kwargs):
                if nombre == modulo:
                    raise ModuleNotFoundError('core no instalado')
                return original(nombre, *args, **kwargs)
            with self.subTest(modulo=modulo), patch('builtins.__import__', side_effect=sin_modulo):
                with self.assertRaisesRegex(runner.base.StopUAT, 'core_ingreso_no_instalado_o_incompatible'):
                    self.gate()

    def test_integracion_inspecciona_llamada_real_y_acepta_alias(self):
        from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable
        runner._exigir_integracion(integracion_alias, resolver_ingreso_evaluable)
        with self.assertRaisesRegex(runner.base.StopUAT, 'integracion_ingreso_ausente'):
            runner._exigir_integracion(integracion_solo_nombre, resolver_ingreso_evaluable)

    def test_integracion_faltante_bloquea_antes_de_schema_y_http(self):
        with patch('contractors.services.oferta_financiera.calcular_oferta_evaluada', new=integracion_solo_nombre):
            with self.assertRaisesRegex(runner.base.StopUAT, 'integracion_ingreso_ausente'):
                self.gate()

    def test_predecision_sin_habilitacion_por_solicitud_bloquea_antes_de_http(self):
        with patch('contractors.services.predecision._evaluar_predecision_dual', new=integracion_solo_nombre):
            with self.assertRaisesRegex(runner.base.StopUAT, 'integracion_ingreso_ausente'):
                self.gate()

    def test_migracion_0022_debe_estar_aplicada_realmente(self):
        with patch.object(runner, 'MigrationRecorder') as recorder:
            recorder.Migration.objects.filter.return_value.exists.return_value = False
            with self.assertRaisesRegex(runner.base.StopUAT, 'migracion_ingreso_no_aplicada'):
                self.gate()
            recorder.Migration.objects.filter.assert_called_once_with(app='contractors', name=runner.MIGRACION_INGRESO)
            recorder.assert_not_called()

    def test_error_sql_en_registro_migraciones_es_controlado(self):
        with patch.object(runner, 'MigrationRecorder') as recorder:
            recorder.Migration.objects.filter.return_value.exists.side_effect = runner.DatabaseError('tabla ausente')
            with self.assertRaisesRegex(runner.base.StopUAT, 'migracion_ingreso_no_verificable'):
                self.gate()

    def test_migracion_registrada_sin_columna_tampoco_pasa(self):
        with patch.object(runner, 'MigrationRecorder') as recorder, \
             patch.object(connections[connection.alias], 'cursor') as cursor:
            recorder.Migration.objects.filter.return_value.exists.return_value = True
            cursor.return_value.__enter__.return_value.execute.side_effect = runner.DatabaseError('columna ausente')
            with self.assertRaisesRegex(runner.base.StopUAT, 'schema_ingreso_no_instalado'):
                self.gate()

    def test_core_integrado_y_schema_completo(self):
        from contractors.services.politica_ingreso_publicacion import obtener_politica_para_solicitud
        with patch.object(runner, 'MigrationRecorder') as recorder, \
             patch.object(connections[connection.alias], 'cursor') as cursor, \
             patch.object(connection.introspection, 'get_table_description',
                          side_effect=AssertionError('Introspeccion prohibida por solo_select')):
            recorder.Migration.objects.filter.return_value.exists.side_effect = [True, False]
            self.assertIs(runner.validar_core_ingreso(), obtener_politica_para_solicitud)
            schema = cursor.return_value.__enter__.return_value.execute
            self.assertEqual([c.args[0] for c in schema.call_args_list], [
                'SELECT "politica_ingreso_evaluable", "version", "version_politica", "activa" '
                'FROM "contractors_configuracionscoreprestador" LIMIT 0',
                'SELECT "version_politica_simulacion" FROM "contractors_contractorapplication" LIMIT 0',
                'SELECT "politica_nueva_id", "snapshot_nuevo" '
                'FROM "contractors_cambiopoliticascoreprestadoraudit" LIMIT 0',
                'SELECT "solicitud_id", "tipo_evento", "metadata" FROM "contractors_timelineprestador_v2" LIMIT 0',
            ])
            with self.assertRaisesRegex(runner.base.StopUAT, 'migracion_ingreso_no_aplicada'):
                runner.validar_core_ingreso()
            self.assertEqual(schema.call_count, 4)
            self.assertEqual(recorder.Migration.objects.filter.return_value.exists.call_count, 2)
            recorder.assert_not_called()

    def politica(self):
        return SimpleNamespace(version=runner.VERSION_INGRESO, version_politica=runner.VERSION_INGRESO,
                               activa=False, politica_ingreso_evaluable=dict(runner.REGLAS_INGRESO),
                               fecha_vigencia_desde=runner.perfiles.timezone.localdate(), fecha_vigencia_hasta=None)

    def test_politica_explicita_publicada_y_asociada(self):
        politica = self.politica()
        solicitud = SimpleNamespace(version_politica_simulacion=runner.VERSION_INGRESO)
        with patch('contractors.models.CambioPoliticaScorePrestadorAudit.objects.filter') as audit, \
             patch('contractors.models.TimelinePrestador.objects.filter') as timeline:
            audit.return_value.exists.return_value = True
            timeline.return_value.exists.return_value = True
            self.assertIs(runner.validar_politica_ingreso(solicitud, lambda s: politica), politica)
            audit.assert_called_once_with(politica_nueva=politica, snapshot_nuevo__ingreso_publicado=True)
            timeline.assert_called_once_with(solicitud=solicitud, tipo_evento='DATOS_MODIFICADOS',
                metadata__tipo='ADOPCION_INGRESO', metadata__version_politica=runner.VERSION_INGRESO)

    def test_politica_legacy_no_asociada_inactiva_o_reglas_distintas_bloquean(self):
        solicitud = SimpleNamespace(version_politica_simulacion='prestadores-score-prod-v1')
        with self.assertRaisesRegex(runner.base.StopUAT, 'politica_ingreso_no_asociada'):
            runner.validar_politica_ingreso(solicitud, Mock())
        solicitud.version_politica_simulacion = runner.VERSION_INGRESO
        for campo, valor in (('version', 'prestadores-score-prod-v1'), ('activa', True),
                             ('politica_ingreso_evaluable', {}),
                             ('politica_ingreso_evaluable', {**runner.REGLAS_INGRESO,
                                                           'semantica_midecisor': 'NETO_MENSUAL_ESTIMADO'}),
                             ('politica_ingreso_evaluable', {**runner.REGLAS_INGRESO,
                                                           'descuento_contractual_porcentaje': '20.00'})):
            politica = self.politica()
            setattr(politica, campo, valor)
            with self.subTest(campo=campo, valor=valor):
                with self.assertRaisesRegex(runner.base.StopUAT, 'politica_ingreso_no_publicada_o_incompatible'):
                    runner.validar_politica_ingreso(solicitud, lambda s: politica)

    def test_publicacion_y_asociacion_sin_auditoria_no_pasan(self):
        solicitud = SimpleNamespace(version_politica_simulacion=runner.VERSION_INGRESO)
        for publicada, asociada, codigo in ((False, True, 'publicacion_ingreso_no_auditada'),
                                            (True, False, 'asociacion_ingreso_no_auditada')):
            with self.subTest(codigo=codigo), \
                 patch('contractors.models.CambioPoliticaScorePrestadorAudit.objects.filter') as audit, \
                 patch('contractors.models.TimelinePrestador.objects.filter') as timeline:
                audit.return_value.exists.return_value = publicada
                timeline.return_value.exists.return_value = asociada
                with self.assertRaisesRegex(runner.base.StopUAT, codigo):
                    runner.validar_politica_ingreso(solicitud, lambda s: self.politica())


@unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell no disponible')
class LauncherPreflightUAT03Test(SimpleTestCase):
    def test_preparacion_pin_ausente_y_release_base_nunca_son_pass(self):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        entorno = dict(os.environ)
        entorno.pop('APROBADO_UAT_RELEASE_SHA', None)
        for argumentos in ([], ['-ReleaseSha', runner.RELEASE_BASE]):
            ruta = str(Path(__file__).with_name('preflight_uat03_actual.ps1')).replace("'", "''")
            comando = (f"$fuente=Get-Content -Raw -LiteralPath '{ruta}'; "
                       "& ([scriptblock]::Create($fuente)) " + ' '.join(argumentos))
            resultado = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', comando],
                capture_output=True, text=True, env=entorno, timeout=30)
            self.assertEqual(resultado.returncode, 2, resultado.stderr)
            salida = json.loads(resultado.stdout)
            self.assertEqual(salida['resultado'], 'BLOCKED_NO_DESPLEGADO')
            self.assertFalse(salida['autorizacion_consumo'])

    def test_launcher_operativo_importa_modulo_instalado_no_funcion_ast(self):
        fuente = Path(__file__).with_name('preflight_uat03_actual.ps1').read_text(encoding='utf-8')
        self.assertIn("[string]$Modo = 'PREPARACION'", fuente)
        self.assertIn("importlib.util.find_spec(modulo)", fuente)
        self.assertIn("runpy.run_module(modulo, run_name='__main__')", fuente)
        self.assertNotIn('ast.', fuente)
        self.assertNotIn('base64', fuente)
        self.assertNotIn('PASS_ELEGIBILIDAD_FUENTES_CANDIDATA', fuente)


class SalvaguardasUAT03Test(SimpleTestCase):
    def test_solicitudes_anteriores_nunca_se_consultan(self):
        for sid in (3, 4, 9):
            with self.subTest(sid=sid), self.assertRaises(runner.base.StopUAT):
                runner.validar_destino(sid)

    def test_observador_no_expone_dto_en_repr_y_limpia(self):
        holder = runner.CandidatoEnMemoria()
        holder.dto = ResultadoNormalizadoDatacreditoPrestador(score_externo=667)
        self.assertNotIn('667', repr(holder))
        holder.limpiar()
        self.assertIsNone(holder.dto)
        self.assertIsNone(holder.cliente)

    def test_helper_diagnostico_no_puede_invocarse_sin_fence(self):
        with self.assertRaises(runner.diagnostico.OperacionBloqueada):
            runner.diagnostico.evaluar_dto_en_memoria(solicitud=None, politica=None,
                                                    normalizado=None, historial=None)

    def test_importacion_no_ejecuta_fuentes(self):
        self.assertNotIn('FORZAR_CONSULTA', Path(runner.__file__).read_text(encoding='utf-8'))
        self.assertEqual(runner.PERFILES_SHA, 'ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173')

    def test_presupuesto_no_hace_http_dentro_de_atomic_ni_repite_producto(self):
        cfg = runner.perfiles.obtener_configuracion_datacredito()
        presupuesto = runner.base.TransportBudget(cfg, enabled=True)
        presupuesto.current = 'historial'
        sesion = presupuesto.session()
        request = requests.Request('POST', cfg.historial_url).prepare()
        enviados = []
        presupuesto.send_original = lambda *a, **kw: (enviados.append(kw) or SimpleNamespace(status_code=200))
        with patch.object(connection, 'in_atomic_block', True):
            with self.assertRaises(runner.base.StopUAT):
                presupuesto.send(sesion, request)
        self.assertEqual(enviados, [])
        with patch.object(connection, 'in_atomic_block', False):
            presupuesto.send(sesion, request)
            with self.assertRaises(runner.base.StopUAT):
                presupuesto.send(sesion, request)
        self.assertEqual(len(enviados), 1)
        self.assertIs(enviados[0]['allow_redirects'], False)
        sesion.close()


CONFIGURACION = {
    **CONFIGURACION_DATACREDITO_PRUEBA,
    'DATACREDITO_ENABLED': False, 'DATACREDITO_REAL_ENABLED': False,
    'PRESTADORES_AUTO_EVALUACION_ENABLED': False,
    'DATACREDITO_HDC_PRODUCT_ID_EXPLICIT': True,
    'DATACREDITO_HDC_INFO_ACCOUNT_TYPE_EXPLICIT': True,
    'DATACREDITO_HDC_CHANNEL_NAME': 'CONEXRED-01', 'DATACREDITO_HDC_CHANNEL_TYPE': '42',
    'ALLOWED_HOSTS': ['testserver', 'localhost', 'contratistas.localhost'],
    'SECURE_SSL_REDIRECT': False, 'CONTRACTORS_CONTRACT_AI_ENABLED': False, 'OPENAI_API_KEY': '',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
}


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL aislado y autocommit real')
@override_settings(**CONFIGURACION)
class UAT03FuentesPostgresTest(FixturePipelineFinanciero, TransactionTestCase):
    reset_sequences = True
    ingreso_automatico = True
    semantica_ingreso_test = 'NO_CONFIRMADA'

    def setUp(self):
        super().setUp()
        self.raw = fixture_hc14_tx05_sintetico()
        self.raw['content']['respuesta']['informacionRiesgo']['alertas'] = []
        self.hdc = {'ReportHDCplus': {'productResult': {'responseCode': '14'}}}
        self.eventos = []
        self.http = []
        self.redirect = False
        self.timeout = False

    def adapter_sintetico(self, adapter, request, **kwargs):
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(adapter.max_retries.total, 0)
        self.http.append(request.url)
        cfg = runner.perfiles.obtener_configuracion_datacredito()
        response = requests.Response()
        response.status_code = 200
        response.request = request
        if request.url == cfg.token_url:
            datos = {'access_token': 'token-sintetico-no-real', 'token_type': 'Bearer', 'expires_in': 600}
        elif request.url == cfg.historial_url:
            if self.timeout:
                raise requests.exceptions.Timeout('timeout sintetico')
            if self.redirect:
                response.status_code = 302
                response.headers['Location'] = 'https://example.invalid/no-seguir'
            datos = self.hdc
        elif request.url == cfg.midecisor_url:
            datos = self.raw
        else:
            self.fail('Endpoint no permitido')
        response._content = json.dumps(datos).encode('utf-8')
        return response

    def ejecutar(self, *, autorizar=True, modo='fuentes_y_diagnostico'):
        entorno = {
            'APROBADO_UAT_RELEASE_SHA': RELEASE_PRUEBA,
            'APROBADO_UAT_MODO': modo,
            'APROBADO_UAT_SOLICITUD_ID': str(self.solicitud.pk),
            'APROBADO_UAT_PROPIETARIO_ID': str(self.usuario.pk),
            'APROBADO_UAT_ACTOR_ID': str(self.staff.pk),
            'APROBADO_UAT_CREDENCIALES': 'CONFIRMADAS_POR_OPERADOR_COMO_UAT',
            'APROBADO_UAT_AUTORIZACION': (f'AUTORIZO_UAT03_FUENTES_Y_DIAGNOSTICO_SOLICITUD_{self.solicitud.pk}'
                                        if autorizar else ''),
        }
        def guardar(evento, **campos):
            self.eventos.append({'evento': evento, **campos})
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, entorno))
            ruta_posix = Mock(side_effect=lambda path: Path(path).resolve())
            ruta_posix.cwd.return_value = Path('/root/aprobado_web').resolve()
            stack.enter_context(patch.object(runner.perfiles, 'Path', ruta_posix))
            stack.enter_context(patch.object(runner.subprocess, 'run', side_effect=git_sintetico))
            for modulo in (runner, runner.perfiles, runner.base):
                stack.enter_context(patch.object(modulo, 'emit', side_effect=guardar))
            stack.enter_context(patch('requests.sessions.Session.request', new=REAL_REQUEST))
            stack.enter_context(patch('requests.adapters.HTTPAdapter.send',
                new=lambda adapter, request, **kwargs: self.adapter_sintetico(adapter, request, **kwargs)))
            # Allow the real shadow parser only in this dedicated operational observer.
            self.resultado = runner.main()

    def assert_sin_avance(self):
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'EVALUACION_PENDIENTE')
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(settings.DATACREDITO_ENABLED)
        self.assertFalse(settings.DATACREDITO_REAL_ENABLED)

    def diagnostico(self):
        return next(e for e in self.eventos if e['evento'] == 'DIAGNOSTICO_CANDIDATO_UAT')

    def test_hc14_hdc_sin_informacion_no_pierde_candidato_ni_persiste_score(self):
        holder = runner.CandidatoEnMemoria()
        with patch.object(runner, 'CandidatoEnMemoria', return_value=holder):
            self.ejecutar()
        self.assertIsNone(holder.dto)
        self.assertIsNone(holder.cliente)
        self.assertEqual(len(self.http), 4)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 2)
        snapshot = ConsultaDatacreditoSnapshot.objects.get(servicio='decisor')
        self.assertEqual(snapshot.estado, 'ERROR_PERMANENTE')
        self.assertEqual(snapshot.codigo_funcional, 'HC14_TX05')
        self.assertEqual(snapshot.resultado_normalizado, {})
        diag = self.diagnostico()
        self.assertIn('datacredito_score', diag['componentes_disponibles'])
        self.assertEqual(diag['riesgo'], 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(diag['oferta'], 'NO_EVALUABLE')
        self.assertFalse(diag['elegibilidad_crediticia_final'])
        self.assertEqual(diag['llamadas_diagnostico'], {'red_bloqueada': 0, 'publicaciones_bloqueadas': 0, 'sql_bloqueado': 0})
        self.assert_sin_avance()

    def test_hdc_completo_permite_diagnostico_financiero_sin_aprobacion(self):
        self.hdc = deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())
        self.ejecutar()
        self.assertEqual(self.diagnostico()['oferta'], 'OFERTA_CALCULADA')
        self.assertEqual(self.diagnostico()['riesgo'], 'PREAPROBADO_READ_ONLY')
        self.assertTrue(self.diagnostico()['rollback_obligatorio'])
        self.assert_sin_avance()

    def test_ingreso_insuficiente_conserva_sin_oferta(self):
        self.hdc = deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())
        self.verificar_ingreso('100000')
        self.ejecutar()
        self.assertEqual(self.diagnostico()['oferta'], 'SIN_OFERTA')
        self.assert_sin_avance()

    def test_sin_autorizacion_operativa_no_hay_http_ni_snapshot(self):
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar(autorizar=False)
        self.assertEqual(self.http, [])
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())

    def test_preflight_es_read_only_y_no_consume(self):
        self.ejecutar(modo='preflight')
        self.assertEqual(self.http, [])
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assert_sin_avance()

    def test_gate_preflight_debe_rechazar_core_sin_resolvedor_antes_del_consumo(self):
        import builtins
        original = builtins.__import__

        def core_anterior(nombre, *args, **kwargs):
            if nombre == 'contractors.services.ingreso_evaluable':
                raise ModuleNotFoundError('resolvedor_no_instalado')
            return original(nombre, *args, **kwargs)

        with patch('builtins.__import__', side_effect=core_anterior):
            with self.assertRaises(runner.base.StopUAT,
                                   msg='GATE_BLOCKED: preflight admite un core sin resolvedor y puede fallar despues de consumir'):
                self.ejecutar(modo='preflight')
        self.assertEqual(self.http, [])

    def test_sin_ingreso_documental_consulta_elegible_diagnostico_contractual_aislado(self):
        from contractors.services.ingreso_neto import registrar_ingreso_neto
        registrar_ingreso_neto(solicitud=self.solicitud, actor=self.staff, invalidar=True,
                              observacion='Fixture sin ingreso documental vigente')
        self.ejecutar(modo='preflight')
        self.assertEqual(self.http, [])
        self.assertTrue(any(e.get('ingreso_estado') == 'PENDIENTE_RESOLUCION_AUTOMATICA' for e in self.eventos))
        self.ejecutar()
        self.assertEqual(self.diagnostico()['ingreso_fuente'], 'CONTRATO_ESTIMADO')
        self.assertTrue(self.diagnostico()['ingreso_estimado_no_habilita_originacion'])
        self.assertEqual(self.diagnostico()['oferta'], 'NO_EVALUABLE')
        self.assertEqual(len(self.http), 4)
        self.assert_sin_avance()

    def test_diagnostico_ingreso_sin_fuentes_no_es_consulta_ni_aprobacion(self):
        from contractors.services.ingreso_neto import registrar_ingreso_neto
        registrar_ingreso_neto(solicitud=self.solicitud, actor=self.staff, invalidar=True,
                              observacion='Fixture para diagnostico sin fuentes')
        self.ejecutar(modo='diagnostico_ingreso')
        evento = next(e for e in self.eventos if e['evento'] == 'INGRESO_DIAGNOSTICO_SIN_FUENTES')
        self.assertEqual(evento['fuente'], 'CONTRATO_ESTIMADO')
        self.assertTrue(evento['valor_determinado'])
        self.assertFalse(evento['aprobacion_habilitada'])
        self.assertEqual(self.http, [])
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assert_sin_avance()

    def test_descubrimiento_read_only_no_identifica_automaticamente_a_la_persona(self):
        from diagnosticos.descubrir_uat03 import descubrir
        resultado = descubrir()
        self.assertEqual(resultado['resultado'], 'REQUIERE_IDENTIFICACION_OPERATIVA')
        fila = next(f for f in resultado['candidatas'] if f['solicitud_id'] == self.solicitud.pk)
        self.assertEqual(fila['propietario_id'], self.usuario.pk)
        self.assertTrue(fila['consentimiento_del_propietario'])
        self.assertTrue(fila['ingreso_vigente'])
        self.assertNotIn(self.solicitud.numero_documento, json.dumps(resultado))
        self.assertEqual(resultado['http'], 0)
        self.assert_sin_avance()

    def test_redirect_detiene_sin_segunda_fuente_ni_retry(self):
        self.redirect = True
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar()
        self.assertEqual(len(self.http), 2)
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.filter(servicio='decisor').exists())
        self.assert_sin_avance()

    def test_timeout_detiene_y_prohibe_repeticion(self):
        self.timeout = True
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar()
        antes = list(self.http)
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar()
        self.assertEqual(self.http, antes)
        self.assert_sin_avance()

    def test_sin_score_no_habilita_diagnostico_ni_reconsulta(self):
        self.raw['content']['respuesta']['informacionRiesgo']['score'] = None
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar()
        self.assertFalse(any(e['evento'] == 'DIAGNOSTICO_CANDIDATO_UAT' for e in self.eventos))
        self.assertEqual(len(self.http), 4)
        self.assert_sin_avance()

    def test_diagnostico_no_puede_escribir_y_conserva_fuentes_oficiales(self):
        def intentar_escritura(*args, **kwargs):
            self.solicitud.__class__.objects.filter(pk=self.solicitud.pk).update(estado='EN_EVALUACION')
        with patch.object(runner.diagnostico, 'evaluar_score_prestador', side_effect=intentar_escritura):
            with self.assertRaises(runner.diagnostico.OperacionBloqueada):
                self.ejecutar()
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 2)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.get(servicio='decisor').resultado_normalizado, {})
        self.assert_sin_avance()

    def test_salida_no_expone_score_raw_ni_identidad_y_reintento_no_consume(self):
        self.ejecutar()
        antes = list(ConsultaDatacreditoSnapshot.objects.values())
        eventos = json.dumps(self.eventos)
        for clave in ('"score_externo"', '"score_final"', '"raw_sanitizado"', '"numero_documento"',
                      '"667"', 'token-sintetico-no-real', self.solicitud.numero_documento):
            self.assertNotIn(clave, eventos)
        with self.assertRaises(runner.base.StopUAT):
            self.ejecutar()
        self.assertEqual(len(self.http), 4)
        self.assertEqual(list(ConsultaDatacreditoSnapshot.objects.values()), antes)
        self.assertTrue(any(e.get('temporales_liberados') for e in self.eventos))
        self.assert_sin_avance()

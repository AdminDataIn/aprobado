"""Upgrade the released schema with synthetic history; never connect to operations."""
from datetime import timedelta
from decimal import Decimal
import hashlib
from io import StringIO
import subprocess
import unittest

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from contractors.services.politica_financiera_prestador import PARAMETROS, VERSION


ANTES = [('contractors', '0021_evaluacionprestadorjob')]
DESPUES = [('contractors', '0022_configuracionscoreprestador_politica_ingreso_evaluable')]
BASE = '4c536c148e946e13aefdc9c0012fdeccdb048123'


@unittest.skipUnless(connection.vendor == 'postgresql', 'Gate requiere PostgreSQL aislado')
class GateMigracionIngresoTest(TransactionTestCase):
    def setUp(self):
        db = settings.DATABASES['default']
        self.assertEqual(db['HOST'], '127.0.0.1')
        self.assertEqual(connection.settings_dict['NAME'], db['TEST']['NAME'])
        self.assertTrue(db['TEST']['NAME'].startswith('test_'))
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(self.latest))
        anteriores = [n for n in self.latest if n[0] != 'contractors'] + ANTES
        executor.migrate(anteriores)
        apps = executor.loader.project_state(anteriores).apps
        User = apps.get_model('auth', 'User')
        Empresa = apps.get_model('gestion_creditos', 'Empresa')
        Finance = apps.get_model('contractors', 'ConfiguracionSimuladorPrestador')
        Policy = apps.get_model('contractors', 'ConfiguracionScorePrestador')
        self.PolicyAnterior = Policy
        App = apps.get_model('contractors', 'ContractorApplication')
        Audit = apps.get_model('contractors', 'PredecisionPrestadorAudit')
        Snapshot = apps.get_model('integrations', 'ConsultaDatacreditoSnapshot')
        hoy = timezone.localdate()
        financiera = Finance.objects.create(version=VERSION, activo=True, **PARAMETROS)
        self.policy = Policy.objects.create(
            nombre='Politica sintetica previa al release', version='prestadores-score-prod-v1',
            version_politica='prestadores-score-prod-v1', version_score='prestadores-score-prod-v1',
            activa=True, fecha_vigencia_desde=hoy-timedelta(days=30), configuracion_financiera=financiera,
            peso_datacredito=0, peso_midecisor=Decimal('.45'), peso_hdcplus=0,
            peso_capacidad=Decimal('.30'), peso_comportamiento=Decimal('.08'),
            peso_riesgo=Decimal('.12'), peso_referencias=Decimal('.05'),
            cuota_ingreso_maxima=Decimal('.30'), monto_maximo_politica=Decimal('10000000'),
            plazo_maximo_politica=8, tasa_mensual_referencia=Decimal('2.2000'),
        )
        empresa = Empresa.objects.create(nombre='Empresa sintetica gate', convenio_activo=True)
        # Load only the already released versioning function, not provider code.
        original = subprocess.run(
            ['git', 'show', BASE + ':contractors/services/evaluacion_versionado.py'],
            check=True, capture_output=True, text=True, encoding='utf-8',
        ).stdout
        ns = {'__name__': '_versionado_release_4c536c1'}
        exec(compile(original, '<versionado-release-base>', 'exec'), ns)
        self.versions = {}
        for sid in (4, 9):
            usuario = User.objects.create(username=f'uat-historica-sintetica-{sid}')
            App.objects.create(pk=sid, usuario_id=usuario.pk, empresa_id=empresa.pk,
                               estado='EN_REVISION', tipo_documento='CC', numero_documento=f'99900000{sid}')
            from contractors.models import ContractorApplication
            solicitud = ContractorApplication.objects.get(pk=sid)
            version, entrada = ns['construir_version_datos'](solicitud)
            self.versions[sid] = version
            Audit.objects.create(solicitud_id=sid, version_datos=version,
                                 clave_idempotencia=hashlib.sha256(str(sid).encode()).hexdigest(),
                                 estado_ejecucion='COMPLETADA', resultado='NO_EVALUABLE',
                                 version_politica=self.policy.version_politica, snapshot_entrada=entrada,
                                 snapshot_salida={'resultado':'NO_EVALUABLE'}, iniciada_en=timezone.now())
            for servicio, estado, codigo in (
                ('historial', 'SIN_INFORMACION', '14'),
                ('decisor', 'ERROR_PERMANENTE', 'HC14_TX05'),
            ):
                Snapshot.objects.create(
                    ambiente='uat', servicio=servicio, estado=estado, codigo_http=200,
                    codigo_funcional=codigo, resultado_normalizado={},
                    fingerprint=hashlib.sha256(f'{sid}:{servicio}'.encode()).hexdigest(),
                    documento_hash='a'*64, documento_enmascarado='SINTETICO',
                    autorizacion_referencia=str(sid), consultado_en=timezone.now(),
                    vigente_hasta=timezone.now() + timedelta(days=30),
                )
        self.tables = set(connection.introspection.table_names())
        self.old_policy = Policy.objects.values().get(pk=self.policy.pk)
        self.historical = {
            'apps': list(App.objects.order_by('pk').values()),
            'audits': list(Audit.objects.order_by('pk').values()),
            'snapshots': list(Snapshot.objects.order_by('pk').values()),
        }
        executor = MigrationExecutor(connection)
        self.plan = executor.migration_plan(DESPUES)
        self.assertEqual([(m.name, backwards) for m, backwards in self.plan], [(DESPUES[0][1], False)])
        executor.migrate(DESPUES)

    def test_schema_aditivo_no_destruye_historial_4_y_9(self):
        from contractors.models import ContractorApplication, ConfiguracionScorePrestador, PredecisionPrestadorAudit
        from integrations.models import ConsultaDatacreditoSnapshot
        self.assertEqual(set(connection.introspection.table_names()), self.tables)
        policy = ConfiguracionScorePrestador.objects.get(pk=self.policy.pk)
        valores = ConfiguracionScorePrestador.objects.values().get(pk=policy.pk)
        agregado = valores.pop('politica_ingreso_evaluable')
        self.assertEqual(valores, self.old_policy)
        self.assertEqual(agregado, {})
        self.assertEqual(list(ContractorApplication.objects.order_by('pk').values()), self.historical['apps'])
        self.assertEqual(list(PredecisionPrestadorAudit.objects.order_by('pk').values()), self.historical['audits'])
        self.assertEqual(list(ConsultaDatacreditoSnapshot.objects.order_by('pk').values()), self.historical['snapshots'])
        policy.full_clean()

    def test_gate_interpretacion_historica_no_debe_cambiar_sin_nueva_politica(self):
        from contractors.models import ContractorApplication, ConfiguracionScorePrestador
        from contractors.services.evaluacion_versionado import construir_version_datos
        policy = ConfiguracionScorePrestador.objects.get(pk=self.policy.pk)
        self.assertEqual(policy.version_politica, self.old_policy['version_politica'])
        for sid, anterior in self.versions.items():
            actual, _ = construir_version_datos(ContractorApplication.objects.get(pk=sid))
            self.assertTrue(actual == anterior,
                            'GATE_BLOCKED: version_datos historico cambio sin editar la solicitud ni versionar la politica')

    def test_codigo_legacy_puede_insertar_politica_tras_upgrade(self):
        from contractors.models import ConfiguracionScorePrestador
        valores = {k: v for k, v in self.old_policy.items()
                   if k not in {'id', 'created_at', 'updated_at'}}
        valores.update(version='legacy-insert-tras-upgrade', version_politica='legacy-insert-tras-upgrade', activa=False)
        nueva = self.PolicyAnterior.objects.create(**valores)
        actual = ConfiguracionScorePrestador.objects.get(pk=nueva.pk)
        self.assertEqual(actual.politica_ingreso_evaluable, {})

    def test_checks_y_plan_tras_upgrade(self):
        for command, options in (
            ('check', {}),
            ('makemigrations', {'check': True, 'dry_run': True}),
            ('migrate', {'plan': True}),
        ):
            salida = StringIO()
            call_command(command, stdout=salida, stderr=salida, **options)
            if command == 'migrate':
                self.assertFalse(MigrationExecutor(connection).migration_plan(self.latest))
            print(f'GATE_CONFIG {command}: PASS (PostgreSQL aislado tras 0022)')

    def test_capacidad_legacy_conserva_calculo_del_release(self):
        from contractors.models import ContractorApplication, ConfiguracionScorePrestador
        from contractors.score.componentes import componente_capacidad_dual
        original = subprocess.run(
            ['git', 'show', BASE + ':contractors/score/componentes.py'],
            check=True, capture_output=True, text=True, encoding='utf-8',
        ).stdout
        ns = {'__name__': '_componentes_release_base'}
        exec(compile(original, '<componentes-release-base>', 'exec'), ns)
        policy = ConfiguracionScorePrestador.objects.get(pk=self.policy.pk)
        for sid in (4, 9):
            solicitud = ContractorApplication.objects.get(pk=sid)
            anterior = ns['componente_capacidad_dual'](solicitud, None, policy)
            actual = componente_capacidad_dual(solicitud, None, policy)
            self.assertEqual(actual, anterior)

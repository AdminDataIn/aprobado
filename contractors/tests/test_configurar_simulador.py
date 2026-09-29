import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone

from contractors.management.commands.configurar_politica_prestadores_demo import (
    CONFIGURACION_FINANCIERA_DEMO, POLITICA_SCORE_DEMO,
)
from contractors.management.commands.configurar_simulador_prestadores import CAMPOS
from contractors.models import ConfiguracionSimuladorPrestador, ConfiguracionScorePrestador
from contractors.services.capacidad_contractual import obtener_configuracion_simulador_prestador


class ConfigurarSimuladorTest(TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.archivo = Path(temporal.name) / 'simulador.json'
        self.datos = {campo: CONFIGURACION_FINANCIERA_DEMO[campo] for campo in CAMPOS if campo != 'version'}
        self.datos['version'] = 'prueba-simulador-v1'

    def ejecutar(self, aplicar=False):
        self.archivo.write_text(json.dumps(self.datos, default=str), encoding='utf-8')
        call_command('configurar_simulador_prestadores', archivo=str(self.archivo),
                     aplicar=aplicar, stdout=StringIO())

    def test_validacion_sin_persistencia(self):
        self.ejecutar()
        self.assertFalse(ConfiguracionSimuladorPrestador.objects.exists())
        self.assertFalse(ConfiguracionScorePrestador.objects.exists())

    def test_activacion_idempotente_sin_score(self):
        self.ejecutar(True)
        self.ejecutar(True)
        self.assertEqual(ConfiguracionSimuladorPrestador.objects.count(), 1)
        self.assertEqual(obtener_configuracion_simulador_prestador().version, self.datos['version'])
        self.assertFalse(ConfiguracionScorePrestador.objects.exists())

    def test_no_sobrescribe_version_ni_otra_activa(self):
        self.ejecutar(True)
        self.datos['tasa_mensual'] = '3.1'
        with self.assertRaises(CommandError):
            self.ejecutar(True)
        self.datos['version'] = 'otra'
        with self.assertRaises(CommandError):
            self.ejecutar(True)
        self.assertEqual(ConfiguracionSimuladorPrestador.objects.count(), 1)

    def test_rechaza_parametros_incompletos_y_negativos(self):
        self.datos['monto_minimo'] = '-1'
        with self.assertRaises(CommandError):
            self.ejecutar(True)
        self.datos.pop('tasa_mensual')
        with self.assertRaises(CommandError):
            self.ejecutar(True)
        self.assertFalse(ConfiguracionSimuladorPrestador.objects.exists())

    def test_politica_vigente_incompatible_revierte_sin_modificar_score(self):
        otra = ConfiguracionSimuladorPrestador.objects.create(version='otra', activo=False)
        politica = ConfiguracionScorePrestador.objects.create(
            version='score-test', activa=True, fecha_vigencia_desde=timezone.localdate(),
            configuracion_financiera=otra, **POLITICA_SCORE_DEMO)
        with self.assertRaises(CommandError):
            self.ejecutar(True)
        self.assertEqual(ConfiguracionSimuladorPrestador.objects.count(), 1)
        politica.refresh_from_db()
        self.assertTrue(politica.activa)
        self.assertEqual(politica.configuracion_financiera_id, otra.pk)

    def test_reactiva_mismos_valores_sin_reescribir_version(self):
        self.ejecutar(True)
        configuracion = ConfiguracionSimuladorPrestador.objects.get()
        configuracion.activo = False
        configuracion.save(update_fields=['activo'])
        self.ejecutar(True)
        self.assertEqual(obtener_configuracion_simulador_prestador().pk, configuracion.pk)

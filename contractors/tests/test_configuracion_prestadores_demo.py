from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from contractors.management.commands.configurar_politica_prestadores_demo import (
    TASA_MENSUAL_DEMO,
    VERSION_FINANCIERA_DEMO,
    VERSION_SCORE_DEMO,
)
from contractors.models import (
    BandaScorePrestador,
    ConfiguracionScorePrestador,
    ConfiguracionSimuladorPrestador,
)
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.capacidad_contractual import (
    obtener_configuracion_publica_simulador_prestador,
    obtener_configuracion_simulador_prestador,
)


class ConfiguracionPrestadoresDemoTest(TestCase):
    def test_command_crea_configuraciones_y_bandas_alineadas(self):
        salida = StringIO()

        call_command('configurar_politica_prestadores_demo', stdout=salida)

        financiera = ConfiguracionSimuladorPrestador.objects.get(
            version=VERSION_FINANCIERA_DEMO,
        )
        politica = ConfiguracionScorePrestador.objects.get(version=VERSION_SCORE_DEMO)
        self.assertTrue(financiera.activo)
        self.assertTrue(politica.activa)
        self.assertEqual(financiera.plazo_maximo_meses, 8)
        self.assertEqual(politica.plazo_maximo_politica, 8)
        self.assertEqual(financiera.tasa_mensual, TASA_MENSUAL_DEMO)
        self.assertEqual(politica.tasa_mensual_referencia, TASA_MENSUAL_DEMO)
        self.assertEqual(politica.configuracion_financiera, financiera)
        self.assertEqual(politica.bandas.count(), 5)
        self.assertEqual(
            sum((
                politica.peso_datacredito,
                politica.peso_capacidad,
                politica.peso_comportamiento,
                politica.peso_riesgo,
                politica.peso_referencias,
            )),
            1,
        )
        self.assertIn('Financiera=creada', salida.getvalue())

    def test_command_es_idempotente_y_conserva_historicos_inactivos(self):
        historica = ConfiguracionSimuladorPrestador.objects.create(
            nombre='Historica',
            version='historica-v1',
            activo=False,
        )
        call_command('configurar_politica_prestadores_demo', stdout=StringIO())
        segunda_salida = StringIO()

        call_command('configurar_politica_prestadores_demo', stdout=segunda_salida)

        self.assertEqual(
            ConfiguracionSimuladorPrestador.objects.filter(
                version=VERSION_FINANCIERA_DEMO,
            ).count(),
            1,
        )
        self.assertEqual(
            ConfiguracionScorePrestador.objects.filter(version=VERSION_SCORE_DEMO).count(),
            1,
        )
        self.assertEqual(BandaScorePrestador.objects.count(), 5)
        self.assertTrue(ConfiguracionSimuladorPrestador.objects.filter(pk=historica.pk).exists())
        self.assertIn('Financiera=reutilizada', segunda_salida.getvalue())
        self.assertIn('bandas_creadas=0', segunda_salida.getvalue())

    def test_simulador_y_politica_usan_parametrizacion_activa_de_base_de_datos(self):
        self.assertIsNone(obtener_configuracion_simulador_prestador())
        self.assertEqual(
            obtener_configuracion_publica_simulador_prestador(),
            {'disponible': False},
        )

        call_command('configurar_politica_prestadores_demo', stdout=StringIO())

        financiera = obtener_configuracion_simulador_prestador()
        politica = obtener_politica_score_activa()
        publica = obtener_configuracion_publica_simulador_prestador(financiera)
        self.assertEqual(publica['plazo_maximo_meses'], 8)
        self.assertEqual(politica.plazo_maximo_politica, 8)
        self.assertEqual(politica.configuracion_financiera, financiera)

    def test_politica_y_bandas_no_pueden_superar_configuracion_financiera(self):
        call_command('configurar_politica_prestadores_demo', stdout=StringIO())
        politica = ConfiguracionScorePrestador.objects.get(version=VERSION_SCORE_DEMO)

        politica.plazo_maximo_politica = 9
        with self.assertRaises(ValidationError):
            politica.full_clean()
        politica.refresh_from_db()

        banda = politica.bandas.get(nombre=BandaScorePrestador.Nombre.ENTRADA)
        banda.plazo_maximo = 9
        with self.assertRaises(ValidationError):
            banda.full_clean()

    def test_default_modelo_es_ocho_pero_no_reemplaza_configuracion_activa(self):
        instancia = ConfiguracionSimuladorPrestador(activo=False)

        self.assertEqual(instancia.plazo_maximo_meses, 8)
        self.assertIsNone(obtener_configuracion_simulador_prestador())

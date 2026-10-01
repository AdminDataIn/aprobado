import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase

from contractors.models import ConfiguracionSimuladorPrestador
from contractors.management.commands.configurar_politica_prestadores_demo import CONFIGURACION_FINANCIERA_DEMO
from contractors.services.capacidad_contractual import (
    ConfiguracionSimuladorNoDisponible,
    evaluar_capacidad_contractual_preliminar,
    simular_credito_prestador_informativo,
)
from gestion_creditos.services.condiciones_financieras import calcular_componentes_financieros


PLANTILLA = Path(settings.BASE_DIR) / 'docs/parametros_prestadores_prod_pendientes.json'


class SemanticaFinancieraSimuladorTest(SimpleTestCase):
    def setUp(self):
        datos = json.loads(PLANTILLA.read_text(encoding='utf-8'))
        # Seguro y plazo son exclusivamente el ejemplo de aceptacion, no politica PROD.
        datos.update(version='ejemplo-test', plazo_minimo_meses=3, plazo_maximo_meses=3,
                     porcentaje_seguro_vida_primera_cuota='0.3711')
        self.config = ConfiguracionSimuladorPrestador(**datos)
        for campo in self.config._meta.fields:
            if campo.get_internal_type() == 'DecimalField':
                setattr(self.config, campo.name, Decimal(getattr(self.config, campo.name)))

    def simular(self, monto='1000000'):
        return simular_credito_prestador_informativo(
            monto=monto, plazo_meses=3, configuracion=self.config,
        )

    def test_ejemplo_decimal_cargos_financiados_y_desembolso_integro(self):
        resultado = self.simular()
        for campo, esperado in {
            'monto_solicitado': '1000000.00', 'desembolso_neto': '1000000.00',
            'costo_originacion': '100000.00', 'iva_costo_originacion': '19000.00',
            'fondo_garantia': '20000.00', 'seguro_vida': '3711.00',
            'capital_total_financiado': '1142711.00', 'tasa_mensual': '0.01',
        }.items():
            with self.subTest(campo=campo):
                self.assertEqual(getattr(resultado, campo), Decimal(esperado))
        i = Decimal('0.01')
        cuota = (resultado.capital_total_financiado * i * (1+i)**3 / ((1+i)**3-1)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        self.assertEqual(resultado.cuota_mensual, cuota)
        self.assertEqual(cuota, Decimal('388547.01'))
        self.assertEqual(resultado.total_a_pagar, cuota * 3)
        self.assertEqual(resultado.intereses_estimados, cuota * 3 - resultado.capital_total_financiado)
        self.assertEqual(resultado.como_dict()['desembolso_neto'], resultado.monto_solicitado)

    def test_capacidad_y_simulador_delegan_al_mismo_calculo_sin_score_o_centrales(self):
        solicitud = SimpleNamespace(id=1, valor_total_contrato=Decimal('12000000'),
            valor_pendiente_cobrar=Decimal('10000000'), monto_solicitado=Decimal('1000000'),
            plazo_meses=3, fecha_fin_contrato=None)
        with patch('contractors.services.capacidad_contractual.calcular_componentes_financieros',
                   wraps=calcular_componentes_financieros) as calcular:
            simulacion = self.simular()
            capacidad = evaluar_capacidad_contractual_preliminar(solicitud, True, self.config)
        self.assertTrue(capacidad.calculable)
        self.assertEqual(capacidad.cuota_estimada_preliminar, simulacion.cuota_mensual)
        self.assertEqual(calcular.call_count, 2)
        self.assertEqual(calcular.call_args_list[0], calcular.call_args_list[1])

    def test_limites_aprobados(self):
        for monto in ('499999.99', '3000000.01'):
            with self.subTest(monto=monto), self.assertRaises(ValueError):
                self.simular(monto)
        for monto in ('500000', '3000000'):
            self.assertEqual(self.simular(monto).desembolso_neto, Decimal(monto))

    def test_tasa_cero_tambien_financia_cargos(self):
        self.config.tasa_mensual = Decimal('0')
        resultado = self.simular()
        self.assertEqual(resultado.cuota_mensual, (resultado.capital_total_financiado / 3).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP))

    def test_parametros_pendientes_no_usan_defaults(self):
        for campo in ('porcentaje_seguro_vida_primera_cuota', 'plazo_minimo_meses', 'plazo_maximo_meses'):
            anterior = getattr(self.config, campo)
            setattr(self.config, campo, None)
            with self.subTest(campo=campo), self.assertRaises(ConfiguracionSimuladorNoDisponible):
                self.simular()
            setattr(self.config, campo, anterior)


class PlantillaProductivaTest(TestCase):
    def test_plantilla_incompleta_no_activa_ni_reutiliza_demo(self):
        demo = ConfiguracionSimuladorPrestador.objects.create(
            version='prestadores-demo-v1', **CONFIGURACION_FINANCIERA_DEMO,
        )
        with self.assertRaisesMessage(CommandError, 'Configuracion incompleta'):
            call_command('configurar_simulador_prestadores', archivo=str(PLANTILLA))
        self.assertEqual(ConfiguracionSimuladorPrestador.objects.count(), 1)
        demo.refresh_from_db()
        self.assertEqual(demo.version, 'prestadores-demo-v1')
        self.assertEqual(demo.tasa_mensual, Decimal('2.2000'))

    def test_cada_null_requerido_es_rechazado_antes_de_persistir(self):
        datos = json.loads(PLANTILLA.read_text(encoding='utf-8'))
        datos.update(version='solo-test', plazo_minimo_meses=3, plazo_maximo_meses=3,
                     porcentaje_seguro_vida_primera_cuota='0.3711')
        for campo in datos:
            with self.subTest(campo=campo), patch(
                'contractors.management.commands.configurar_simulador_prestadores.json.loads',
                return_value={**datos, campo: None},
            ), self.assertNumQueries(0), self.assertRaisesMessage(CommandError, 'Configuracion incompleta'):
                call_command('configurar_simulador_prestadores', archivo=str(PLANTILLA))

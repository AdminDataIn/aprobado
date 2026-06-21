from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from contractors.models import ConfiguracionPortalContratistas
from contractors.score.configuracion import (
    CONFIGURACION_SCORE_PRESTADORES_V1,
    CONFIGURACION_SCORE_PRESTADORES_V2,
    ErrorConfiguracionScorePrestadores,
    obtener_configuracion_score_prestadores,
)
from contractors.score.dto import EntradaScoreInternoPrestador
from contractors.score.motor import evaluar_score_interno_prestador
from contractors.score.policies import (
    calcular_puntaje_capacidad_contractual,
    decimal_configuracion,
    obtener_bandas,
    validar_configuracion_score,
)


class ConfiguracionScoreInternoPrestadoresTests(SimpleTestCase):
    def test_v1_sigue_disponible_y_valida(self):
        self.assertTrue(validar_configuracion_score(CONFIGURACION_SCORE_PRESTADORES_V1))

    def test_v2_es_configuracion_activa_y_valida(self):
        configuracion = obtener_configuracion_score_prestadores()

        self.assertEqual(configuracion['version'], 'prestadores_score_v2_2026_06')
        self.assertTrue(validar_configuracion_score(configuracion))

    def test_selector_explicito_v2(self):
        configuracion = obtener_configuracion_score_prestadores('prestadores_score_v2_2026_06')

        self.assertIs(configuracion, CONFIGURACION_SCORE_PRESTADORES_V2)

    def test_selector_rechaza_version_invalida_sin_fallback_silencioso(self):
        with self.assertRaises(ErrorConfiguracionScorePrestadores):
            obtener_configuracion_score_prestadores('version_inexistente')

    def test_bandas_v2_cubren_rango_completo_sin_solaparse(self):
        bandas = sorted(obtener_bandas(CONFIGURACION_SCORE_PRESTADORES_V2), key=lambda banda: banda.minimo)

        self.assertEqual(bandas[0].minimo, Decimal('0'))
        self.assertEqual(bandas[-1].maximo, Decimal('1000'))
        for indice, banda in enumerate(bandas[:-1]):
            self.assertEqual(banda.maximo + Decimal('1'), bandas[indice + 1].minimo)

    def test_montos_y_plazos_v2_salen_de_configuracion(self):
        banda_premium = next(
            banda for banda in obtener_bandas(CONFIGURACION_SCORE_PRESTADORES_V2) if banda.nombre == 'PREMIUM'
        )
        banda_media = next(
            banda for banda in obtener_bandas(CONFIGURACION_SCORE_PRESTADORES_V2) if banda.nombre == 'MEDIA'
        )

        self.assertEqual(banda_premium.monto_maximo, Decimal('10000000.00'))
        self.assertEqual(banda_premium.plazo_maximo_meses, 8)
        self.assertEqual(banda_media.monto_maximo, Decimal('5000000.00'))
        self.assertEqual(banda_media.plazo_maximo_meses, 8)


class MotorScoreInternoPrestadoresTests(SimpleTestCase):
    def _entrada(self, **componentes):
        return EntradaScoreInternoPrestador(
            solicitud_id=1,
            componentes=componentes,
            datacredito_status='PENDIENTE',
        )

    def test_caso_referencia_v2_score_media_y_capacidad_financiera(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=720,
                capacidad=680,
                comportamiento_digital=760,
                riesgo_fraude=820,
                referencias=900,
                geolocalizacion=850,
                ingreso_neto=Decimal('2400000.00'),
                obligaciones_mensuales=Decimal('650000.00'),
                monto_solicitado=Decimal('10000000.00'),
                plazo_solicitado=6,
            ),
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertEqual(resultado.score_final, Decimal('732.20'))
        self.assertEqual(resultado.banda.nombre, 'MEDIA')
        self.assertEqual(resultado.monto_maximo_sugerido, Decimal('2921005.02'))
        self.assertEqual(resultado.capacidad_financiera['cuota_maxima'], '525000.00')
        self.assertEqual(resultado.capacidad_financiera['relacion_cuota_ingreso'], '0.3000')
        self.assertTrue(resultado.capacidad_financiera['viable'])

    def test_thresholds_v2_resuelven_bandas(self):
        casos = [
            (850, 'PREMIUM', Decimal('10000000.00'), 8),
            (750, 'ALTA', Decimal('8000000.00'), 8),
            (680, 'MEDIA', Decimal('5000000.00'), 8),
            (600, 'ENTRADA', Decimal('3000000.00'), 6),
            (599, 'REVISION', Decimal('0.00'), 0),
        ]

        for puntaje, banda, monto, plazo in casos:
            with self.subTest(puntaje=puntaje):
                resultado = evaluar_score_interno_prestador(
                    self._entrada(
                        datacredito=puntaje,
                        capacidad=puntaje,
                        comportamiento_digital=puntaje,
                        riesgo_fraude=puntaje,
                        referencias=puntaje,
                    ),
                    CONFIGURACION_SCORE_PRESTADORES_V2,
                )
                self.assertEqual(resultado.banda.nombre, banda)
                self.assertEqual(resultado.monto_maximo_sugerido, monto)
                self.assertEqual(resultado.plazo_maximo_sugerido, plazo)

    def test_geolocalizacion_v2_no_aporta_score_y_penaliza_bajo_600(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=750,
                capacidad=750,
                comportamiento_digital=750,
                riesgo_fraude=750,
                referencias=750,
                geolocalizacion=500,
            ),
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertEqual(resultado.score_final, Decimal('670.00'))
        self.assertEqual(resultado.banda.nombre, 'ENTRADA')
        self.assertEqual(resultado.penalizaciones[0].penalizacion, Decimal('-80'))

    def test_capacidad_financiera_v2_recorta_monto_por_regla_30_por_ciento(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=950,
                capacidad=950,
                ingreso_neto=Decimal('2400000.00'),
                obligaciones_mensuales=Decimal('650000.00'),
                monto_solicitado=Decimal('10000000.00'),
                plazo_solicitado=6,
            ),
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertLess(resultado.monto_maximo_sugerido, Decimal('3000000.00'))
        self.assertEqual(resultado.capacidad_financiera['cuota_maxima'], '525000.00')

    def test_v2_respeta_meses_restantes_de_contrato(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=900,
                capacidad=900,
                meses_restantes_contrato=4,
                plazo_solicitado=8,
            ),
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertEqual(resultado.plazo_maximo_sugerido, 4)

    def test_v2_respeta_valor_pendiente_por_cobrar(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=900,
                capacidad=900,
                valor_pendiente_cobrar=Decimal('2000000.00'),
                monto_solicitado=Decimal('9000000.00'),
            ),
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertEqual(resultado.monto_maximo_sugerido, Decimal('2000000.00'))

    def test_datacredito_pendiente_queda_marcado_y_score_es_parcial(self):
        resultado = evaluar_score_interno_prestador(self._entrada(capacidad=900), CONFIGURACION_SCORE_PRESTADORES_V2)

        self.assertEqual(resultado.datacredito_status, 'PENDIENTE')
        self.assertIn('datacredito', resultado.componentes_pendientes)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIn('capacidad_financiera_sin_ingreso_u_obligaciones_formales', resultado.advertencias_tecnicas)

    def test_capacidad_contractual_se_convierte_a_puntaje_desde_configuracion(self):
        puntaje = calcular_puntaje_capacidad_contractual(
            {
                'monto_solicitado': Decimal('1000000.00'),
                'capacidad_maxima_estimada': Decimal('8000000.00'),
            },
            CONFIGURACION_SCORE_PRESTADORES_V2,
        )

        self.assertEqual(puntaje, Decimal('900.00'))

    def test_decimal_configuracion_no_usa_float(self):
        self.assertEqual(decimal_configuracion('0.25'), Decimal('0.25'))


class ConfigurarPoliticaPrestadoresV2CommandTests(TestCase):
    def setUp(self):
        self.configuracion = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Contratistas',
            host='contratistas.localhost',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )

    def test_comando_sin_confirmar_solo_muestra_diferencias(self):
        salida = StringIO()

        call_command('configurar_politica_prestadores_v2', '--host', 'contratistas.localhost:8000', stdout=salida)

        self.configuracion.refresh_from_db()
        self.assertEqual(self.configuracion.plazo_maximo_meses, 24)
        self.assertIn('No se aplicaron cambios', salida.getvalue())

    def test_comando_con_confirmar_actualiza_solo_host_indicado(self):
        salida = StringIO()

        call_command(
            'configurar_politica_prestadores_v2',
            '--host',
            'contratistas.localhost',
            '--confirmar',
            stdout=salida,
        )

        self.configuracion.refresh_from_db()
        self.assertEqual(self.configuracion.monto_maximo, Decimal('10000000.00'))
        self.assertEqual(self.configuracion.plazo_maximo_meses, 8)
        self.assertEqual(self.configuracion.tasa_mensual, Decimal('2.2000'))
        self.assertEqual(self.configuracion.tasa_comision, Decimal('10.0000'))
        self.assertEqual(self.configuracion.comision_fija, Decimal('0.00'))
        self.assertEqual(self.configuracion.tasa_fondo_garantia, Decimal('2.0000'))
        self.assertEqual(self.configuracion.iva_fondo_garantia, Decimal('19.0000'))
        self.assertTrue(self.configuracion.fondo_garantia_incluye_iva)
        self.assertEqual(self.configuracion.factor_seguro_vida, Decimal('0.003711'))
        self.assertTrue(self.configuracion.seguro_vida_financiado)
        self.assertIn('Politica V2 aplicada', salida.getvalue())

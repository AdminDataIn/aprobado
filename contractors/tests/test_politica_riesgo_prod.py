from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import patch
from dateutil.relativedelta import relativedelta

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from contractors.models import ConfiguracionSimuladorPrestador, ConfiguracionScorePrestador
from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador
from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from contractors.score.motor import evaluar_score_prestador
from contractors.services.centrales_riesgo import obtener_evaluacion_centrales_prestador
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente, registrar_ingreso_neto
from contractors.services.politica_financiera_prestador import (
    VERSION, PARAMETROS, preparar_oferta, oferta_con_ingreso_vigente,
)
from contractors.services.preparacion_score_prod import preparar_politica_score_prod, definicion_score_prod
from contractors.tests import test_centrales_duales_prestador as duales
from contractors.tests import test_politica_prod_horizonte as horizonte_fixture
from contractors.tests.fixtures_ingreso_neto import preparar_evidencia_neta
from integrations.datacredito.normalizadores import normalizar_midecisor_pn


class PoliticaRiesgoProdTest(TestCase):
    def setUp(self):
        self.solicitud_neta, self.actor, self.documento = preparar_evidencia_neta(self)
        self.corte = timezone.localdate()
        self.datos = duales.CentralesDualesPrestadorTest(methodName='runTest')
        self.addCleanup(self.datos.doCleanups)
        self.datos.setUp()
        ConfiguracionSimuladorPrestador.objects.filter(activo=True).update(activo=False)
        self.financiera = ConfiguracionSimuladorPrestador.objects.create(version=VERSION, **PARAMETROS)
        self.politica = preparar_politica_score_prod(parametros={'fecha_vigencia_desde': self.corte},
            persistir=True, actor=self.actor, motivo='Politica ratificada solo en DB test')
        self.solicitud = self.datos.solicitud
        self.solicitud.version_configuracion_financiera_simulacion = VERSION
        self.solicitud.version_politica_simulacion = self.politica.version_politica
        self.solicitud.tasa_mensual_simulacion = self.financiera.tasa_mensual
        self.solicitud.monto_maximo_configuracion_simulacion = self.financiera.monto_maximo
        self.solicitud.plazo_maximo_configuracion_simulacion = self.financiera.plazo_maximo_meses

    def score(self, centrales=None):
        with self.datos._parches_autorizacion():
            return evaluar_score_prestador(self.solicitud, self.politica, centrales or self.datos._centrales())

    def test_pesos_flags_ttl_y_redistribucion_ratificados(self):
        valores = {'midecisor': '.45', 'capacidad': '.30', 'comportamiento': '.08', 'riesgo': '.12',
                   'referencias': '.05', 'hdcplus': '0', 'datacredito': '0'}
        self.assertEqual(sum(getattr(self.politica, 'peso_' + k) for k in valores), 1)
        for nombre, valor in valores.items():
            self.assertEqual(getattr(self.politica, 'peso_' + nombre), Decimal(valor))
        self.assertFalse(self.politica.activa)
        self.assertTrue(self.politica.requiere_hdcplus)
        self.assertTrue(self.politica.requiere_midecisor)
        self.assertFalse(self.politica.permite_evaluar_sin_hdc)
        self.assertFalse(self.politica.permite_evaluar_sin_midecisor)
        self.assertEqual((self.politica.vigencia_hdcplus_dias, self.politica.vigencia_midecisor_dias), (30, 30))
        score = self.score()
        self.assertTrue(score.variables_calculadas['redistribucion_aplicada'])
        self.assertEqual(score.variables_calculadas['motivo_redistribucion'], 'referencias_no_verificadas')
        self.assertEqual(next(c for c in score.componentes if c.nombre == 'hdcplus').peso_aplicado, 0)

    def test_hdc_y_midecisor_requeridos_no_redistribuye_ausencia(self):
        for servicio, campo in (('historial', 'historial'), ('decisor', 'decisor')):
            with self.subTest(servicio=servicio):
                centrales = replace(self.datos._centrales(), **{campo: ResultadoConsultaDatacreditoPrestador(
                    estado='ERROR_TRANSITORIO', servicio=servicio)})
                score = self.score(centrales)
                self.assertIsNone(score.score_final)
                self.assertFalse(score.variables_calculadas['redistribucion_aplicada'])

    def test_sin_info_transitorio_permanente_respetan_accion_aprobada(self):
        casos = [('SIN_INFORMACION', 'REQUIERE_REVISION_MANUAL'),
                 ('ERROR_TRANSITORIO', 'REQUIERE_REVISION_MANUAL'), ('ERROR_PERMANENTE', 'NO_EVALUABLE')]
        for estado, esperado in casos:
            with self.subTest(estado=estado), patch(
                'contractors.services.centrales_riesgo.obtener_evaluacion_datacredito_prestador',
                side_effect=[ResultadoConsultaDatacreditoPrestador(estado=estado, servicio='decisor'), self.datos._hdc()],
            ):
                resultado = obtener_evaluacion_centrales_prestador(self.solicitud, politica=self.politica)
                self.assertEqual(resultado.estado_global, esperado)

    def test_mora_consultas_y_geografia_no_penalizan_automaticamente(self):
        base = self.score()
        informativo = self.score(self.datos._centrales(historial=self.datos._hdc(mora_severa=True, consultas=99)))
        self.assertEqual(informativo.score_final, base.score_final)
        self.assertFalse(informativo.bloqueos)
        self.assertFalse(informativo.penalizaciones)
        self.assertFalse(informativo.variables_calculadas['geolocalizacion_disponible'])

    def test_estimado_midecisor_conservado_interno_no_entra_en_capacidad(self):
        normalizado = normalizar_midecisor_pn({})
        normalizado = replace(normalizado, ingreso_estimado=Decimal('9999999'))
        interno = _proyectar_resultado_allowlist(normalizado, servicio='decisor')
        self.assertEqual(interno.ingreso_estimado_midecisor, '9999999')
        self.assertIsNone(_proyectar_resultado_allowlist(normalizado, servicio='historial').ingreso_estimado_midecisor)
        centrales = self.datos._centrales()
        score = self.score(centrales)
        modificado = replace(centrales, decisor=replace(centrales.decisor,
            resultado_normalizado=replace(centrales.decisor.resultado_normalizado,
                                          ingreso_estimado_midecisor='9999999')))
        self.assertEqual(self.score(modificado).como_dict(), score.como_dict())
        self.assertNotIn('ingreso_estimado_midecisor', str(modificado.como_dict_seguro()))
        self.assertEqual(self.oferta(None).estado, 'NO_EVALUABLE')

    def registrar(self, monto='100000000'):
        return registrar_ingreso_neto(solicitud=self.solicitud_neta, actor=self.actor, monto=monto,
            fecha_corte=self.corte, vigente_hasta=self.corte + timedelta(days=30),
            documentos=[self.documento], observacion='Soporte sintetico revisado')

    def oferta(self, ingreso, **kwargs):
        score = self.score()
        datos = dict(score=score, politica=self.politica, ingreso_neto=ingreso,
            obligaciones_mensuales=Decimal('100000'), monto_solicitado=Decimal('1000000'), plazo_solicitado=4,
            horizonte=horizonte_fixture.horizonte(18, inicio=self.corte.replace(day=1),
                fin=self.corte.replace(day=1) + relativedelta(months=18)-timedelta(days=1),
                corte=self.corte), configuracion=self.financiera, corte=self.corte, solicitud_id=self.solicitud_neta.pk)
        datos.update(kwargs)
        with patch('contractors.score.motor.evaluar_score_prestador', side_effect=AssertionError('Oferta no recalcula score')):
            return preparar_oferta(**datos)

    def test_ingreso_manual_habilita_oferta_versionada_y_cambio_la_invalida(self):
        registro = self.registrar()
        ingreso = obtener_ingreso_neto_vigente(self.solicitud_neta)
        oferta = self.oferta(ingreso)
        self.assertEqual(oferta.estado, 'OFERTA_CALCULADA')
        self.assertEqual(oferta.ingreso_neto_registro_id, registro.pk)
        self.assertEqual(oferta.ingreso_neto_version, 1)
        self.assertEqual(oferta.ingreso_neto_fuente, 'MANUAL_VERIFICADA')
        self.assertEqual(oferta.cuota_maxima, (Decimal('100000000') - Decimal('100000')) * Decimal('.30'))
        snapshot = oferta.como_dict()
        self.assertTrue(oferta_con_ingreso_vigente(oferta, self.solicitud_neta))
        self.registrar('90000000')
        self.assertFalse(oferta_con_ingreso_vigente(oferta, self.solicitud_neta))
        self.assertEqual(self.oferta(ingreso).estado, 'NO_EVALUABLE')
        self.assertEqual(oferta.como_dict(), snapshot)

    def test_ausente_vencido_o_estimado_no_equivale_a_neto(self):
        self.assertEqual(self.oferta(None).estado, 'NO_EVALUABLE')
        self.registrar()
        ingreso = obtener_ingreso_neto_vigente(self.solicitud_neta)
        futuro = self.corte + timedelta(days=31)
        self.assertIsNone(obtener_ingreso_neto_vigente(self.solicitud_neta, corte=futuro))
        self.assertEqual(self.oferta(ingreso, corte=futuro).estado, 'NO_EVALUABLE')
        with self.assertRaises(ValidationError):
            self.oferta(replace(ingreso, fuente='ingreso_estimado_midecisor'))

    def test_dry_run_completo_demuestra_vinculo_y_no_persiste(self):
        cantidad = ConfiguracionScorePrestador.objects.count()
        salida = StringIO()
        call_command('preparar_politica_score_prestadores_prod', fecha_vigencia=self.corte.isoformat(), stdout=salida)
        datos = json.loads(salida.getvalue())
        self.assertEqual(datos['configuracion_financiera_id'], self.financiera.pk)
        self.assertEqual(Decimal(datos['peso_total']), 1)
        self.assertEqual(datos['parametros'], {**definicion_score_prod()['parametros_aprobados'],
                                             'fecha_vigencia_desde': self.corte.isoformat()})
        self.assertFalse(datos['activa'])
        self.assertFalse(datos['persistida'])
        self.assertEqual(ConfiguracionScorePrestador.objects.count(), cantidad)
        self.politica.refresh_from_db()
        self.assertFalse(self.politica.activa)

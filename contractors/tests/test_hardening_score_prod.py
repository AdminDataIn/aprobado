import json
from datetime import date
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from contractors.models import ConfiguracionSimuladorPrestador, ConfiguracionScorePrestador, CambioPoliticaScorePrestadorAudit
from contractors.score.dto import ComponenteScorePrestador
from contractors.score.motor import evaluar_score_prestador
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.politica_financiera_prestador import VERSION, PARAMETROS, preparar_oferta, IngresoNetoValido
from contractors.services.preparacion_score_prod import preparar_politica_score_prod, definicion_score_prod
from contractors.tests import test_politica_prod_horizonte as fixtures
from gestion_creditos.models import Credito, CreditoLibranza


class PreparacionScoreProdTest(TestCase):
    def setUp(self):
        self.financiera = ConfiguracionSimuladorPrestador.objects.create(version=VERSION, **PARAMETROS)
        self.actor = get_user_model().objects.create_superuser('preparador', 'preparador@example.com', 'test')
        # These values exist exclusively as synthetic test inputs, NOT an approved PROD policy.
        self.parametros = dict(
            version_score='motor-sintetico-v1', version_politica='prestadores-score-prod-v1',
            peso_datacredito=Decimal('0'), peso_midecisor=Decimal('.45'), peso_hdcplus=Decimal('0'),
            peso_capacidad=Decimal('.30'), peso_comportamiento=Decimal('.08'), peso_riesgo=Decimal('.12'),
            peso_referencias=Decimal('.05'), permite_redistribuir_pesos_faltantes=False,
            requiere_referencias=False, penalizacion_geolocalizacion=0, umbral_geolocalizacion=0,
            mora_bloqueo_dias=90, consultas_recientes_revision=6, requiere_midecisor=True,
            requiere_hdcplus=True, permite_evaluar_sin_midecisor=False, permite_evaluar_sin_hdc=False,
            vigencia_midecisor_dias=30, vigencia_hdcplus_dias=30,
            accion_sin_informacion_centrales='REVISION_MANUAL', accion_error_transitorio_centrales='REVISION_MANUAL',
            accion_error_permanente_centrales='NO_EVALUABLE', accion_exceso_capacidad='REVISION',
            cuota_ingreso_maxima=Decimal('.30'), tolerancia_ingreso_contractual=Decimal('.15'),
            fuente_ingreso_neto_valido_para_riesgo='fuente-sintetica-test', fecha_vigencia_desde=date(2026,8,1),
        )
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
        guard.start()
        self.addCleanup(guard.stop)

    def persistir(self):
        return preparar_politica_score_prod(parametros=self.parametros, persistir=True,
                                          actor=self.actor, motivo='Preparacion sintetica de test')

    def test_definicion_incompleta_dry_run_no_copia_demo(self):
        salida = StringIO()
        call_command('preparar_politica_score_prestadores_prod', stdout=salida)
        datos = json.loads(salida.getvalue())
        self.assertFalse(datos['activa'])
        self.assertFalse(datos['persistida'])
        self.assertIn('peso_midecisor', datos['pendientes'])
        self.assertTrue(all(v is None for v in definicion_score_prod()['parametros_pendientes_aprobacion'].values()))
        self.assertFalse(ConfiguracionScorePrestador.objects.exists())
        with self.assertRaises(ValidationError):
            preparar_politica_score_prod(persistir=True, actor=self.actor, motivo='Test')

    def test_bandas_persistidas_inactivas_idempotentes_y_auditadas(self):
        politica = self.persistir()
        repetida = self.persistir()
        self.assertEqual(politica.pk, repetida.pk)
        politica.refresh_from_db()
        self.assertFalse(politica.activa)
        self.assertIsNone(obtener_politica_score_activa())
        self.assertEqual(politica.configuracion_financiera_id, self.financiera.pk)
        self.assertEqual(list(politica.bandas.order_by('orden').values_list('nombre','score_min','monto_maximo','plazo_maximo')),
            [('PREMIUM',850,Decimal('10000000'),8), ('ALTA',750,Decimal('8000000'),8),
             ('MEDIA',680,Decimal('5000000'),8), ('ENTRADA',600,Decimal('3000000'),6), ('REVISION',0,Decimal('0'),0)])
        self.assertEqual(CambioPoliticaScorePrestadorAudit.objects.count(), 1)
        audit = CambioPoliticaScorePrestadorAudit.objects.get()
        self.assertTrue(audit.snapshot_nuevo['preparacion_inactiva'])
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

    def test_no_persiste_fuera_de_tests_ni_reinterpreta_version_existente(self):
        with override_settings(RUNNING_TESTS=False), self.assertRaises(PermissionDenied):
            self.persistir()
        politica = self.persistir()
        self.parametros['peso_midecisor'] = Decimal('.40')
        self.parametros['peso_capacidad'] = Decimal('.35')
        with self.assertRaises(ValidationError):
            self.persistir()
        politica.refresh_from_db()
        self.assertEqual(politica.peso_midecisor, Decimal('.45'))

    def test_score_existente_banda_persistida_y_oferta_serializable_sin_neto_no_evaluable(self):
        politica = self.persistir()
        solicitud = SimpleNamespace(monto_solicitado=Decimal('10000000'), plazo_meses=8,
            valor_pendiente_cobrar=Decimal('100000000'), monto_simulado=Decimal('10000000'), plazo_simulado_meses=8,
            version_configuracion_financiera_simulacion=self.financiera.version,
            version_politica_simulacion=politica.version_politica,
            tasa_mensual_simulacion=self.financiera.tasa_mensual,
            monto_maximo_configuracion_simulacion=self.financiera.monto_maximo,
            plazo_maximo_configuracion_simulacion=self.financiera.plazo_maximo_meses)
        componentes = tuple(ComponenteScorePrestador(nombre, True, Decimal('900'), peso)
            for nombre, peso in (('datacredito_score',politica.peso_midecisor), ('hdcplus',politica.peso_hdcplus),
                ('capacidad',politica.peso_capacidad), ('comportamiento',politica.peso_comportamiento),
                ('riesgo',politica.peso_riesgo), ('referencias',politica.peso_referencias)))
        with patch('contractors.score.motor.construir_componentes_score', return_value=(
            componentes, {'capacidad_disponible':Decimal('900000')}, (), {'disponible':False},
        )):
            score = evaluar_score_prestador(solicitud, politica, None)
        datos = dict(score=score, politica=politica, ingreso_neto=None, obligaciones_mensuales=Decimal('0'),
            monto_solicitado=solicitud.monto_solicitado, plazo_solicitado=8, horizonte=fixtures.horizonte(18),
            configuracion=self.financiera, corte=date(2026,8,1))
        oferta = preparar_oferta(**datos)
        serializado = json.loads(json.dumps(oferta.como_dict()))
        self.assertEqual(serializado['estado'], 'NO_EVALUABLE')
        self.assertIn('neto', serializado['motivo'])
        self.assertEqual(serializado['score'], '900.00')
        self.assertEqual(serializado['banda_id'], politica.bandas.get(nombre='PREMIUM').pk)
        self.assertEqual(serializado['monto_maximo_banda'], '10000000.00')
        self.assertEqual(serializado['plazo_maximo_banda'], 8)
        datos['ingreso_neto'] = IngresoNetoValido(Decimal('100000000'), 'fuente-sintetica-test', date(2026,8,1))
        datos['obligaciones_mensuales'] = None
        self.assertEqual(preparar_oferta(**datos).estado, 'NO_EVALUABLE')
        datos['obligaciones_mensuales'] = Decimal('0')
        with patch('contractors.score.motor.evaluar_score_prestador', side_effect=AssertionError('No recalcular score')):
            self.assertEqual(preparar_oferta(**datos).estado, 'OFERTA_CALCULADA')
        solicitud.version_politica_simulacion = ''
        with patch('contractors.score.motor.construir_componentes_score', return_value=(
            componentes, {}, (), {'disponible':False},
        )):
            legacy = evaluar_score_prestador(solicitud, politica, None)
        self.assertEqual(solicitud.version_politica_simulacion, '')
        self.assertTrue(legacy.requiere_revision_manual)
        datos.update(score=legacy)
        self.assertEqual(preparar_oferta(**datos).estado, 'SIN_OFERTA')

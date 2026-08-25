from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from contractors.datacredito.dto import (
    ResultadoConsultaDatacreditoPrestador,
    ResultadoNormalizadoDatacreditoPrestador,
)
from contractors.models import (
    BandaScorePrestador,
    ConfiguracionScorePrestador,
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
)
from contractors.score.componentes import componente_capacidad, componente_datacredito
from contractors.score.dto import ComponenteScorePrestador
from contractors.score.motor import detectar_incompatibilidades_simulador, evaluar_score_prestador
from contractors.score.politica import validar_politica_score_completa
from contractors.services.predecision import evaluar_predecision_formal_prestador
from gestion_creditos.models import Empresa


class ScorePrestadoresV2Test(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='score-prestador', password='test-password'
        )
        self.empresa = Empresa.objects.create(nombre='Empresa Score', convenio_activo=True)
        self.solicitud = self._crear_solicitud()
        self.configuracion_financiera = ConfiguracionSimuladorPrestador.objects.create(
            nombre='Simulador alineado',
            version='financiera-v1',
            activo=True,
            monto_minimo=Decimal('1000000'),
            monto_maximo=Decimal('10000000'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=8,
            tasa_mensual=Decimal('2.2000'),
        )
        self.politica = crear_politica_score(
            configuracion_financiera=self.configuracion_financiera
        )
        self._marcar_simulada(self.solicitud, self.configuracion_financiera)

    def test_pesos_deben_sumar_uno(self):
        self.politica.peso_referencias = Decimal('0.04')
        with self.assertRaisesMessage(ValidationError, 'sumar'):
            self.politica.full_clean()

    def test_bandas_no_pueden_solaparse(self):
        banda = self.politica.bandas.get(nombre=BandaScorePrestador.Nombre.ENTRADA)
        banda.score_max = 700
        with self.assertRaisesMessage(ValidationError, 'solapa'):
            banda.full_clean()

    def test_solo_una_politica_puede_estar_activa(self):
        segunda = crear_politica_score(
            version='v2', activa=False,
            configuracion_financiera=self.configuracion_financiera,
        )
        segunda.activa = True
        with self.assertRaises(ValidationError):
            segunda.full_clean()

    def test_datacredito_usa_score_real_y_no_inventa_default(self):
        disponible = componente_datacredito(
            ResultadoNormalizadoDatacreditoPrestador(score_externo=837),
            self.politica,
        )
        ausente = componente_datacredito(
            ResultadoNormalizadoDatacreditoPrestador(score_externo=None),
            self.politica,
        )
        self.assertEqual(disponible.score, Decimal('837'))
        self.assertTrue(disponible.disponible)
        self.assertIsNone(ausente.score)
        self.assertFalse(ausente.disponible)

    def test_capacidad_usa_obligaciones_y_relacion_cuota_ingreso(self):
        normalizado = ResultadoNormalizadoDatacreditoPrestador(
            score_externo=850,
            cuota_mensual_total='500000',
            mora_severa=False,
        )
        componente, variables = componente_capacidad(
            self.solicitud, normalizado, self.politica
        )
        self.assertTrue(componente.disponible)
        self.assertEqual(variables['obligaciones_mensuales'], Decimal('500000'))
        self.assertIsNotNone(variables['relacion_cuota_ingreso'])
        self.assertGreaterEqual(componente.score, 0)
        self.assertLessEqual(componente.score, 1000)

    def test_referencias_y_geolocalizacion_no_reciben_default(self):
        resultado = self._evaluar_score(score=850)
        referencias = next(
            item for item in resultado.componentes if item.nombre == 'referencias'
        )
        self.assertFalse(referencias.disponible)
        self.assertIsNone(referencias.score)
        self.assertFalse(resultado.variables_calculadas['geolocalizacion_disponible'])
        self.assertEqual(resultado.penalizaciones, ())

    def test_simulacion_y_politica_financiera_vinculadas_son_compatibles(self):
        self.assertEqual(
            detectar_incompatibilidades_simulador(self.solicitud, self.politica),
            (),
        )

    def test_edicion_manual_posterior_a_simulacion_requiere_revision(self):
        self.solicitud.monto_solicitado = Decimal('3500000')
        self.solicitud.save(update_fields=['monto_solicitado', 'updated_at'])
        incompatibilidades = detectar_incompatibilidades_simulador(
            self.solicitud, self.politica
        )
        self.assertIn('editado manualmente', ' '.join(incompatibilidades))

    def test_referencias_ausentes_no_bloquean_v1_si_politica_redistribuye(self):
        predecision = self._predecision(950)
        self.assertEqual(predecision.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertNotIn('referencias', ' '.join(predecision.bloqueos).lower())

    def test_calculo_ponderado_decimal_es_deterministico_y_clamp(self):
        componentes = tuple(
            ComponenteScorePrestador(
                nombre=nombre,
                disponible=True,
                score=score,
                peso_configurado=peso,
            )
            for nombre, score, peso in (
                ('datacredito', Decimal('1000'), Decimal('0.45')),
                ('capacidad', Decimal('800'), Decimal('0.30')),
                ('comportamiento', Decimal('700'), Decimal('0.08')),
                ('riesgo', Decimal('900'), Decimal('0.12')),
                ('referencias', Decimal('600'), Decimal('0.05')),
            )
        )
        with patch(
            'contractors.score.motor.construir_componentes_score',
            return_value=(componentes, {
                'capacidad_monto_teorica': Decimal('10000000'),
                'meses_restantes_contrato': 8,
                'relacion_cuota_ingreso': Decimal('0.10'),
            }, (), {'disponible': False, 'score': None}),
        ), patch(
            'contractors.services.predecision.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ):
            primero = evaluar_score_prestador(
                self.solicitud, self.politica, self._datacredito(900)
            )
            segundo = evaluar_score_prestador(
                self.solicitud, self.politica, self._datacredito(900)
            )
        self.assertEqual(primero.score_final, Decimal('884.00'))
        self.assertEqual(primero.score_final, segundo.score_final)
        self.assertEqual(primero.banda, BandaScorePrestador.Nombre.PREMIUM)

    def test_bandas_y_topes_se_resuelven_desde_configuracion(self):
        self.solicitud.monto_solicitado = Decimal('10000000')
        self.solicitud.plazo_meses = 8
        casos = (
            (875, 'PREMIUM', Decimal('10000000'), 8),
            (800, 'ALTA', Decimal('8000000'), 8),
            (700, 'MEDIA', Decimal('5000000'), 8),
            (625, 'ENTRADA', Decimal('3000000'), 6),
        )
        for score, banda_esperada, monto_esperado, plazo_esperado in casos:
            componentes = tuple(
                ComponenteScorePrestador(
                    nombre=nombre,
                    disponible=True,
                    score=Decimal(score),
                    peso_configurado=peso,
                )
                for nombre, peso in (
                    ('datacredito', Decimal('0.45')),
                    ('capacidad', Decimal('0.30')),
                    ('comportamiento', Decimal('0.08')),
                    ('riesgo', Decimal('0.12')),
                    ('referencias', Decimal('0.05')),
                )
            )
            with self.subTest(banda=banda_esperada), patch(
                'contractors.score.motor.construir_componentes_score',
                return_value=(componentes, {
                    'capacidad_monto_teorica': Decimal('10000000'),
                    'meses_restantes_contrato': 8,
                    'relacion_cuota_ingreso': Decimal('0.10'),
                }, (), {'disponible': False, 'score': None}),
            ):
                resultado = evaluar_score_prestador(
                    self.solicitud, self.politica, self._datacredito(score)
                )
                self.assertEqual(resultado.banda, banda_esperada)
                self.assertEqual(resultado.monto_maximo_sugerido, monto_esperado)
                self.assertEqual(resultado.plazo_maximo_sugerido, plazo_esperado)

    def test_score_revision_no_preaprueba(self):
        componentes = tuple(
            ComponenteScorePrestador(
                nombre=nombre,
                disponible=True,
                score=Decimal('550'),
                peso_configurado=peso,
            )
            for nombre, peso in (
                ('datacredito', Decimal('0.45')),
                ('capacidad', Decimal('0.30')),
                ('comportamiento', Decimal('0.08')),
                ('riesgo', Decimal('0.12')),
                ('referencias', Decimal('0.05')),
            )
        )
        with patch(
            'contractors.score.motor.construir_componentes_score',
            return_value=(componentes, {
                'capacidad_monto_teorica': Decimal('10000000'),
                'meses_restantes_contrato': 8,
                'relacion_cuota_ingreso': Decimal('0.10'),
            }, (), {'disponible': False, 'score': None}),
        ), patch(
            'contractors.services.predecision.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ):
            predecision = evaluar_predecision_formal_prestador(
                solicitud=self.solicitud,
                politica=self.politica,
                datacredito=self._datacredito(550),
            )
        self.assertEqual(predecision.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertFalse(predecision.eligible)

    def test_incompatibilidad_simulador_politica_requiere_revision(self):
        self.solicitud.version_configuracion_financiera_simulacion = 'financiera-anterior'
        self.solicitud.save(update_fields=[
            'version_configuracion_financiera_simulacion', 'updated_at'
        ])
        predecision = self._predecision(900)
        self.assertEqual(predecision.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertIn('cambio', ' '.join(predecision.alertas).lower())

    def test_cambio_politica_posterior_a_simulacion_requiere_revision(self):
        self.solicitud.version_politica_simulacion = 'politica-anterior'
        self.solicitud.save(update_fields=['version_politica_simulacion', 'updated_at'])
        predecision = self._predecision(900)
        self.assertEqual(predecision.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertIn('score cambio', ' '.join(predecision.alertas).lower())

    def test_documento_inconsistente_bloquea(self):
        self.solicitud.metadata_analisis_contractual['identidad'] = {
            'documento_coincide': False,
        }
        self.solicitud.estado_analisis_contractual = (
            ContractorApplication.EstadoAnalisisContractual.BLOQUEADO
        )
        self.solicitud.save(update_fields=[
            'metadata_analisis_contractual', 'estado_analisis_contractual', 'updated_at'
        ])
        predecision = self._predecision(900)
        self.assertEqual(predecision.resultado, 'BLOQUEADO_READ_ONLY')

    def _evaluar_score(self, score):
        with patch(
            'contractors.score.componentes.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ):
            return evaluar_score_prestador(
                self.solicitud, self.politica, self._datacredito(score)
            )

    def _datacredito(self, score):
        return ResultadoConsultaDatacreditoPrestador(
            estado='EXITOSO',
            snapshot_id='00000000-0000-0000-0000-000000000002',
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                score_externo=score,
                cuota_mensual_total='0',
                mora_actual=False,
                mora_severa=False,
                servicio_fuente='decisor',
            ),
        )

    def _crear_solicitud(self):
        solicitud = ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento='900000001',
            nombres='Persona',
            apellidos='Prueba',
            celular='3000000000',
            correo='persona@example.com',
            direccion='Direccion prueba',
            cargo='Consultoria',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=240),
            valor_total_contrato=Decimal('50000000'),
            valor_pagado_contrato=Decimal('2000000'),
            valor_pendiente_cobrar=Decimal('48000000'),
            forma_pago=ContractorApplication.FormaPago.MENSUAL,
            valor_mensual_contractual=Decimal('6000000'),
            monto_solicitado=Decimal('3000000'),
            plazo_meses=6,
            acepta_terminos=True,
            acepta_politica_privacidad=True,
            autoriza_analisis_contractual_asistido=True,
            autoriza_consulta_centrales=True,
            estado_analisis_contractual='COMPLETADO',
            metadata_analisis_contractual={
                'identidad': {'documento_coincide': True},
                'empresa_sugerida': {
                    'empresa_sugerida_id': self.empresa.id,
                    'tipo_coincidencia': 'NIT_EXACTO',
                },
                'bloqueos': [],
            },
            estado=ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )
        for tipo in ContractorApplicationDocument.TipoDocumento.values:
            extension = '.jpg' if tipo.startswith('CEDULA') else '.pdf'
            ContractorApplicationDocument.objects.create(
                solicitud=solicitud,
                tipo_documento=tipo,
                archivo=SimpleUploadedFile(f'{tipo}{extension}', b'data'),
                uploaded_by=self.usuario,
                metadata_captura={'source': 'capture'} if tipo.startswith('CEDULA') else {},
            )
        return solicitud

    def _marcar_simulada(self, solicitud, configuracion):
        solicitud.version_configuracion_financiera_simulacion = configuracion.version
        solicitud.version_politica_simulacion = self.politica.version_politica
        solicitud.monto_simulado = solicitud.monto_solicitado
        solicitud.plazo_simulado_meses = solicitud.plazo_meses
        solicitud.tasa_mensual_simulacion = configuracion.tasa_mensual
        solicitud.monto_maximo_configuracion_simulacion = configuracion.monto_maximo
        solicitud.plazo_maximo_configuracion_simulacion = configuracion.plazo_maximo_meses
        solicitud.simulada_en = timezone.now()
        solicitud.save()

    def _predecision(self, score):
        with patch(
            'contractors.services.predecision.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ), patch(
            'contractors.score.componentes.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ):
            return evaluar_predecision_formal_prestador(
                solicitud=self.solicitud,
                politica=self.politica,
                datacredito=self._datacredito(score),
            )


def crear_politica_score(*, version='v1', activa=True, configuracion_financiera=None):
    configuracion_financiera = configuracion_financiera or (
        ConfiguracionSimuladorPrestador.objects.filter(activo=True).first()
    )
    politica = ConfiguracionScorePrestador.objects.create(
        nombre=f'Politica {version}',
        version=version,
        activa=activa,
        fecha_vigencia_desde=timezone.localdate() - timedelta(days=1),
        configuracion_financiera=configuracion_financiera,
        peso_datacredito=Decimal('0.45'),
        peso_capacidad=Decimal('0.30'),
        peso_comportamiento=Decimal('0.08'),
        peso_riesgo=Decimal('0.12'),
        peso_referencias=Decimal('0.05'),
        cuota_ingreso_maxima=Decimal('0.30'),
        monto_maximo_politica=Decimal('10000000'),
        plazo_maximo_politica=8,
        tasa_mensual_referencia=Decimal('2.2000'),
        permite_redistribuir_pesos_faltantes=True,
        version_score=f'score-{version}',
        version_politica=f'politica-{version}',
    )
    bandas = (
        ('REVISION', 0, 599, 0, 0, 'REQUIERE_REVISION_MANUAL', 5),
        ('ENTRADA', 600, 679, 3000000, 6, 'PREAPROBADO_READ_ONLY', 4),
        ('MEDIA', 680, 749, 5000000, 8, 'PREAPROBADO_READ_ONLY', 3),
        ('ALTA', 750, 849, 8000000, 8, 'PREAPROBADO_READ_ONLY', 2),
        ('PREMIUM', 850, 1000, 10000000, 8, 'PREAPROBADO_READ_ONLY', 1),
    )
    for nombre, minimo, maximo, monto, plazo, resultado, orden in bandas:
        BandaScorePrestador.objects.create(
            configuracion=politica,
            nombre=nombre,
            score_min=minimo,
            score_max=maximo,
            monto_maximo=Decimal(monto),
            plazo_maximo=plazo,
            resultado=resultado,
            orden=orden,
        )
    validar_politica_score_completa(politica)
    return politica

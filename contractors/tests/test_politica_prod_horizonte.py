import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase

from contractors.forms import SimulacionPrestadorForm
from contractors.models import (
    BandaScorePrestador, ConfiguracionSimuladorPrestador, ConfiguracionScorePrestador,
    ContractorApplication, PredecisionPrestadorAudit,
)
from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador, ResultadoNormalizadoDatacreditoPrestador
from contractors.score.dto import ComponenteScorePrestador
from contractors.score.motor import evaluar_score_prestador
from contractors.services.capacidad_contractual import simular_credito_prestador_informativo, evaluar_capacidad_contractual_preliminar
from contractors.services.analisis_contrato import analizar_contrato_fallback
from contractors.services.horizonte_simulacion import (
    horizonte_para_solicitud, plazo_maximo_respaldado, fecha_ultima_cuota_proyectada,
    validar_plazo_contractual,
)
from contractors.tests.test_score_prestadores_v2 import crear_politica_score
from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from contractors.services.politica_financiera_prestador import (
    VERSION, PARAMETROS, IngresoNetoValido, preparar_oferta,
)
from gestion_creditos.services.horizonte_contractual import calcular_horizonte_contractual


ARCHIVO = Path(settings.BASE_DIR) / 'docs/parametros_prestadores_prod_aprobados.json'
EVIDENCIA = 'pagos mensuales; primeros cinco (5) dias habiles del mes siguiente'


def horizonte(meses=12, corte=date(2026, 8, 1), **kwargs):
    datos = dict(inicio=date(2026, 8, 1), fin=date(2026, 8, 1)+relativedelta(months=meses)-timedelta(days=1),
                 periodicidad='MENSUAL', valor_periodico=Decimal('10400000'),
                 evidencia_calendario=EVIDENCIA, corte=corte, duracion_meses=meses)
    datos.update(kwargs)
    return calcular_horizonte_contractual(**datos)


class HorizonteProdTest(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(version=VERSION, **PARAMETROS)

    def test_horizonte_limita_producto_sin_ampliar_ocho(self):
        for meses, limite in ((4, 4), (8, 8), (18, 8)):
            with self.subTest(meses=meses):
                h = horizonte(meses)
                self.assertEqual(h.periodos_futuros, meses)
                form = SimulacionPrestadorForm({'monto': '1000000', 'plazo_meses': limite}, configuracion=self.config, horizonte=h)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.plazo_maximo, limite)
                excesivo = SimulacionPrestadorForm({'monto': '1000000', 'plazo_meses': limite+1}, configuracion=self.config, horizonte=h)
                self.assertFalse(excesivo.is_valid())

    def test_uat_exigible_no_es_pagado_y_ultimo_flujo_posterior(self):
        h = horizonte(corte=date(2026, 9, 29), valor_pagado_al_corte_documento=Decimal('0'),
                      fecha_corte_documento=date(2026, 8, 1))
        self.assertEqual((h.periodos_totales, h.periodos_causados, h.periodos_exigibles, h.periodos_futuros), (12, 1, 1, 11))
        self.assertEqual(h.flujos[0].fecha_pago, date(2026, 9, 7))
        self.assertEqual(h.flujos[1].fecha_pago, date(2026, 10, 7))
        self.assertGreater(h.fecha_ultimo_flujo_contractual, date(2027, 7, 31))
        self.assertEqual(h.valor_exigible_a_fecha, Decimal('10400000'))
        self.assertEqual(h.flujo_contractual_futuro, Decimal('114400000'))
        self.assertEqual(h.valor_pagado_al_corte_documento, 0)
        self.assertIsNone(h.valor_pagado_actual_declarado)
        self.assertIsNone(h.valor_pagado_actual_verificado)

    def test_fin_contrato_no_elimina_ultimo_flujo_respaldado(self):
        h = horizonte(1, corte=date(2026, 9, 1))
        self.assertEqual(h.periodos_futuros, 1)
        self.assertEqual(plazo_maximo_respaldado(h, 8), 0)
        self.assertFalse(SimulacionPrestadorForm({'monto': '1000000', 'plazo_meses': 1},
                         configuracion=self.config, horizonte=h).is_valid())
        vencido = horizonte(1, corte=date(2026, 9, 8))
        self.assertEqual(vencido.periodos_futuros, 0)
        self.assertFalse(SimulacionPrestadorForm({'monto': '1000000', 'plazo_meses': 1},
                         configuracion=self.config, horizonte=vencido).is_valid())

    def test_festivo_fin_semana_y_limite_de_exigibilidad(self):
        h = horizonte(1, inicio=date(2026, 7, 1), fin=date(2026, 7, 31), corte=date(2026, 8, 1))
        self.assertEqual(h.flujos[0].fecha_pago, date(2026, 8, 10))  # 7 agosto festivo.
        h = horizonte(1, corte=date(2026, 9, 7))
        self.assertEqual(h.periodos_exigibles, 1)
        self.assertEqual(h.periodos_futuros, 0)

    def test_calendario_incompleto_inconsistente_o_no_mensual_no_inventa(self):
        for cambios in ({'evidencia_calendario': 'pagos mensuales'},
                        {'evidencia_calendario': 'primeros dias habiles del mes siguiente'},
                        {'duracion_meses': 11}, {'periodicidad': 'VARIABLE'},
                        {'inicio': date(2026, 8, 3)}, {'valor_periodico': None}):
            with self.subTest(cambios=cambios):
                self.assertFalse(horizonte(**cambios).disponible)

    def test_fin_mes_y_febrero_bisiesto(self):
        h = horizonte(1, inicio=date(2028, 2, 1), fin=date(2028, 2, 29), corte=date(2028, 2, 1),
                      evidencia_calendario='pago el ultimo dia del mes')
        self.assertEqual(h.flujos[0].fecha_pago, date(2028, 2, 29))

    def test_json_y_componentes_prod(self):
        datos = json.loads(ARCHIVO.read_text(encoding='utf-8'))
        self.assertEqual(datos.pop('version'), VERSION)
        for clave, valor in PARAMETROS.items():
            self.assertEqual(Decimal(str(datos[clave])), valor)
        r = simular_credito_prestador_informativo(monto='1000000', plazo_meses=1, configuracion=self.config)
        for campo, valor in {'desembolso_neto':'1000000', 'costo_originacion':'100000',
            'iva_costo_originacion':'19000', 'fondo_garantia':'20000', 'seguro_vida':'3711',
            'capital_total_financiado':'1142711', 'tasa_mensual':'0.022'}.items():
            self.assertEqual(getattr(r, campo), Decimal(valor))
        for monto in ('999999.99', '10000000.01'):
            with self.assertRaises(ValueError):
                simular_credito_prestador_informativo(monto=monto, plazo_meses=1, configuracion=self.config)

    @patch('django.utils.timezone.localdate', return_value=date(2026,8,1))
    def test_capacidad_prod_y_evidencia_documental_sin_promover_a_verificado(self, _fecha):
        resultado = analizar_contrato_fallback(None, texto_pdf=(
            'Doce pagos mensuales. Cada pago se hara dentro de los primeros cinco (5) dias\n'
            'habiles del mes siguiente al periodo causado. No se registra ningun pago efectuado.'
        ))
        solicitud = SimpleNamespace(id=1, fecha_inicio_contrato=date(2026,8,1),
            fecha_fin_contrato=date(2027,7,31), forma_pago=resultado.forma_pago,
            evidencia_forma_pago=resultado.evidencia_forma_pago, duracion_contrato_meses=12,
            valor_mensual_contractual=Decimal('10400000'), valor_total_contrato=Decimal('124800000'),
            valor_pendiente_cobrar=Decimal('124800000'), valor_pagado_contrato=Decimal('10400000'),
            monto_solicitado=Decimal('1000000'), plazo_meses=4,
            metadata_analisis_contractual={'datos_sugeridos':resultado.datos_sugeridos()})
        h = horizonte_para_solicitud(solicitud, self.config)
        self.assertTrue(h.disponible)
        self.assertEqual(h.valor_pagado_al_corte_documento, 0)
        self.assertEqual(h.valor_pagado_actual_declarado, Decimal('10400000'))
        self.assertIsNone(h.valor_pagado_actual_verificado)
        self.assertIsNone(h.fecha_corte_documento)
        capacidad = evaluar_capacidad_contractual_preliminar(solicitud, True, self.config)
        simulacion = simular_credito_prestador_informativo(monto='1000000', plazo_meses=4,
                                                          configuracion=self.config, solicitud=solicitud)
        self.assertTrue(capacidad.calculable)
        self.assertEqual(capacidad.cuota_estimada_preliminar, simulacion.cuota_mensual)


class OfertaProdIntegradaTest(TestCase):
    def setUp(self):
        self.config = ConfiguracionSimuladorPrestador.objects.create(version=VERSION, **PARAMETROS)
        self.politica = crear_politica_score(version='prod-test', configuracion_financiera=self.config)
        self.solicitud = SimpleNamespace(
            monto_solicitado=Decimal('10000000'), plazo_meses=8,
            valor_pendiente_cobrar=Decimal('100000000'),
            version_configuracion_financiera_simulacion=self.config.version,
            version_politica_simulacion=self.politica.version_politica,
            monto_simulado=Decimal('10000000'), plazo_simulado_meses=8,
            tasa_mensual_simulacion=self.config.tasa_mensual,
            monto_maximo_configuracion_simulacion=self.config.monto_maximo,
            plazo_maximo_configuracion_simulacion=self.config.plazo_maximo_meses,
        )

    def score_del_motor(self, valor):
        # Entradas sinteticas controladas; el unico calculo ponderado es el motor real.
        componentes = tuple(
            ComponenteScorePrestador(nombre, True, Decimal(valor), getattr(self.politica, f'peso_{nombre}'))
            for nombre in ('datacredito', 'capacidad', 'comportamiento', 'riesgo', 'referencias')
        )
        with patch('contractors.score.motor.construir_componentes_score', return_value=(
            componentes, {'capacidad_disponible': Decimal('900000')}, (), {'disponible': False},
        )):
            return evaluar_score_prestador(self.solicitud, self.politica, None)

    def oferta(self, score=900, **kwargs):
        resultado = self.score_del_motor(score) if isinstance(score, (int, Decimal)) else score
        datos = dict(score=resultado, politica=self.politica,
            ingreso_neto=IngresoNetoValido(Decimal('100000000'), 'fuente-test-verificada', date(2026,8,1)),
            obligaciones_mensuales=Decimal('0'), monto_solicitado=Decimal('10000000'), plazo_solicitado=8,
            horizonte=horizonte(18), configuracion=self.config, corte=date(2026,8,1))
        datos.update(kwargs)
        return preparar_oferta(**datos)

    def test_bandas_y_limites_sin_calcular_score(self):
        for score, banda, monto, plazo in ((850,'PREMIUM',10000000,8), (849,'ALTA',8000000,8),
            (750,'ALTA',8000000,8), (749,'MEDIA',5000000,8), (680,'MEDIA',5000000,8),
            (679,'ENTRADA',3000000,6), (600,'ENTRADA',3000000,6), (599,'REVISION',0,0)):
            with self.subTest(score=score):
                r = self.oferta(score)
                self.assertEqual((r.banda,r.monto,r.plazo), (banda,Decimal(monto),plazo))
        self.assertEqual(self.oferta(horizonte=horizonte(4)).plazo, 4)
        self.assertEqual(self.oferta(600, horizonte=horizonte(8)).plazo, 6)
        self.assertEqual(self.oferta(plazo_solicitado=2).plazo, 2)
        self.assertEqual(self.oferta(monto_solicitado=Decimal('2000000')).monto, Decimal('2000000'))

    def test_capacidad_treinta_por_ciento_sobre_disponible_y_cuota_real(self):
        r = self.oferta(ingreso_neto=IngresoNetoValido(Decimal('3000000'), 'test', date(2026,8,1)),
                        obligaciones_mensuales=Decimal('1000000'))
        self.assertEqual(r.cuota_maxima, Decimal('600000'))
        self.assertLessEqual(r.cuota, r.cuota_maxima)
        siguiente = simular_credito_prestador_informativo(monto=r.monto+Decimal('.01'), plazo_meses=r.plazo, configuracion=self.config)
        self.assertGreater(siguiente.cuota_mensual, r.cuota_maxima)
        r = self.oferta(ingreso_neto=IngresoNetoValido(Decimal('1000'), 'test', date(2026,8,1)))
        self.assertEqual((r.monto,r.plazo), (0,0))
        self.assertEqual(self.oferta(monto_solicitado=Decimal('500000')).monto, 0)

    def test_no_asume_bruto_como_neto_ni_fuente_de_ingreso(self):
        with self.assertRaises(ValidationError):
            self.oferta(ingreso_neto=Decimal('10000000'))
        with self.assertRaises(ValidationError):
            self.oferta(ingreso_neto=IngresoNetoValido(Decimal('10000000'), '', date(2026,8,1)))

    def test_banda_persistida_y_versiones_en_resultado_serializable(self):
        for valor, nombre in ((900, 'PREMIUM'), (800, 'ALTA')):
            with self.subTest(nombre=nombre):
                r = self.oferta(valor)
                banda = self.politica.bandas.get(nombre=nombre)
                self.assertEqual((r.banda_id, r.monto, r.plazo), (banda.pk, banda.monto_maximo, banda.plazo_maximo))
                self.assertEqual(r.estado, 'OFERTA_CALCULADA')
                self.assertEqual(r.version_score, self.politica.version_score)
                self.assertEqual(r.version_politica, self.politica.version_politica)
                self.assertEqual(r.version_configuracion_financiera, self.config.version)
                json.dumps(r.como_dict())

    def test_topes_de_fixture_cambian_oferta_sin_cambiar_score(self):
        score = self.score_del_motor(800)
        banda = self.politica.bandas.get(nombre='ALTA')
        banda.monto_maximo = Decimal('4200000')
        banda.plazo_maximo = 3
        banda.full_clean()
        banda.save()
        r = self.oferta(score)
        self.assertEqual((r.banda_id, r.monto, r.plazo), (banda.pk, Decimal('4200000'), 3))
        self.assertEqual(score.score_final, Decimal('800'))

    def test_sin_neto_no_evaluable_no_usa_valor_contractual(self):
        score = self.score_del_motor(900)
        score = replace(score, variables_calculadas={
            **score.variables_calculadas,
            'ingreso_contractual_estimado': Decimal('100000000'),
            'valor_mensual_explicito': Decimal('100000000'),
        })
        r = self.oferta(score, ingreso_neto=None)
        self.assertEqual((r.estado, r.monto, r.plazo), ('NO_EVALUABLE', 0, 0))
        self.assertIn('no se infiere', r.motivo)

    def test_capacidades_separadas_no_recalibra_score(self):
        score = self.score_del_motor(900)
        antes = score.como_dict()
        r = self.oferta(score, ingreso_neto=IngresoNetoValido(Decimal('3000000'), 'test', date(2026, 8, 1)),
                        obligaciones_mensuales=Decimal('1000000'))
        self.assertEqual(r.capacidad_componente_score, Decimal('900000'))
        self.assertEqual(r.cuota_maxima, Decimal('600000'))
        self.assertEqual(r.capacidad_crediticia_oferta, Decimal('600000'))
        self.assertEqual(r.como_dict()['capacidad_crediticia_oferta'], '600000.00')
        self.assertEqual(score.como_dict(), antes)

    def test_version_o_configuracion_distinta_rechazada(self):
        score = self.score_del_motor(900)
        for campo in ('version_score', 'version_politica', 'banda'):
            with self.subTest(campo=campo), self.assertRaises(ValidationError):
                self.oferta(replace(score, **{campo: 'otra'}))
        otra = ConfiguracionSimuladorPrestador.objects.create(version='otra', activo=False, **PARAMETROS)
        otra.version = VERSION
        with self.assertRaises(ValidationError):
            self.oferta(score, configuracion=otra)

    def test_score_no_evaluable_o_bloqueado_no_oferta(self):
        score = self.score_del_motor(900)
        self.assertEqual(self.oferta(replace(score, score_final=None)).estado, 'NO_EVALUABLE')
        for cambios in ({'bloqueos': ('identidad',)}, {'requiere_revision_manual': True}):
            self.assertEqual(self.oferta(replace(score, **cambios)).estado, 'SIN_OFERTA')

    def test_fecha_ultima_cuota_respaldada_y_corte_deterministico(self):
        h = horizonte(4, corte=date(2026, 8, 29))
        self.assertEqual(h.periodos_futuros, 4)
        self.assertEqual(fecha_ultima_cuota_proyectada(h.corte, 4), date(2027, 1, 1))
        r = self.oferta(horizonte=h, corte=h.corte)
        self.assertEqual(r.plazo, 3)
        self.assertLessEqual(r.fecha_ultima_cuota_credito, r.fecha_ultimo_flujo_contractual)
        for corte, plazo in ((date(2026, 8, 14), 4), (date(2026, 8, 15), 3)):
            h = horizonte(4, corte=corte)
            self.assertEqual(self.oferta(horizonte=h, corte=corte).plazo, plazo)

    def test_fecha_igual_a_ultimo_flujo_admitida_y_posterior_no(self):
        h = horizonte(1)
        h = replace(h, flujos=(replace(h.flujos[0], fecha_pago=date(2026, 9, 1)),))
        r = self.oferta(horizonte=h)
        self.assertEqual((r.plazo, r.fecha_ultima_cuota_credito), (1, date(2026, 9, 1)))
        h = replace(h, flujos=(replace(h.flujos[0], fecha_pago=date(2026, 8, 31)),))
        self.assertEqual(self.oferta(horizonte=h).estado, 'SIN_OFERTA')

    def test_simulacion_formulario_y_revalidacion_comparten_limite_temporal(self):
        solicitud = SimpleNamespace(
            fecha_inicio_contrato=date(2026, 8, 1), fecha_fin_contrato=date(2026, 11, 30),
            forma_pago='MENSUAL', valor_mensual_contractual=Decimal('10400000'),
            evidencia_forma_pago=EVIDENCIA, duracion_contrato_meses=4,
            valor_pagado_contrato=None, metadata_analisis_contractual={},
        )
        corte = date(2026, 8, 29)
        h = horizonte_para_solicitud(solicitud, self.config, corte=corte)
        form = SimulacionPrestadorForm({'monto': '1000000', 'plazo_meses': 4}, configuracion=self.config, horizonte=h)
        self.assertEqual(form.plazo_maximo, 3)
        self.assertFalse(form.is_valid())
        with self.assertRaises(ValidationError):
            validar_plazo_contractual(solicitud, self.config, 4, corte=corte)
        validar_plazo_contractual(solicitud, self.config, 3, corte=corte)

    def test_componentes_reales_motor_a_oferta_sin_proveedores_ni_originacion(self):
        usuario = get_user_model().objects.create_user(username='oferta-test')
        empresa = Empresa.objects.create(nombre='Empresa Test', convenio_activo=True)
        solicitud = ContractorApplication.objects.create(
            usuario=usuario, empresa=empresa, tipo_documento='CC', numero_documento='900000001',
            nombres='Persona', apellidos='Test', celular='3000000000', correo='test@example.com',
            fecha_inicio_contrato=date(2026, 8, 1), fecha_fin_contrato=date(2028, 1, 31),
            duracion_contrato_meses=18, forma_pago='MENSUAL',
            valor_total_contrato=Decimal('1800000000'), valor_pagado_contrato=0,
            valor_pendiente_cobrar=Decimal('1800000000'), valor_mensual_contractual=Decimal('100000000'),
            monto_solicitado=Decimal('10000000'), plazo_meses=8,
            estado_analisis_contractual='COMPLETADO',
            metadata_analisis_contractual={'identidad': {'documento_coincide': True},
                'empresa_sugerida': {'empresa_sugerida_id': empresa.pk, 'tipo_coincidencia': 'NIT_EXACTO'}},
        )
        for campo in ('version_configuracion_financiera_simulacion', 'version_politica_simulacion',
                      'monto_simulado', 'plazo_simulado_meses', 'tasa_mensual_simulacion',
                      'monto_maximo_configuracion_simulacion', 'plazo_maximo_configuracion_simulacion'):
            setattr(solicitud, campo, getattr(self.solicitud, campo))
        self.politica.peso_midecisor = self.politica.peso_datacredito
        self.politica.peso_hdcplus = Decimal('0')
        from contractors.datacredito.dto import ResultadoCentralesPrestador
        centrales = ResultadoCentralesPrestador(
            decisor=ResultadoConsultaDatacreditoPrestador(estado='EXITOSO',
                resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(score_externo=1000)),
            historial=ResultadoConsultaDatacreditoPrestador(estado='EXITOSO',
                resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(cuota_mensual_total='1000000')),
            estado_global='COMPLETA', completa=True, requiere_revision_manual=False,
        )
        with patch('contractors.services.validacion_contractual.date') as fecha, patch(
            'contractors.score.componentes.obtener_autorizacion_datacredito_vigente', return_value=object(),
        ), patch('contractors.datacredito.adapter.consultar_proveedor_datacredito_prestador') as proveedor:
            fecha.today.return_value = date(2026, 8, 1)
            score = evaluar_score_prestador(solicitud, self.politica, centrales)
            self.assertEqual(score.banda, 'PREMIUM')
            r = self.oferta(score, obligaciones_mensuales=Decimal('1000000'))
            proveedor.assert_not_called()
        self.assertEqual((r.estado, r.monto, r.plazo), ('OFERTA_CALCULADA', Decimal('10000000'), 8))
        self.assertEqual(r.banda_id, self.politica.bandas.get(nombre='PREMIUM').pk)
        self.assertEqual(r.capacidad_componente_score, score.variables_calculadas['capacidad_disponible'])
        self.assertEqual((Credito.objects.count(), CreditoLibranza.objects.count(), PredecisionPrestadorAudit.objects.count()), (0, 0, 0))


class DryRunProdTest(TestCase):
    def test_validacion_aprobada_no_persiste_ni_crea_score(self):
        salida = StringIO()
        call_command('configurar_simulador_prestadores', archivo=str(ARCHIVO), stdout=salida)
        self.assertIn('Validacion revertida', salida.getvalue())
        self.assertFalse(ConfiguracionSimuladorPrestador.objects.exists())
        self.assertFalse(ConfiguracionScorePrestador.objects.exists())

    def test_demo_existente_no_se_convierte_en_prod(self):
        ConfiguracionSimuladorPrestador.objects.create(version='prestadores-demo-v1')
        with self.assertRaises(CommandError):
            call_command('configurar_simulador_prestadores', archivo=str(ARCHIVO))
        self.assertEqual(list(ConfiguracionSimuladorPrestador.objects.values_list('version',flat=True)), ['prestadores-demo-v1'])

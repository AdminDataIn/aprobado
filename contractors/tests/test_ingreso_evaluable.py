"""Synthetic income hierarchy through real financial services, never real HTTP."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador
from contractors.models import AprobacionInternaPrestador, IngresoNetoVerificadoPrestador
from contractors.services.aprobacion_interna import crear_o_reutilizar_aprobacion_interna
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable, ingreso_evaluable_vigente
from contractors.services.ingreso_neto import registrar_ingreso_neto
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA, SECURE_SSL_REDIRECT=False,
                   ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
                   CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='')
class IngresoEvaluableTest(FixturePipelineFinanciero, TestCase):
    def setUp(self):
        if self._testMethodName == 'test_pipeline_midecisor_usable_comparte_ingreso_con_oferta':
            self.semantica_ingreso_test = 'NETO_MENSUAL_ESTIMADO'
        super().setUp()

    def sin_manual(self):
        registrar_ingreso_neto(solicitud=self.solicitud, actor=self.staff, invalidar=True,
                              observacion='Invalidacion sintetica para probar fallback')

    def resolver(self, **kwargs):
        return resolver_ingreso_evaluable(self.solicitud, self.politica, **kwargs)

    def test_documental_tiene_prioridad_sin_inventar_registros(self):
        n = IngresoNetoVerificadoPrestador.objects.count()
        ingreso = self.resolver()
        self.assertEqual(ingreso.fuente, 'MANUAL_VERIFICADA')
        self.assertEqual(ingreso.monto, Decimal('10000000'))
        self.assertEqual(IngresoNetoVerificadoPrestador.objects.count(), n)

    def test_ausencia_total_no_crea_ingreso_verificado(self):
        with patch.object(self, 'verificar_ingreso'):
            otra = self.nueva_solicitud_autorizada()
        self.assertFalse(otra.ingresos_netos_verificados.exists())
        ingreso = resolver_ingreso_evaluable(otra, self.politica)
        self.assertEqual(ingreso.fuente, 'CONTRATO_ESTIMADO')
        self.assertEqual(ingreso.monto, Decimal('7500000.00'))
        self.assertFalse(otra.ingresos_netos_verificados.exists())

    def test_contrato_menos_25_y_oferta_solo_preliminar(self):
        self.sin_manual()
        ingreso = self.resolver()
        self.assertIsNotNone(ingreso)
        self.assertEqual(ingreso.monto, Decimal('7500000.00'))
        self.assertEqual(ingreso.fuente, 'CONTRATO_ESTIMADO')
        audit = self.evaluar(ingreso_estimado='6000000')
        self.assertIsNotNone(audit.snapshot_salida.get('score_resultado'),
                             (audit.resultado, audit.error_codigo, audit.razones))
        variables = audit.snapshot_salida['score_resultado']['variables_calculadas']
        self.assertEqual(Decimal(variables['ingreso_evaluable']), ingreso.monto)
        oferta = self.oferta(audit)
        self.assertEqual(oferta.estado, 'OFERTA_CALCULADA')
        self.assertFalse(oferta.habilitada_para_originacion)
        with self.assertRaisesMessage(ValidationError, 'ingreso documental'):
            crear_o_reutilizar_aprobacion_interna(audit, actor=self.staff)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_midecisor_estimado_requiere_semantica_y_modulo(self):
        self.sin_manual()
        audit = self.evaluar(ingreso_estimado='6000000')
        snap = audit.snapshot_midecisor
        self.assertEqual(snap.resultado_normalizado['ingreso_estimado_midecisor'], '6000000')
        self.assertTrue(snap.resultado_normalizado['ingreso_estimado_modulo_valido'])
        # Only synthetic test snapshots; production histories must never be rewritten.
        datos = {**snap.resultado_normalizado, 'ingreso_estimado_midecisor': '6000000',
                 'ingreso_estimado_modulo_valido': True}
        from integrations.models import ConsultaDatacreditoSnapshot
        ConsultaDatacreditoSnapshot.objects.filter(pk=snap.pk).update(resultado_normalizado=datos)
        decisor = ResultadoConsultaDatacreditoPrestador(estado='EXITOSO', servicio='decisor', snapshot_id=str(snap.pk))
        self.assertEqual(self.resolver(decisor=decisor).fuente, 'CONTRATO_ESTIMADO')
        self.politica.politica_ingreso_evaluable = {**self.politica.politica_ingreso_evaluable,
                                                   'semantica_midecisor': 'NETO_MENSUAL_ESTIMADO'}
        ingreso = self.resolver(decisor=decisor)
        self.assertEqual(ingreso.fuente, 'MIDECISOR_ESTIMADO')
        self.assertEqual(ingreso.monto, Decimal('6000000'))
        self.assertTrue(ingreso.evidencia['discrepancia'])
        for valor, modulo in [('20000000', True), ('-1', True), ('NaN', True),
                              ('0.001', True), ('6000000', False), (None, True)]:
            with self.subTest(valor=valor, modulo=modulo):
                ConsultaDatacreditoSnapshot.objects.filter(pk=snap.pk).update(resultado_normalizado={
                    **datos, 'ingreso_estimado_midecisor': valor, 'ingreso_estimado_modulo_valido': modulo})
                actual = self.resolver(decisor=decisor)
                if valor == '20000000':
                    self.assertEqual(actual.monto, Decimal('10000000'))
                else:
                    self.assertEqual(actual.fuente, 'CONTRATO_ESTIMADO')

    def test_hc14_nunca_aporta_ingreso_oficial(self):
        self.sin_manual()
        self.politica.politica_ingreso_evaluable = {**self.politica.politica_ingreso_evaluable,
                                                   'semantica_midecisor': 'NETO_MENSUAL_ESTIMADO'}
        audit = self.evaluar(hc='14', tx='05')
        self.assert_no_aprueba(audit)
        decisor = ResultadoConsultaDatacreditoPrestador(estado='ERROR_PERMANENTE', servicio='decisor',
                                                       snapshot_id=str(audit.snapshot_midecisor_id))
        self.assertEqual(self.resolver(decisor=decisor).fuente, 'CONTRATO_ESTIMADO')
        self.assertEqual(audit.snapshot_midecisor.resultado_normalizado, {})
        self.assertEqual(audit.snapshot_salida['ingreso_evaluable']['fuente'], 'CONTRATO_ESTIMADO')

    def test_pipeline_midecisor_usable_comparte_ingreso_con_oferta(self):
        self.sin_manual()
        audit = self.evaluar(ingreso_estimado='6000000')
        evidencia = audit.snapshot_salida['ingreso_evaluable']
        self.assertIsNotNone(audit.snapshot_salida.get('score_resultado'),
                             (audit.resultado, audit.error_codigo, audit.razones))
        variables = audit.snapshot_salida['score_resultado']['variables_calculadas']
        self.assertEqual(evidencia['fuente'], 'MIDECISOR_ESTIMADO')
        self.assertEqual(Decimal(variables['ingreso_evaluable']), Decimal('6000000'))
        self.assertEqual(self.oferta(audit).ingreso_neto_fuente, 'MIDECISOR_ESTIMADO')
        self.assertFalse(self.oferta(audit).habilitada_para_originacion)
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(audit, actor=self.staff)

    def test_no_determinable_finaliza_en_revision_sin_importe(self):
        self.sin_manual()
        self.solicitud.valor_mensual_contractual = None
        self.solicitud.save(update_fields=['valor_mensual_contractual'])
        audit = self.evaluar()
        self.assert_no_aprueba(audit)
        self.assertIsNone(audit.snapshot_salida['ingreso_evaluable']['monto'])
        self.assertEqual(audit.snapshot_salida['ingreso_evaluable']['estado'], 'NO_DETERMINABLE')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, self.solicitud.Estado.EN_REVISION_MANUAL)

    def test_verificar_despues_no_promueve_auditoria_estimada(self):
        self.sin_manual()
        audit = self.evaluar()
        historico = deepcopy(audit.snapshot_salida)
        self.verificar_ingreso('7500000')
        self.assertEqual(self.oferta(audit).estado, 'NO_EVALUABLE')
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(audit, actor=self.staff)
        audit.refresh_from_db()
        self.assertEqual(audit.snapshot_salida, historico)

    def test_contrato_sin_honorarios_o_invalido_no_inventa(self):
        self.sin_manual()
        original = self.solicitud.valor_mensual_contractual
        for valor in (None, Decimal('0')):
            self.solicitud.valor_mensual_contractual = valor
            self.assertIsNone(self.resolver())
        self.solicitud.valor_mensual_contractual = original
        self.solicitud.fecha_fin_contrato = timezone.localdate() - timedelta(days=1)
        self.assertIsNone(self.resolver())

    def test_insuficiencia_financiera_conserva_sin_oferta(self):
        self.verificar_ingreso('100000')
        audit = self.evaluar()
        self.assertNotEqual(self.oferta(audit).estado, 'OFERTA_CALCULADA')
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(audit, actor=self.staff)

    def test_hdc_sin_informacion_no_se_convierte_en_cero(self):
        self.sin_manual()
        audit = self.evaluar(hdc_sin_informacion=True)
        self.assertIsNone(audit.cuota_mensual_hdc)
        self.assert_no_aprueba(audit)

    def test_cambio_de_contrato_ingreso_o_politica_invalida(self):
        self.sin_manual()
        ingreso = self.resolver()
        corte = timezone.localdate()
        self.assertTrue(ingreso_evaluable_vigente(ingreso, solicitud_id=self.solicitud.pk, politica=self.politica, corte=corte))
        self.solicitud.valor_mensual_contractual = Decimal('9000000')
        self.solicitud.save(update_fields=['valor_mensual_contractual'])
        self.assertFalse(ingreso_evaluable_vigente(ingreso, solicitud_id=self.solicitud.pk, politica=self.politica, corte=corte))
        ingreso = self.resolver()
        self.politica.politica_ingreso_evaluable = {**self.politica.politica_ingreso_evaluable,
                                                   'descuento_contractual_porcentaje': '30.00'}
        self.assertFalse(ingreso_evaluable_vigente(ingreso, solicitud_id=self.solicitud.pk, politica=self.politica, corte=corte))
        self.assertEqual(self.resolver().monto, Decimal('6300000.00'))
        self.verificar_ingreso('8000000')
        self.assertEqual(self.resolver().fuente, 'MANUAL_VERIFICADA')

    def test_politica_usada_no_cambia_significado(self):
        self.evaluar()
        anterior = construir_version_datos(self.solicitud)[0]
        self.politica.politica_ingreso_evaluable = {**self.politica.politica_ingreso_evaluable,
                                                   'descuento_contractual_porcentaje': '30.00'}
        with self.assertRaises(ValidationError):
            self.politica.full_clean()
        self.assertEqual(construir_version_datos(self.solicitud)[0], anterior)

    def test_politica_ingreso_incompleta_o_invalida_falla_cerrada(self):
        from contractors.politica_ingresos import validar_politica_ingreso, politica_ingreso_default
        self.assertEqual(validar_politica_ingreso(politica_ingreso_default())['descuento_contractual_porcentaje'], '25.00')
        for cambios in ({'descuento_contractual_porcentaje':'100'},
                        {'descuento_contractual_porcentaje':'NaN'},
                        {'descuento_contractual_porcentaje':'-1'},
                        {'version':'desconocida'}, {'semantica_midecisor':{}},
                        {'semantica_midecisor':'BRUTO_ANUAL'}):
            with self.subTest(cambios=cambios), self.assertRaises(ValidationError):
                validar_politica_ingreso({**politica_ingreso_default(), **cambios})
        with self.assertRaises(ValidationError):
            validar_politica_ingreso({})

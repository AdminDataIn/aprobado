from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from contractors.services.analisis_contrato import ResultadoAnalisisContrato, analizar_contrato_fallback, leer_texto_pdf
from contractors.services.analisis_contrato_ia import _normalizar
from contractors.services.analisis_contractual_seguro import analizar_contrato_seguro
from contractors.services.extraccion_campos import derivar_pendiente, ESQUEMA, datos_canonicos
from contractors.tests.contrato_uat_fixture import pdf_uat
from gestion_creditos.models import Empresa


@override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='')
class ExtraccionContratoUATTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='PRUEBAS DATAIN', nit='999888777', convenio_activo=True)

    def test_pdf_textual_extrae_todos_los_hechos_reportados(self):
        archivo = pdf_uat()
        texto, error = leer_texto_pdf(archivo)
        self.assertFalse(error)
        self.assertIn('124.800.000', texto)
        resultado = analizar_contrato_fallback(archivo)
        esperado = {
            'empresa_contratante': 'PRUEBAS DATAIN', 'nit_empresa': '999888777',
            'nombre_contratista': 'CARLOS DANIEL ORTIZ ANGEL', 'documento_contratista': '1006442329',
            'cargo_o_servicio': 'Project Manager', 'valor_total_contrato': Decimal('124800000'),
            'valor_pagado_estimado': Decimal('0'), 'valor_mensual_o_honorarios': Decimal('10400000'),
            'duracion_meses_contrato': 12, 'fecha_inicio_contrato': date(2026,8,1),
            'fecha_fin_contrato': date(2027,7,31), 'forma_pago': 'MENSUAL',
        }
        for campo, valor in esperado.items():
            with self.subTest(campo=campo):
                self.assertEqual(getattr(resultado, campo), valor)

    def test_pipeline_deriva_saldo_y_preserva_procedencia(self):
        resultado = analizar_contrato_seguro(solicitud=SimpleNamespace(numero_documento='1006442329'), documento=pdf_uat())
        self.assertFalse(resultado.bloqueos)
        self.assertEqual(resultado.datos['valor_pendiente_estimado'], '124800000')
        campo = resultado.metadata['campos_extraidos']['valor_pendiente_cobrar']
        self.assertEqual(campo['fuente'], 'DERIVADO_DETERMINISTICAMENTE')
        self.assertEqual(campo['sustentado_por'], ['valor_total_contrato', 'valor_pagado_contrato'])
        self.assertEqual(resultado.empresa_sugerida['empresa_sugerida_id'], self.empresa.pk)
        self.assertFalse(any('pocos campos' in aviso for aviso in resultado.advertencias))

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_ia_parcial_completa_faltantes_sin_reemplazar_campo_valido(self, ia):
        ia.return_value = ResultadoAnalisisContrato(fuente='openai', nombre_contratista='CARLOS DANIEL ORTIZ ANGEL', cargo_o_servicio='Project Manager Senior')
        resultado = analizar_contrato_seguro(solicitud=SimpleNamespace(numero_documento='1006442329'), documento=pdf_uat())
        campos = resultado.metadata['campos_extraidos']
        self.assertEqual(campos['cargo']['valor'], 'Project Manager Senior')
        self.assertEqual(campos['cargo']['fuente'], 'IA')
        self.assertEqual(campos['valor_total_contrato']['valor'], '124800000')
        self.assertEqual(campos['valor_total_contrato']['fuente'], 'REGEX_TEXTO_PDF')
        self.assertEqual(campos['valor_pendiente_cobrar']['valor'], '124800000')

    def test_aliases_se_convierten_solo_en_la_frontera(self):
        for datos in ({'cargo':'Project Manager', 'valor_pagado_contrato':0, 'duracion_contrato_meses':12},
                      {'cargo_o_servicio':'Project Manager', 'valor_pagado_estimado':0, 'duracion_meses_contrato':12}):
            resultado = _normalizar(datos, modelo='mock')
            self.assertEqual(resultado.cargo_o_servicio, 'Project Manager')
            self.assertEqual(resultado.valor_pagado_estimado, Decimal('0'))
            self.assertEqual(resultado.duracion_meses_contrato, 12)
        self.assertEqual(set(datos_canonicos({})), set(ESQUEMA))
        self.assertEqual(datos_canonicos({'cargo':'Canonico', 'cargo_o_servicio':'Legacy'})['cargo'], 'Canonico')

    def test_derivacion_acotada_decimal_sin_negativos_ni_reemplazo(self):
        for total, pagado in ((None, Decimal('0')), (Decimal('10'), None), (Decimal('10'), Decimal('11'))):
            resultado = derivar_pendiente(ResultadoAnalisisContrato(valor_total_contrato=total, valor_pagado_estimado=pagado))
            self.assertIsNone(resultado.valor_pendiente_estimado)
        resultado = derivar_pendiente(ResultadoAnalisisContrato(valor_total_contrato=Decimal('124800000.37'), valor_pagado_estimado=Decimal('0.12')))
        self.assertEqual(resultado.valor_pendiente_estimado, Decimal('124800000.25'))
        resultado = derivar_pendiente(ResultadoAnalisisContrato(valor_total_contrato=Decimal('100'), valor_pagado_estimado=Decimal('0'), valor_pendiente_estimado=Decimal('80')))
        self.assertEqual(resultado.valor_pendiente_estimado, Decimal('80'))

    def test_no_infiere_cero_de_ausencia_o_pago_pendiente(self):
        for texto in ('No se registra ningun pago pendiente.', 'Valor total: $100', 'Forma de pago: mensual.'):
            resultado = analizar_contrato_fallback(None, texto_pdf=texto)
            self.assertIsNone(resultado.valor_pagado_estimado)
        for texto in ('no se registra ningún pago efectuado', 'NO SE REGISTRA NINGUN PAGO EFECTUADO'):
            self.assertEqual(analizar_contrato_fallback(None, texto_pdf=texto).valor_pagado_estimado, Decimal('0'))

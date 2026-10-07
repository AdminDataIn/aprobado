from datetime import date
from decimal import Decimal
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings

from contractors.services.analisis_contrato import ResultadoAnalisisContrato, analizar_contrato_fallback, leer_texto_pdf
from contractors.services.analisis_contrato_ia import _confianza, _decimal, analizar_contrato_con_openai
from contractors.services.analisis_contractual_seguro import analizar_contrato_seguro, sugerir_empresa_exacta
from contractors.services.extraccion_campos import CAMPOS, completar_faltantes, evidencia_campos
from contractors.services.reconciliacion_contractual import comparar, reconciliar, confirmar_reconciliacion
from gestion_creditos.models import Empresa


class ExtraccionCamposTest(SimpleTestCase):
    def test_saldo_derivado_actual_sin_pisar_evidencia_independiente(self):
        from contractors.services.reconciliacion_contractual import ajustar_datos_contractuales
        metadata = {'datos_sugeridos': {'valor_pendiente_estimado':'124800000'},
                    'campos_extraidos': {'valor_pendiente_cobrar': {'fuente':'DERIVADO_DETERMINISTICAMENTE'}}}
        datos = {'valor_total_contrato':Decimal('124800000'), 'valor_pagado_contrato':Decimal('20800000'),
                 'valor_pendiente_cobrar':Decimal('124800000')}
        ajustar_datos_contractuales(datos, metadata)
        self.assertEqual(datos['valor_pendiente_cobrar'], Decimal('104000000'))
        datos['valor_pendiente_cobrar'] = Decimal('120000000')
        ajustar_datos_contractuales(datos, metadata)
        self.assertEqual(datos['valor_pendiente_cobrar'], Decimal('120000000'))
        datos['valor_pendiente_cobrar'] = Decimal('124800000')
        metadata['campos_extraidos']['valor_pendiente_cobrar']['fuente'] = 'REGEX_TEXTO_PDF'
        ajustar_datos_contractuales(datos, metadata)
        self.assertEqual(datos['valor_pendiente_cobrar'], Decimal('124800000'))

    def test_pdf_real_lee_texto_reinicia_stream_y_no_lanza_ocr(self):
        from weasyprint import HTML
        pdf = HTML(string='<p>Valor total del contrato: $ 12.000.000,37</p>').write_pdf()
        archivo = SimpleUploadedFile('contrato.pdf', pdf, content_type='application/pdf')
        texto, error = leer_texto_pdf(archivo)
        self.assertFalse(error)
        self.assertIn('12.000.000,37', texto)
        self.assertEqual(archivo.tell(), 0)
        self.assertEqual(analizar_contrato_fallback(archivo).valor_total_contrato, Decimal('12000000.37'))

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='solo-test')
    @patch('openai.OpenAI')
    def test_proveedor_recibe_texto_si_disponible_y_error_no_filtra_pii(self, cliente):
        cliente.return_value.responses.create.return_value.output_text = '{"nombres":"Ana"}'
        archivo = SimpleUploadedFile('privado.pdf', b'%PDF-1.4')
        resultado = analizar_contrato_con_openai(archivo, texto_pdf='Contrato de servicios\n' * 20)
        self.assertEqual(resultado.nombres, 'Ana')
        entrada = cliente.return_value.responses.create.call_args.kwargs['input'][0]['content'][0]
        self.assertEqual(entrada['type'], 'input_text')
        self.assertNotIn('file_data', entrada)
        cliente.return_value.responses.create.side_effect = RuntimeError('PII 123456789 Ana')
        with self.assertLogs('contractors.services.analisis_contrato_ia', level='WARNING') as logs:
            resultado = analizar_contrato_con_openai(archivo, texto_pdf='Texto\n' * 30)
        self.assertFalse(resultado.disponible)
        self.assertNotIn('123456789', str(logs.output))

    def test_fallback_centavos_y_campos_ausentes(self):
        resultado = analizar_contrato_fallback(None, texto_pdf='Valor total del contrato: $ 12.000.000,37\nValor pagado: $ 0\nHonorarios mensuales: $ 1.000.000,25\nFecha de inicio: 01/09/2026')
        self.assertEqual(resultado.valor_total_contrato, Decimal('12000000.37'))
        self.assertEqual(resultado.valor_pagado_estimado, 0)
        self.assertEqual(resultado.valor_mensual_o_honorarios, Decimal('1000000.25'))
        self.assertIsNone(resultado.valor_pendiente_estimado)
        self.assertEqual(resultado.fecha_inicio_contrato, date(2026, 9, 1))

    def test_ia_parcial_conserva_validos_y_completa_faltantes(self):
        ia = ResultadoAnalisisContrato(fuente='openai', nombres='Ana', valor_total_contrato=Decimal('500.37'), valor_pagado_estimado=Decimal('0'))
        fallback = ResultadoAnalisisContrato(nombres='Maria', valor_total_contrato=Decimal('999'), valor_pagado_estimado=Decimal('10'), valor_mensual_o_honorarios=Decimal('50'))
        resultado = completar_faltantes(ia, fallback)
        self.assertEqual(resultado.nombres, 'Ana')
        self.assertEqual(resultado.valor_total_contrato, Decimal('500.37'))
        self.assertEqual(resultado.valor_pagado_estimado, 0)
        self.assertEqual(resultado.valor_mensual_o_honorarios, 50)
        self.assertIsNone(resultado.valor_pendiente_estimado)
        evidencia = evidencia_campos(resultado)
        self.assertEqual(evidencia['nombres']['fuente'], 'IA')
        self.assertEqual(evidencia['valor_mensual_contractual']['fuente'], 'REGEX_TEXTO_PDF')
        self.assertFalse(evidencia['valor_pendiente_cobrar']['encontrado'])

    def test_ia_invalida_deja_espacio_al_fallback(self):
        resultado = completar_faltantes(ResultadoAnalisisContrato(nombres='Ana1', valor_total_contrato=Decimal('NaN')),
                                       ResultadoAnalisisContrato(nombres='Ana', valor_total_contrato=Decimal('100')))
        self.assertEqual(resultado.nombres, 'Ana')
        self.assertEqual(resultado.valor_total_contrato, 100)

    def test_confianza_no_es_importe_monetario(self):
        self.assertEqual(_confianza('0.98765'), Decimal('0.98765'))
        self.assertEqual(_confianza('NaN'), 0)
        self.assertIsNone(_decimal(1000.123))
        self.assertEqual(_decimal(1000.12), Decimal('1000.12'))

    def test_estados_comparacion_y_cero(self):
        self.assertEqual(comparar('nombres', 'Mar\u00eda', 'MARIA')['estado'], 'COINCIDE')
        self.assertEqual(comparar('valor_total_contrato', '10.00', '11')['estado'], 'DIFIERE')
        self.assertEqual(comparar('cargo', 'Abogado', '')['estado'], 'FALTANTE')
        self.assertEqual(comparar('valor_total_contrato', 'abc', '11')['estado'], 'NO_COMPARABLE')
        self.assertEqual(comparar('valor_pagado_contrato', 0, Decimal('0'))['formulario'], '0')

    def test_confirmacion_firmada_vinculada_a_valores_actor_y_archivo(self):
        class Formulario(forms.Form):
            valor_total_contrato = forms.DecimalField()
            confirma_discrepancias = forms.BooleanField(required=False)
            reconciliacion_token = forms.CharField(required=False)

        evidencia = {'archivo_hash_sha256': 'abc', 'metadata_segura': {'datos_sugeridos': {'valor_total_contrato': '100'}}}
        actor = SimpleNamespace(pk=1)
        form = Formulario({'valor_total_contrato': '200'})
        self.assertTrue(form.is_valid())
        _, error = confirmar_reconciliacion(form=form, evidencia=evidencia, actor=actor)
        self.assertTrue(error)
        datos = {**form.data, 'confirma_discrepancias': 'on'}
        confirmado = Formulario(datos)
        self.assertTrue(confirmado.is_valid())
        resultado, error = confirmar_reconciliacion(form=confirmado, evidencia={**evidencia, 'analizado_en': 'nuevo'}, actor=actor)
        self.assertFalse(error)
        self.assertTrue(resultado['metadata_segura']['reconciliacion']['confirmada'])
        for valor, usuario, archivo in [('300', actor, 'abc'), ('200', SimpleNamespace(pk=2), 'abc'), ('200', actor, 'otro')]:
            form = Formulario({**datos, 'valor_total_contrato': valor})
            self.assertTrue(form.is_valid())
            _, error = confirmar_reconciliacion(form=form, evidencia={**evidencia, 'archivo_hash_sha256': archivo}, actor=usuario)
            self.assertTrue(error)


class PipelineContractualTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='Empresa Alfa SAS', nit='900123456-8', convenio_activo=True)

    def analizar(self, resultado, texto='Valor total del contrato: $ 100,37'):
        with patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai', return_value=resultado), patch('contractors.services.analisis_contractual_seguro.leer_texto_pdf', return_value=(texto, '')):
            return analizar_contrato_seguro(solicitud=SimpleNamespace(numero_documento='123456789'), documento=None)

    def test_ia_parcial_completa_con_texto_y_no_infiere_saldo(self):
        resultado = self.analizar(ResultadoAnalisisContrato(fuente='openai', nombres='Ana'))
        self.assertEqual(resultado.datos['valor_total_contrato'], '100.37')
        self.assertEqual(resultado.datos['valor_pendiente_estimado'], '')
        self.assertEqual(resultado.metadata['campos_extraidos']['nombres']['fuente'], 'IA')

    def test_ia_completa_no_ejecuta_fallback(self):
        valores = {c: 'Dato' for c in CAMPOS}
        valores.update(nombres='Ana', apellidos='Perez', nombre_contratista='Ana Perez', documento_contratista='123456789', nit_empresa='900123456-8', fecha_inicio_contrato=date(2026,1,1), fecha_fin_contrato=date(2026,12,31), duracion_meses_contrato=12, forma_pago='MENSUAL', tipo_contrato='PRESTACION_SERVICIOS')
        valores.update({c: Decimal('100') for c in CAMPOS if c.startswith('valor_')})
        with patch('contractors.services.analisis_contractual_seguro.analizar_contrato_fallback') as fallback:
            self.analizar(ResultadoAnalisisContrato(**valores, fuente='openai'))
        fallback.assert_not_called()

    def test_proveedor_caido_y_fallo_total_degradan(self):
        self.assertEqual(self.analizar(ResultadoAnalisisContrato(disponible=False)).datos['valor_total_contrato'], '100.37')
        resultado = self.analizar(None, texto='')
        self.assertFalse(resultado.disponible)
        self.assertFalse(resultado.bloqueos)

    def test_documento_diferente_bloquea_sin_exponer_numero_en_metadata_o_log(self):
        with self.assertLogs('contractors.services.analisis_contractual_seguro', level='INFO') as logs:
            resultado = self.analizar(ResultadoAnalisisContrato(documento_contratista='987654321', nombres='Nombre Privado'))
        self.assertTrue(resultado.bloqueos)
        self.assertNotIn('987654321', str(resultado.metadata))
        self.assertNotIn('Nombre Privado', str(logs.output))
        self.assertNotIn('987654321', str(logs.output))

    def test_nit_primero_y_nunca_fallback_nombre_en_conflicto(self):
        self.assertEqual(sugerir_empresa_exacta(nit='900.123.456-8')['empresa_sugerida_id'], self.empresa.pk)
        for nit in ('900123456-7', '800123456'):
            self.assertIsNone(sugerir_empresa_exacta(nit=nit, nombre=self.empresa.nombre)['empresa_sugerida_id'])

    def test_empresas_homonimas_sin_nit_exigen_seleccion(self):
        Empresa.objects.create(nombre='Empresa Alfa S.A.S.', convenio_activo=True)
        self.assertEqual(sugerir_empresa_exacta(nombre=self.empresa.nombre)['match_tipo'], 'ambiguo')

    def test_reconciliacion_no_muta_formulario_y_nit_diferente_se_muestra(self):
        datos = {'nombres': 'Ana', 'empresa': self.empresa, 'valor_total_contrato': Decimal('200')}
        anterior = datos.copy()
        filas = reconciliar({'datos_sugeridos': {'nombres': 'Maria', 'nit_empresa': '800123456', 'valor_total_contrato': '100'}}, datos)
        self.assertEqual(datos, anterior)
        self.assertTrue(all(f['estado'] == 'DIFIERE' for f in filas if f['campo'] in ('nombres', 'nit_empresa', 'valor_total_contrato')))

    def test_ocr_usable_se_compara_sin_verificar_identidad_y_respeta_ownership(self):
        from django.contrib.auth import get_user_model
        from django.core.exceptions import ValidationError
        from gestion_creditos.models import ProcesamientoOCRDocumental
        from gestion_creditos.tests.captura_fixtures import sesion_finalizada

        with tempfile.TemporaryDirectory() as directory, override_settings(PRIVATE_DOCUMENTS_ROOT=directory):
            actor = get_user_model().objects.create_user(username='titular-ocr')
            sesion = sesion_finalizada(actor, 'PRESTADORES')
            ocr = ProcesamientoOCRDocumental.objects.create(
                sesion=sesion, captura_frontal=sesion.capturas.get(lado='FRONTAL'),
                captura_trasera=sesion.capturas.get(lado='TRASERA'), estado='COMPLETADO',
                version_parser='test', firma_configuracion='a' * 64, firma_inputs='b' * 64,
                tipo_documento_detectado='CC', nombres_normalizado='Maria', numero_documento_normalizado='987654321')
            datos = {'tipo_documento': 'CC', 'nombres': 'Ana', 'numero_documento': '123456789'}
            filas = reconciliar({}, datos, sesion=sesion)
            diferencias = [f for f in filas if f['fuente'] == 'OCR_CEDULA_NO_IDENTIDAD' and f['estado'] == 'DIFIERE']
            self.assertEqual(len(diferencias), 2)
            self.assertNotIn('987654321', str(filas))
            ocr.vigente = False
            ocr.save(update_fields=['vigente'])
            self.assertFalse(any(f['fuente'] == 'OCR_CEDULA_NO_IDENTIDAD' for f in reconciliar({}, datos, sesion=sesion)))
            form = SimpleNamespace(cleaned_data=datos)
            with self.assertRaises(ValidationError):
                confirmar_reconciliacion(form=form, evidencia={}, actor=SimpleNamespace(pk=actor.pk + 1), sesion=sesion)

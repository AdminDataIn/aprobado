from datetime import date
from decimal import Decimal
import os
import shutil
import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from contractors.models import (
    ConfiguracionPortalContratistas,
    ContractorApplication,
    ContractorApplicationDocument,
)
from contractors.services.analisis_contrato import analizar_contrato_con_ia
from contractors.services.analisis_contrato_ia import (
    ResultadoAnalisisContratoIAOpenAI,
    analizar_contrato_con_openai,
    normalizar_resultado_analisis_contrato,
    validar_resultado_analisis_contrato,
    _crear_respuesta_openai,
)
from contractors.services.analisis_contractual_seguro import (
    TIPO_EMPRESA_NIT_EXACTO,
    enriquecer_analisis_contrato_prestador,
    sugerir_empresa_por_contrato,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa


class AnalisisContratoIATests(TestCase):
    def test_placeholder_legacy_no_extrae_ni_llama_api_externa(self):
        documento = Mock(name='contrato.pdf')

        resultado = analizar_contrato_con_ia(documento)

        self.assertIsNone(resultado.fecha_inicio_contrato)
        self.assertIsNone(resultado.fecha_fin_contrato)
        self.assertIsNone(resultado.valor_total_contrato)
        self.assertEqual(resultado.confidence, Decimal('0.00'))
        self.assertIn('fecha_inicio_contrato', resultado.campos_requieren_confirmacion)
        self.assertIn('valor_pendiente_estimado', resultado.campos_requieren_confirmacion)

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='')
    def test_openai_deshabilitado_no_llama_api(self):
        documento = SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf')

        resultado = analizar_contrato_con_openai(documento)

        self.assertFalse(resultado.habilitado)
        self.assertFalse(resultado.exito)
        self.assertEqual(resultado.error, 'ia_deshabilitada')
        self.assertIn('fecha_inicio_contrato', resultado.campos_no_encontrados)

    def test_normaliza_resultado_analisis_contrato(self):
        resultado = normalizar_resultado_analisis_contrato(
            {
                'es_contrato': True,
                'tipo_documento_detectado': 'contrato de prestacion de servicios',
                'empresa_contratante': 'Empresa SAS',
                'nit_empresa': '900123456',
                'nombre_contratista': 'Ana Perez',
                'documento_contratista': '1020304050',
                'cargo_o_servicio': 'Consultoria',
                'fecha_inicio_contrato': '2026-01-01',
                'fecha_fin_contrato': '2026-12-31',
                'valor_total_contrato': '12000000',
                'valor_mensual_o_honorarios': '1000000',
                'valor_pendiente_estimado': '8000000',
                'moneda': 'COP',
                'resumen': 'Contrato anual.',
                'campos_no_encontrados': [],
                'advertencias': ['Confirmar valores.'],
                'confianza_general': '0.82',
                'requiere_confirmacion_usuario': True,
            },
            modelo_usado='gpt-4o-mini',
        )

        self.assertTrue(resultado.es_contrato)
        self.assertEqual(resultado.fecha_inicio_contrato, date(2026, 1, 1))
        self.assertEqual(resultado.valor_total_contrato, Decimal('12000000'))
        self.assertEqual(resultado.confianza_general, Decimal('0.82'))
        self.assertEqual(resultado.modelo_usado, 'gpt-4o-mini')
        self.assertTrue(resultado.requiere_confirmacion_usuario)
        metadata = resultado.metadata_segura(documento_id=123)
        self.assertTrue(metadata['enabled'])
        self.assertTrue(metadata['attempted'])
        self.assertTrue(metadata['success'])
        self.assertEqual(metadata['modelo'], 'gpt-4o-mini')
        self.assertIn('empresa_contratante', metadata['campos_detectados'])
        self.assertEqual(metadata['documento_id'], 123)
        self.assertNotIn('resumen', metadata)
        self.assertNotIn('prompt', str(metadata).lower())
        self.assertNotIn('base64', str(metadata).lower())

    def test_valida_resultado_no_contrato(self):
        resultado = normalizar_resultado_analisis_contrato(
            {'es_contrato': False, 'campos_no_encontrados': []},
            modelo_usado='gpt-4o-mini',
        )

        with self.assertRaisesMessage(Exception, 'El documento cargado no parece ser un contrato valido.'):
            validar_resultado_analisis_contrato(resultado)

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_openai_devuelve_error_controlado_si_sdk_falla(self):
        documento = SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf')

        with patch('openai.OpenAI') as cliente_mock:
            cliente_mock.side_effect = RuntimeError('sin red')
            resultado = analizar_contrato_con_openai(documento)

        self.assertTrue(resultado.habilitado)
        self.assertFalse(resultado.exito)
        self.assertEqual(resultado.error, 'error_openai')

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_openai_clasifica_cuota_excedida(self):
        class RateLimitError(Exception):
            pass

        documento = SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf')

        with patch('openai.OpenAI') as cliente_mock:
            cliente_mock.side_effect = RateLimitError(
                'Error code: 429 - exceeded your current quota, please check your plan and billing details'
            )
            resultado = analizar_contrato_con_openai(documento)

        self.assertTrue(resultado.habilitado)
        self.assertFalse(resultado.exito)
        self.assertEqual(resultado.error, 'cuota_openai_excedida')

    def test_pdf_se_envia_como_data_url_a_responses_api(self):
        cliente = Mock()
        cliente.responses.create.return_value = Mock(output_text='{}')

        _crear_respuesta_openai(cliente, 'gpt-4.1-mini', 'contrato.pdf', b'%PDF-contrato')

        llamada = cliente.responses.create.call_args.kwargs
        contenido = llamada['input'][0]['content'][0]
        self.assertEqual(contenido['type'], 'input_file')
        self.assertEqual(contenido['filename'], 'contrato.pdf')
        self.assertTrue(contenido['file_data'].startswith('data:application/pdf;base64,'))

    def test_infiere_fecha_final_desde_duracion_meses(self):
        resultado_ia = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            fecha_inicio_contrato=date(2026, 1, 15),
            duracion_meses_contrato=6,
            habilitado=True,
            exito=True,
        )

        resultado = enriquecer_analisis_contrato_prestador(resultado_ia, fecha_base=date(2026, 2, 1))

        self.assertEqual(resultado.datos['fecha_fin_contrato'], '2026-07-15')
        self.assertEqual(resultado.metadata['fecha_fin_fuente'], 'inferida_duracion_meses')
        self.assertTrue(resultado.metadata['fecha_fin_requiere_confirmacion'])
        self.assertIn('CONTRATO_FECHA_FINAL_INFERIDA', resultado.eventos)

    def test_valor_pendiente_calculado_desde_total_menos_pagado(self):
        resultado_ia = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_estimado=Decimal('5000000.00'),
            habilitado=True,
            exito=True,
        )

        resultado = enriquecer_analisis_contrato_prestador(resultado_ia, fecha_base=date(2026, 2, 1))

        self.assertEqual(resultado.datos['valor_pendiente_estimado'], '7000000.00')
        self.assertEqual(resultado.metadata['valor_pendiente_fuente'], 'calculada_total_menos_pagado')
        self.assertTrue(resultado.metadata['valor_pendiente_requiere_confirmacion'])
        self.assertIn('CONTRATO_VALOR_PENDIENTE_INFERIDO', resultado.eventos)

    def test_contrato_vencido_deja_capacidad_cero_y_bloqueo(self):
        resultado_ia = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            fecha_fin_contrato=date(2026, 1, 31),
            valor_pendiente_estimado=Decimal('3000000.00'),
            habilitado=True,
            exito=True,
        )

        resultado = enriquecer_analisis_contrato_prestador(resultado_ia, fecha_base=date(2026, 2, 1))

        self.assertEqual(resultado.datos['valor_pendiente_estimado'], '0.00')
        self.assertIn('contrato_vencido_detectado', resultado.bloqueos)
        self.assertTrue(resultado.metadata['contrato_vencido_detectado'])
        self.assertIn('CONTRATO_VENCIDO_DETECTADO', resultado.eventos)

    def test_sugiere_empresa_por_nit_sin_seleccion_automatica(self):
        empresa = Empresa.objects.create(
            nombre='Aprobado SAS',
            razon_social='Aprobado Soluciones Digitales SAS',
            nit='900123456-7',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )

        sugerencia = sugerir_empresa_por_contrato(
            nit_empresa='9001234567',
            nombre_empresa='Aprobado Soluciones Digitales',
        )

        self.assertEqual(sugerencia.empresa_id, empresa.id)
        self.assertEqual(sugerencia.tipo_coincidencia, TIPO_EMPRESA_NIT_EXACTO)
        self.assertTrue(sugerencia.requiere_confirmacion)


MEDIA_ROOT_ANALISIS_TEMPORAL = tempfile.mkdtemp()


@override_settings(
    PRIMARY_DOMAIN_HOST='aprobado.com.co',
    CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
    ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    MEDIA_ROOT=MEDIA_ROOT_ANALISIS_TEMPORAL,
)
class EndpointAnalisisContratoContratistaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT_ANALISIS_TEMPORAL, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Contratistas',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('10000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.usuario = get_user_model().objects.create_user(
            username='analisis-contrato',
            email='analisis@example.com',
            password='password-test',
        )
        self.client.force_login(self.usuario)

    def _archivo_pdf(self, nombre='contrato.pdf', contenido=b'%PDF-contrato'):
        return SimpleUploadedFile(nombre, contenido, content_type='application/pdf')

    def _post(self, archivo=None, **extra):
        datos = {
            'contrato_actual': archivo or self._archivo_pdf(),
            'tratamiento_datos_analisis_ia': '1',
        }
        datos.update(extra)
        return self.client.post(
            '/contrato/analizar/',
            datos,
            HTTP_HOST='contratistas.aprobado.com.co',
        )

    def test_anonimo_no_accede(self):
        self.client.logout()

        response = self._post()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/login/?next=/contrato/analizar/')

    def test_sin_csrf_no_accede(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)

        response = cliente.post(
            '/contrato/analizar/',
            {
                'contrato_actual': self._archivo_pdf(),
                'tratamiento_datos_analisis_ia': '1',
            },
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 403)

    def test_sin_aceptacion_tratamiento_datos_no_llama_openai(self):
        with patch('contractors.views.analizar_contrato_con_openai') as analisis_mock:
            response = self.client.post(
                '/contrato/analizar/',
                {'contrato_actual': self._archivo_pdf()},
                HTTP_HOST='contratistas.aprobado.com.co',
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertEqual(
            response.json()['error'],
            'Debes aceptar la autorizacion de tratamiento de datos antes de analizar el contrato.',
        )
        analisis_mock.assert_not_called()

    def test_archivo_no_pdf_rechaza(self):
        response = self._post(
            archivo=SimpleUploadedFile('contrato.jpg', b'imagen', content_type='image/jpeg'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_respuesta_no_contrato_bloquea(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=False,
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['success'])
        self.assertFalse(response.json()['manual_allowed'])
        self.assertEqual(response.json()['error'], 'El documento cargado no parece ser un contrato valido.')

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_cuota_openai_devuelve_mensaje_operativo(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            habilitado=True,
            exito=False,
            error='cuota_openai_excedida',
            modelo_usado='gpt-4o-mini',
        )

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertTrue(data['manual_allowed'])
        self.assertEqual(data['metadata']['error_tipo'], 'cuota_openai_excedida')
        self.assertIn('cuota o facturacion de OpenAI', data['error'])

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_no_persiste_archivo_ni_modelos_financieros(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            cargo_o_servicio='Consultoria',
            fecha_inicio_contrato=date(2026, 1, 1),
            fecha_fin_contrato=date(2026, 12, 31),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pendiente_estimado=Decimal('8000000.00'),
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertRegex(data['metadata']['analysis_input_hash'], r'^[a-f0-9]{64}$')
        self.assertIn('analysis_generated_at', data['metadata'])
        self.assertEqual(
            data['analisis_contractual_seguro']['metadata']['analysis_input_hash'],
            data['metadata']['analysis_input_hash'],
        )
        self.assertEqual(ContractorApplication.objects.count(), 0)
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        archivos_media = [
            archivo
            for _, _, archivos in os.walk(MEDIA_ROOT_ANALISIS_TEMPORAL)
            for archivo in archivos
        ]
        self.assertEqual(archivos_media, [])
        serializado = str(response.json()).lower()
        self.assertNotIn('prompt', serializado)
        self.assertNotIn('base64', serializado)
        self.assertNotIn('%pdf-contrato', serializado)
        self.assertNotIn('api_key', serializado)

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_rate_limit_bloquea_abuso_basico(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            respuestas = [self._post(archivo=self._archivo_pdf(nombre=f'contrato-{indice}.pdf')) for indice in range(4)]

        self.assertEqual([respuesta.status_code for respuesta in respuestas[:3]], [200, 200, 200])
        self.assertEqual(respuestas[3].status_code, 429)

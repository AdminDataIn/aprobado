from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.forms import SolicitudPrestadorForm
from contractors.services.analisis_contrato import ResultadoAnalisisContrato
from contractors.services.analisis_contrato_ia import analizar_contrato_con_openai
from contractors.services.analisis_contractual_seguro import MENSAJE_DOCUMENTO_DIFERENTE
from contractors.services.capacidad_contractual import (
    ConfiguracionSimuladorNoDisponible,
    simular_credito_prestador_informativo,
)
from contractors.services.predecision import (
    RESULTADO_NO_EVALUABLE,
    RESULTADO_PREAPROBABLE,
    RESULTADO_REQUIERE_REVISION,
    evaluar_predecision_prestador,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa


@override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='')
class PortalMinimoPrestadoresTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='prestador',
            email='prestador@example.com',
            password='123456',
        )
        self.otro_usuario = get_user_model().objects.create_user(
            username='otro-prestador',
            email='otro@example.com',
            password='123456',
        )
        self.staff = get_user_model().objects.create_user(
            username='staff-prestadores',
            email='staff@example.com',
            password='123456',
            is_staff=True,
        )
        self.staff.user_permissions.add(*Permission.objects.filter(
            codename__in=[
                'can_view_contractor_review_queue',
                'can_assign_contractor_review',
                'can_resolve_contractor_review',
                'can_request_contractor_correction',
                'can_view_contractor_score_details',
            ]
        ))
        self.empresa = Empresa.objects.create(
            nombre='Empresa Convenio',
            convenio_activo=True,
        )
        self.otra_empresa = Empresa.objects.create(
            nombre='Otra Empresa Convenio',
            convenio_activo=True,
        )
        self.configuracion_simulador = ConfiguracionSimuladorPrestador.objects.create(
            nombre='Configuracion financiera de pruebas del portal',
            version='portal-tests-v1',
            activo=True,
            monto_minimo=Decimal('1000000'),
            monto_maximo=Decimal('10000000'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.2000'),
        )

    def test_raiz_subdominio_redirige_a_solicitar(self):
        response = self.client.get('/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_solicitar_exige_login(self):
        response = self.client.get('/solicitar/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])
        self.assertIn('next=/solicitar/', response['Location'])

    def test_login_accounts_carga_en_subdominio_prestadores(self):
        response = self.client.get('/accounts/login/?next=/solicitar/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ACCESO PRESTADORES')
        self.assertContains(response, 'Inicia sesión en Prestadores de Servicios')
        self.assertContains(response, 'Volver al portal de Prestadores')
        self.assertNotContains(response, 'Inicia sesión en Libranza')

    def test_usuario_autenticado_crea_solicitud_basica_con_empresa_existente(self):
        self.client.force_login(self.usuario)
        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()
        response = self.client.post(
            '/solicitar/',
            self._payload_solicitud_con_documentos(),
            HTTP_HOST=self.host,
        )

        solicitud = ContractorApplication.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.usuario, self.usuario)
        self.assertEqual(solicitud.empresa, self.empresa)
        self.assertIsNone(solicitud.monto_solicitado)
        self.assertIsNone(solicitud.plazo_meses)
        self.assertEqual(solicitud.tipo_contrato, ContractorApplication.TipoContrato.PRESTACION_SERVICIOS)
        self.assertEqual(solicitud.valor_pagado_contrato, 4000000)
        self.assertEqual(solicitud.valor_pendiente_cobrar, 8000000)
        self.assertEqual(solicitud.observaciones_contrato, 'Contrato confirmado por el prestador.')
        self.assertTrue(solicitud.acepta_terminos)
        self.assertTrue(solicitud.acepta_politica_privacidad)
        self.assertTrue(solicitud.autoriza_analisis_contractual_asistido)
        self.assertTrue(solicitud.autoriza_consulta_centrales)
        self.assertIn(
            solicitud.estado_analisis_contractual,
            {
                ContractorApplication.EstadoAnalisisContractual.COMPLETADO,
                ContractorApplication.EstadoAnalisisContractual.CON_ADVERTENCIAS,
                ContractorApplication.EstadoAnalisisContractual.NO_DISPONIBLE,
            },
        )
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.DOCUMENTOS_CARGADOS)
        self.assertEqual(solicitud.documentos.count(), 4)
        self.assertTrue(
            TimelinePrestador.objects.filter(
                solicitud=solicitud,
                tipo_evento=TimelinePrestador.TipoEvento.SOLICITUD_REGISTRADA,
            ).exists()
        )
        cedula_frontal = solicitud.documentos.get(
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
        )
        self.assertEqual(cedula_frontal.metadata_captura['source'], 'capture')
        self.assertIn('captured_at', cedula_frontal.metadata_captura)
        self.assertNotIn('base64', cedula_frontal.metadata_captura)
        self.assertEqual(response['Location'], f'/simular/?solicitud_id={solicitud.id}')
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    @override_settings(
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='prestadores-v1',
        DATACREDITO_AUTHORIZATION_TEXT='Autorización de consulta para pruebas.',
    )
    def test_formulario_registra_evidencia_versionada_de_autorizacion(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            '/solicitar/',
            self._payload_solicitud_con_documentos(),
            HTTP_HOST=self.host,
            HTTP_USER_AGENT='Navegador de prueba',
            REMOTE_ADDR='192.0.2.10',
        )

        self.assertEqual(response.status_code, 302)
        evidencia = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertTrue(evidencia.autorizada)
        self.assertEqual(evidencia.version_texto, 'prestadores-v1')
        self.assertEqual(len(evidencia.texto_hash), 64)
        self.assertEqual(len(evidencia.ip_hash), 64)
        self.assertNotEqual(evidencia.ip_hash, '192.0.2.10')

    def test_formulario_inicial_no_solicita_monto_plazo_y_continua_a_simulacion(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Monto solicitado')
        self.assertNotContains(response, 'Plazo solicitado')
        self.assertNotContains(response, 'id="id_monto_solicitado"')
        self.assertNotContains(response, 'id="id_plazo_meses"')
        self.assertContains(response, 'Continuar a simulación')
        self.assertNotContains(response, 'Registrar solicitud')

    def test_campos_contractuales_normalizan_separadores_colombianos(self):
        formulario = SolicitudPrestadorForm()

        self.assertEqual(
            formulario.fields['valor_total_contrato'].clean('80.000.000'),
            Decimal('80000000'),
        )
        self.assertEqual(
            formulario.fields['valor_pagado_contrato'].clean('$ 5.000.000'),
            Decimal('5000000'),
        )
        self.assertEqual(
            formulario.fields['valor_pendiente_cobrar'].clean('75,000,000'),
            Decimal('75000000'),
        )

    def test_campos_contractuales_rechazan_valor_invalido(self):
        formulario = SolicitudPrestadorForm()

        with self.assertRaises(ValidationError):
            formulario.fields['valor_total_contrato'].clean('ochenta millones')

    def test_formulario_muestra_montos_existentes_con_formato_colombiano(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='80000000',
            valor_pendiente='75000000',
        )
        solicitud.valor_pagado_contrato = Decimal('5000000')
        solicitud.save(update_fields=['valor_pagado_contrato'])
        self.client.force_login(self.usuario)

        response = self.client.get(
            f'/solicitar/?solicitud_id={solicitud.id}',
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'value="80.000.000"')
        self.assertContains(response, 'value="5.000.000"')
        self.assertContains(response, 'value="75.000.000"')
        self.assertContains(response, 'data-money-contract="true"', count=3)

    def test_formulario_expone_enlaces_legales_de_prestadores(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertContains(
            response,
            'href="/terminos-y-condiciones/" target="_blank" rel="noopener"',
        )
        self.assertContains(
            response,
            'href="/politica-de-privacidad/" target="_blank" rel="noopener"',
        )
        self.assertContains(
            response,
            'href="/centrales-de-informacion/" target="_blank" rel="noopener"',
        )

    def test_vistas_legales_prestadores_son_publicas(self):
        for ruta, titulo in (
            ('/terminos-y-condiciones/', 'Términos y condiciones para Prestadores de Servicios'),
            ('/politica-de-privacidad/', 'Política de privacidad para Prestadores de Servicios'),
            ('/centrales-de-informacion/', 'Autorización para consulta ante centrales de información'),
        ):
            with self.subTest(ruta=ruta):
                response = self.client.get(ruta, HTTP_HOST=self.host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, titulo)

    def test_vistas_legales_tienen_contenido_especifico_y_profesional(self):
        response = self.client.get('/terminos-y-condiciones/', HTTP_HOST=self.host)
        self.assertContains(response, 'class="legal-container"')
        self.assertContains(response, 'class="legal-intro"')
        self.assertContains(response, 'class="legal-card"')
        self.assertContains(response, 'class="legal-section"')
        self.assertContains(response, 'Documentos y análisis contractual')
        self.assertContains(response, 'Simulación informativa')

        response = self.client.get('/politica-de-privacidad/', HTTP_HOST=self.host)
        self.assertContains(response, 'Responsable del tratamiento')
        self.assertContains(response, 'Derechos del titular')
        self.assertContains(response, 'Análisis asistido')

        response = self.client.get('/centrales-de-informacion/', HTTP_HOST=self.host)
        self.assertContains(response, 'Momento de la consulta')
        self.assertContains(response, 'no ejecuta una consulta externa')

    def test_envio_sin_analisis_contractual_es_rechazado(self):
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud()
        payload.update({
            'origen_documento_identidad_frontal': 'capture',
            'origen_documento_identidad_reverso': 'capture',
            'documento_identidad_frontal': SimpleUploadedFile('frontal.jpg', b'imagen', content_type='image/jpeg'),
            'documento_identidad_reverso': SimpleUploadedFile('trasera.jpg', b'imagen', content_type='image/jpeg'),
            'certificado_bancario': SimpleUploadedFile('certificado.pdf', b'%PDF-1.4', content_type='application/pdf'),
            'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 sin analizar', content_type='application/pdf'),
        })

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes analizar el contrato antes de registrar la solicitud.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_cedula_en_pdf_no_es_valida(self):
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud_con_documentos()
        payload['documento_identidad_frontal'] = SimpleUploadedFile(
            'cedula.pdf', b'%PDF-1.4', content_type='application/pdf',
        )

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Captura una imagen válida de la cédula.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_fallback_manual_cedula_esta_bloqueado_por_defecto(self):
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud_con_documentos()
        payload['origen_documento_identidad_frontal'] = 'upload_fallback'

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La carga manual de cédula no está habilitada.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_usuario_actualiza_solicitud_y_reemplaza_documento_sin_duplicar(self):
        self.client.force_login(self.usuario)
        primera_respuesta = self.client.post(
            '/solicitar/',
            self._payload_solicitud_con_documentos(),
            HTTP_HOST=self.host,
        )
        solicitud = ContractorApplication.objects.get()
        contrato = solicitud.documentos.get(
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
        )
        contrato_id = contrato.id

        payload = self._payload_solicitud_con_documentos()
        payload['solicitud_id'] = str(solicitud.id)
        payload['cargo'] = 'Consultora senior'
        payload['contrato_actual'] = SimpleUploadedFile(
            'contrato-reemplazo.pdf',
            b'%PDF-1.4 contrato reemplazado',
            content_type='application/pdf',
        )
        self._analizar_contrato_transitorio(b'%PDF-1.4 contrato reemplazado')
        segunda_respuesta = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        solicitud.refresh_from_db()
        contrato.refresh_from_db()
        self.assertEqual(primera_respuesta.status_code, 302)
        self.assertEqual(segunda_respuesta.status_code, 302)
        self.assertEqual(ContractorApplication.objects.count(), 1)
        self.assertEqual(solicitud.cargo, 'Consultora senior')
        self.assertEqual(solicitud.documentos.count(), 4)
        self.assertEqual(contrato.id, contrato_id)
        with contrato.archivo.open('rb') as archivo:
            self.assertIn(b'contrato reemplazado', archivo.read())

    def test_contrato_cambiado_despues_del_analisis_exige_reanalizar(self):
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud_con_documentos()
        payload['contrato_actual'] = SimpleUploadedFile(
            'contrato-cambiado.pdf',
            b'%PDF-1.4 contrato diferente',
            content_type='application/pdf',
        )

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El contrato cambió después del análisis')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_usuario_no_puede_actualizar_solicitud_ajena(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.otro_usuario)
        payload = self._payload_solicitud_con_documentos()
        payload['solicitud_id'] = str(solicitud.id)

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 404)

    def test_empresa_debe_ser_empresa_existente_activa(self):
        empresa_inactiva = Empresa.objects.create(
            nombre='Empresa sin convenio',
            convenio_activo=False,
        )
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud()
        payload['empresa'] = empresa_inactiva.id

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes elegir una empresa valida de la lista.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_confirmacion_compacta_empresa_conserva_select_real(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertContains(response, 'id="id_empresa"')
        self.assertContains(response, 'id="empresa_selected_card"')
        self.assertContains(response, "selectEmpresa.value = id")
        self.assertContains(response, "check.className = 'company-selected-check'")
        self.assertContains(response, "validacion.textContent = 'Empresa validada correctamente'")

    def test_usuario_ve_solo_sus_solicitudes_en_mi_credito(self):
        propia = self._crear_solicitud(self.usuario)
        self._crear_solicitud(self.otro_usuario, empresa=self.otra_empresa, documento='987654321')
        self.client.force_login(self.usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="cp-page-header"')
        self.assertContains(response, 'class="cp-dashboard-hero"')
        self.assertContains(response, f'<strong>#{propia.id}</strong>')
        self.assertContains(response, 'Consulta el avance, tus condiciones guardadas y las acciones disponibles.')
        self.assertNotContains(response, '<table')
        self.assertContains(response, propia.empresa.nombre)
        self.assertNotContains(response, self.otra_empresa.nombre)

    def test_mi_credito_destaca_ultima_solicitud_y_muestra_historial_en_tarjetas(self):
        anterior = self._crear_solicitud(self.usuario, documento='111111111')
        principal = self._crear_solicitud(self.usuario, documento='222222222')
        self.client.force_login(self.usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['solicitud_principal'][0], principal)
        self.assertContains(response, f'<strong>#{principal.id}</strong>')
        self.assertContains(response, 'Solicitudes anteriores')
        self.assertContains(response, f'<strong>#{anterior.id}</strong>')
        self.assertContains(response, 'class="cp-history-list"')
        self.assertContains(response, 'class="cp-history-row"')
        self.assertNotContains(response, 'class="info-card-item"')

    def test_nueva_solicitud_esta_en_encabezado_y_acciones_tienen_jerarquia(self):
        solicitud = self._crear_solicitud(self.usuario, monto='3000000', plazo=12)
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        contenido = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'class="cp-button cp-button--secondary cp-new-request"',
            count=1,
        )
        self.assertNotContains(response, 'Ver estado')
        self.assertContains(response, 'Continuar simulación')
        self.assertContains(response, 'class="cp-quick-actions"')
        self.assertNotContains(response, 'href="#estado-solicitud-title"')
        self.assertLess(
            contenido.index('class="cp-page-header"'),
            contenido.index('class="cp-dashboard-hero"'),
        )

    def test_usuario_no_puede_ver_documentos_de_solicitud_ajena(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.otro_usuario)

        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)

    def test_carga_documento_queda_asociada_a_solicitud(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        archivo = SimpleUploadedFile(
            'contrato.pdf',
            b'%PDF-1.4 contrato',
            content_type='application/pdf',
        )
        response = self.client.post(
            f'/solicitud/{solicitud.id}/documentos/',
            {
                'tipo_documento': ContractorApplicationDocument.TipoDocumento.CONTRATO,
                'archivo': archivo,
            },
            HTTP_HOST=self.host,
        )

        documento = ContractorApplicationDocument.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(documento.solicitud, solicitud)
        self.assertEqual(documento.uploaded_by, self.usuario)

    def test_reemplazo_de_documento_funciona(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.metadata_analisis_contractual = {'estado': 'COMPLETADO'}
        solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        solicitud.save()
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato-reemplazo.pdf')

        solicitud.refresh_from_db()
        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)
        documento = ContractorApplicationDocument.objects.get()
        self.assertIn('CONTRATO', documento.archivo.name)
        self.assertEqual(documento.uploaded_by, self.usuario)
        self.assertEqual(
            solicitud.estado_analisis_contractual,
            ContractorApplication.EstadoAnalisisContractual.NO_SOLICITADO,
        )
        self.assertEqual(solicitud.metadata_analisis_contractual, {})
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)

    def test_estado_cambia_a_documentos_cargados_con_todos_los_obligatorios(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL, 'frontal.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA, 'trasera.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO, 'certificado.pdf')

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.DOCUMENTOS_CARGADOS)

    def test_link_seguro_descarga_documento_solo_para_dueno(self):
        solicitud = self._crear_solicitud(self.usuario)
        documento = ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 contrato', content_type='application/pdf'),
            uploaded_by=self.usuario,
        )
        url = f'/solicitud/{solicitud.id}/documentos/{documento.id}/descargar/'

        self.client.force_login(self.usuario)
        response = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertIn('contrato-vigente.pdf', response['Content-Disposition'])
        self.assertNotIn('contrato.pdf', response['Content-Disposition'])

        self.client.force_login(self.otro_usuario)
        response = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 404)

    def test_simulador_redirige_si_no_hay_solicitud(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/simular/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_simulador_sin_solicitud_muestra_toast_temporal_una_sola_vez(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/simular/', HTTP_HOST=self.host, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Primero registra tu solicitud para habilitar la simulación.')
        self.assertContains(response, 'data-contractor-toast')
        self.assertContains(response, 'data-close-toast')
        self.assertContains(response, 'window.setTimeout(cerrar, 5000)')

        response_recargada = self.client.get('/solicitar/', HTTP_HOST=self.host)
        self.assertNotContains(
            response_recargada,
            'Primero registra tu solicitud para habilitar la simulación.',
        )

    def test_simulador_muestra_advertencia_si_faltan_documentos(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes completar la carga documental antes de simular.')
        self.assertNotContains(response, 'Información pendiente')

    def test_simulador_solo_permite_ver_solicitud_propia(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.otro_usuario)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 404)

    def test_propietario_ve_documentos_con_navegacion_y_pendientes_diferenciados(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ruta de navegación')
        self.assertContains(response, 'Completa tus documentos')
        self.assertContains(response, 'data-state="PENDIENTE"', count=4)
        self.assertContains(response, 'Pendiente')
        self.assertContains(response, 'Continuar solicitud')
        self.assertContains(response, 'Volver a Mi crédito')
        self.assertContains(response, 'data-target="document-file-', count=4)

    def test_documento_cargado_muestra_estado_fecha_y_no_nombre_interno(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)
        self._cargar_documento(
            solicitud,
            ContractorApplicationDocument.TipoDocumento.CONTRATO,
            'nombre-interno-no-mostrar.pdf',
        )

        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-state="CARGADO"')
        self.assertContains(response, 'Cargado')
        self.assertContains(response, 'Cargado el')
        self.assertContains(response, 'Consultar')
        self.assertContains(response, 'Reemplazar')
        self.assertNotContains(response, 'nombre-interno-no-mostrar.pdf')

    def test_documento_reemplazado_muestra_estado_publico(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)
        self._cargar_documento(
            solicitud,
            ContractorApplicationDocument.TipoDocumento.CONTRATO,
            'contrato-inicial.pdf',
        )
        documento = solicitud.documentos.get(
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
        )
        ContractorApplicationDocument.objects.filter(pk=documento.pk).update(
            created_at=timezone.now() - timedelta(minutes=2),
        )

        self._cargar_documento(
            solicitud,
            ContractorApplicationDocument.TipoDocumento.CONTRATO,
            'contrato-reemplazo.pdf',
        )
        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'data-state="REEMPLAZADO"')
        self.assertContains(response, 'Reemplazado')
        self.assertNotContains(response, 'contrato-reemplazo.pdf')

    def test_vista_documento_es_contextual_privada_y_no_expone_nombre_fisico(self):
        solicitud = self._crear_solicitud(self.usuario)
        documento = ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile(
                'nombre-fisico-privado.pdf',
                b'%PDF-1.4 contrato',
                content_type='application/pdf',
            ),
            uploaded_by=self.usuario,
        )
        url = f'/solicitud/{solicitud.id}/documentos/{documento.id}/'

        self.client.force_login(self.usuario)
        response = self.client.get(url, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Documento protegido')
        self.assertContains(response, 'Contrato vigente')
        self.assertContains(response, 'Abrir documento')
        self.assertNotContains(response, 'nombre-fisico-privado.pdf')
        self.assertNotContains(response, documento.archivo.name)

        self.client.force_login(self.otro_usuario)
        response_ajena = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response_ajena.status_code, 404)

    def test_documento_con_subsanacion_muestra_correccion_sin_detalle_interno(self):
        solicitud = self._crear_solicitud(self.usuario)
        revision = RevisionManualPrestador.objects.create(
            solicitud=solicitud,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        RequerimientoSubsanacionPrestador.objects.create(
            solicitud=solicitud,
            revision=revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_CONTRACTUAL,
            mensaje_publico='Carga una versión legible del contrato.',
            detalle_interno='Regla interna que no debe mostrarse.',
        )
        self.client.force_login(self.usuario)

        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'Requiere corrección')
        self.assertContains(response, 'Carga una versión legible del contrato.')
        self.assertNotContains(response, 'Regla interna que no debe mostrarse.')

    def test_cta_y_estado_publico_no_exponen_enum_tecnico(self):
        solicitud = self._crear_solicitud(self.usuario)
        solicitud.estado = ContractorApplication.Estado.EN_EVALUACION
        solicitud.save(update_fields=['estado', 'updated_at'])
        self.client.force_login(self.usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'En evaluación')
        self.assertContains(response, 'Ver detalle de la solicitud')
        self.assertContains(response, 'Actualizado el')
        self.assertContains(response, 'Resumen de los pasos de tu solicitud.')
        self.assertNotContains(response, 'EN_EVALUACION')
        self.assertNotContains(response, 'PREAPROBADO_READ_ONLY')
        self.assertNotContains(response, 'href="#estado-solicitud-title"')

    def test_condiciones_guardadas_se_consultan_sin_abrir_simulador_vacio(self):
        solicitud = self._crear_solicitud(self.usuario)
        solicitud.monto_solicitado = Decimal('3500000')
        solicitud.plazo_meses = 6
        solicitud.monto_simulado = Decimal('3500000')
        solicitud.plazo_simulado_meses = 6
        solicitud.tasa_mensual_simulacion = Decimal('2.2000')
        solicitud.version_configuracion_financiera_simulacion = 'portal-tests-v1'
        solicitud.version_politica_simulacion = 'score-tests-v1'
        solicitud.monto_maximo_configuracion_simulacion = Decimal('10000000')
        solicitud.plazo_maximo_configuracion_simulacion = 24
        solicitud.simulada_en = timezone.now()
        solicitud.save()
        self.client.force_login(self.usuario)

        dashboard = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        condiciones = self.client.get(
            f'/mi-credito/solicitud/{solicitud.id}/condiciones/',
            HTTP_HOST=self.host,
        )

        self.assertContains(dashboard, 'Ver condiciones solicitadas')
        self.assertContains(
            dashboard,
            f'/mi-credito/solicitud/{solicitud.id}/condiciones/',
        )
        self.assertEqual(condiciones.status_code, 200)
        self.assertContains(condiciones, 'Condiciones solicitadas')
        self.assertContains(condiciones, '$3.500.000')
        self.assertContains(condiciones, '6 meses')
        self.assertContains(condiciones, '2,20%')
        self.assertContains(condiciones, 'portal-tests-v1')
        self.assertContains(condiciones, 'Detalle histórico no disponible')
        self.assertNotContains(condiciones, 'simulador-range-1')

    def test_condiciones_sin_snapshot_muestran_estado_controlado_y_respetan_owner(self):
        solicitud = self._crear_solicitud(self.usuario)
        url = f'/mi-credito/solicitud/{solicitud.id}/condiciones/'

        self.client.force_login(self.usuario)
        response = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aún no hay condiciones guardadas')
        self.assertContains(response, f'/simular/?solicitud_id={solicitud.id}')

        self.client.force_login(self.otro_usuario)
        response_ajena = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response_ajena.status_code, 404)

    def test_navbar_no_expone_simulador_sin_solicitud(self):
        self.client.force_login(self.usuario)
        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<a href="/simular/" class="nav-link"')
        self.assertContains(response, 'aria-current="page"')

    def test_simulador_exige_login(self):
        solicitud = self._crear_solicitud(self.usuario)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])

    def test_simulador_rechaza_solicitud_id_invalido(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/simular/?solicitud_id=no-valido', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 404)

    def test_usuario_autenticado_puede_analizar_su_contrato_con_fallback(self):
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)
        creditos_antes = Credito.objects.count()
        libranzas_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertTrue(data['manual_allowed'])
        self.assertIn(data['estado'], {'NO_DISPONIBLE', 'CON_ADVERTENCIAS'})
        solicitud.refresh_from_db()
        self.assertIsNotNone(solicitud.fecha_analisis_contractual)
        self.assertNotIn('texto_completo', solicitud.metadata_analisis_contractual)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas_antes)

    def test_usuario_puede_analizar_pdf_transitorio_sin_crear_solicitud(self):
        self.client.force_login(self.usuario)
        solicitudes_antes = ContractorApplication.objects.count()
        creditos_antes = Credito.objects.count()
        libranzas_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            '/contrato/analizar/',
            {
                'numero_documento': '123456789',
                'autoriza_analisis_contractual_asistido': '1',
                'contrato_actual': SimpleUploadedFile(
                    'contrato.pdf',
                    b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF',
                    content_type='application/pdf',
                ),
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('estado', response.json())
        self.assertIn('manual_allowed', response.json())
        self.assertEqual(ContractorApplication.objects.count(), solicitudes_antes)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas_antes)

    def test_analisis_transitorio_rechaza_sin_autorizacion(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            '/contrato/analizar/',
            {
                'numero_documento': '123456789',
                'contrato_actual': SimpleUploadedFile(
                    'contrato.pdf',
                    b'%PDF-1.4 contenido',
                    content_type='application/pdf',
                ),
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Debes autorizar', response.json()['error'])

    def test_analisis_transitorio_rechaza_sin_pdf(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            '/contrato/analizar/',
            {
                'numero_documento': '123456789',
                'autoriza_analisis_contractual_asistido': '1',
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Carga el contrato vigente en PDF.')

    def test_wizard_expone_camara_autorizacion_y_endpoint_transitorio(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-analysis-url="/contrato/analizar/"')
        self.assertContains(response, 'id="analizar_contrato_button"')
        self.assertContains(response, 'Analizar contrato')
        self.assertContains(response, 'id="id_autoriza_analisis_contractual_asistido"')
        self.assertContains(
            response,
            'Autorizo a Aprobado a analizar el contrato cargado para extraer información necesaria de mi solicitud.',
        )
        self.assertContains(response, 'capture="environment"', count=2)
        self.assertContains(response, 'navigator.mediaDevices.getUserMedia')
        self.assertContains(response, 'id="camera_modal"')
        self.assertContains(response, '>Tomar foto</button>', count=2)
        self.assertContains(response, 'id="camera_preview"', count=1)
        self.assertContains(response, 'id="camera_canvas" hidden', count=1)
        self.assertContains(response, 'id="camera_photo_preview"', count=1)
        self.assertContains(response, 'id="capture_photo_button"')
        self.assertContains(response, '>Repetir foto</button>')
        self.assertContains(response, '>Usar esta foto</button>')
        self.assertContains(response, 'class="identity-document-rows"')
        self.assertNotContains(response, 'class="document-grid"')
        self.assertContains(response, '>Seleccionar archivo</button>', count=2)
        self.assertContains(response, 'Sin cargar', count=3)
        self.assertContains(response, 'Ningún archivo cargado todavía')
        self.assertContains(response, 'document-compact-status')
        self.assertContains(response, 'contract-upload-status')
        self.assertContains(response, 'id="contract_analysis_spinner"')
        self.assertContains(response, 'Analizando...')
        self.assertContains(response, 'Estamos revisando el contrato. Esto puede tardar unos segundos.')
        self.assertContains(response, 'Contrato cargado correctamente')
        self.assertContains(response, 'contract-analysis-summary')
        self.assertContains(response, 'contract-analysis-warnings')
        self.assertContains(response, 'contract-analysis-blocks')
        self.assertContains(response, 'contract-analysis-data-list')
        self.assertContains(response, 'Datos detectados desde el contrato. Revísalos antes de continuar.')
        self.assertNotContains(response, 'No determinada')
        self.assertNotContains(response, 'document-file-status contract-analysis-result')
        self.assertNotContains(response, 'Archivo seleccionado:')
        self.assertNotContains(response, 'Copiar datos sugeridos')
        self.assertNotContains(response, 'Tomar foto o cargar')
        self.assertNotContains(response, 'analizar temporalmente')
        self.assertNotContains(response, 'Carga manual')

    def test_usuario_no_autenticado_no_puede_analizar_contrato(self):
        solicitud = self._preparar_solicitud_analisis(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])

    def test_usuario_no_puede_analizar_contrato_de_otro_usuario(self):
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.otro_usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)

    def test_analisis_sin_contrato_devuelve_error_controlado(self):
        solicitud = self._crear_solicitud(self.usuario)
        solicitud.autoriza_analisis_contractual_asistido = True
        solicitud.save(update_fields=['autoriza_analisis_contractual_asistido'])
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Carga el contrato vigente en PDF.')

    def test_analisis_sin_autorizacion_devuelve_error_controlado(self):
        solicitud = self._preparar_solicitud_analisis(self.usuario, autorizado=False)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Debes autorizar', response.json()['error'])

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='clave-no-utilizada')
    @patch('openai.OpenAI')
    def test_ia_deshabilitada_no_crea_cliente_openai(self, cliente_openai):
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        cliente_openai.assert_not_called()

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='')
    @patch('openai.OpenAI')
    def test_sin_api_key_no_crea_cliente_openai(self, cliente_openai):
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        cliente_openai.assert_not_called()

    @override_settings(
        CONTRACTORS_CONTRACT_AI_ENABLED=True,
        CONTRACTORS_CONTRACT_AI_MODEL='gpt-4.1-mini',
        OPENAI_API_KEY='clave-de-prueba',
        DEBUG=True,
    )
    @patch('openai.OpenAI')
    def test_ia_habilitada_usa_openai_y_expone_diagnostico_seguro(self, cliente_openai):
        cliente_openai.return_value.responses.create.return_value.output_text = (
            '{"documento_contratista":"123456789","confianza_general":"0.91",'
            '"tipo_contrato":"Contrato de prestación de servicios","advertencias":[]}'
        )
        self.client.force_login(self.usuario)

        response = self.client.post(
            '/contrato/analizar/',
            {
                'numero_documento': '123456789',
                'autoriza_analisis_contractual_asistido': '1',
                'contrato_actual': SimpleUploadedFile(
                    'contrato.pdf',
                    b'%PDF-1.4 contenido de prueba',
                    content_type='application/pdf',
                ),
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        cliente_openai.assert_called_once_with(api_key='clave-de-prueba')
        self.assertEqual(data['fuente'], 'openai')
        self.assertEqual(data['diagnostico']['engine'], 'openai')
        self.assertTrue(data['diagnostico']['ai_enabled'])
        self.assertTrue(data['diagnostico']['has_openai_key'])
        self.assertNotIn('clave-de-prueba', str(data))
        self.assertNotIn('contenido de prueba', str(data))
        self.assertEqual(data['datos']['tipo_contrato'], 'PRESTACION_SERVICIOS')

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='clave-de-prueba')
    @patch('openai.OpenAI')
    def test_fallo_openai_no_cierra_ni_consume_archivo(self, cliente_openai):
        cliente_openai.return_value.responses.create.side_effect = RuntimeError('detalle sensible')
        archivo = SimpleUploadedFile(
            'contrato.pdf',
            b'%PDF-1.4 contenido reutilizable',
            content_type='application/pdf',
        )

        resultado = analizar_contrato_con_openai(archivo)

        self.assertFalse(resultado.disponible)
        self.assertEqual(resultado.diagnostico['openai_error_type'], 'RuntimeError')
        self.assertFalse(archivo.closed)
        archivo.seek(0)
        self.assertEqual(archivo.read(), b'%PDF-1.4 contenido reutilizable')
        self.assertNotIn('detalle sensible', str(resultado.diagnostico))

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_documento_detectado_diferente_bloquea_sin_sobrescribir(self, analizar_mock):
        analizar_mock.return_value = ResultadoAnalisisContrato(
            documento_contratista='987654321',
            cargo_o_servicio='Actividad sugerida',
            confianza_general=Decimal('0.91'),
            fuente='openai',
        )
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        cargo_original = solicitud.cargo
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        data = response.json()
        solicitud.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data['success'])
        self.assertIn(MENSAJE_DOCUMENTO_DIFERENTE, data['bloqueos'])
        self.assertEqual(solicitud.cargo, cargo_original)
        self.assertNotIn('987654321', str(solicitud.metadata_analisis_contractual))

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_empresa_detectada_por_nit_devuelve_sugerencia_exacta(self, analizar_mock):
        self.empresa.nit = '900123456-7'
        self.empresa.save(update_fields=['nit'])
        Empresa.objects.create(
            nombre='Empresa detectada por nombre',
            nit='800000000-1',
            convenio_activo=True,
        )
        analizar_mock.return_value = ResultadoAnalisisContrato(
            documento_contratista='123456789',
            empresa_contratante='Empresa detectada por nombre',
            nit_empresa='900123456-7',
            confianza_general=Decimal('0.90'),
            fuente='openai',
        )
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        sugerencia = response.json()['empresa_sugerida']
        self.assertEqual(sugerencia['empresa_sugerida_id'], self.empresa.id)
        self.assertEqual(sugerencia['tipo_coincidencia'], 'NIT_EXACTO')
        self.assertEqual(sugerencia['match_tipo'], 'nit_exacto')
        self.assertEqual(ContractorApplication.objects.get(pk=solicitud.pk).empresa, self.empresa)

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_empresa_detectada_admite_match_aproximado_unico(self, analizar_mock):
        empresa_aproximada = Empresa.objects.create(
            nombre='TECNOLOGIA VANGUARDIA SAS',
            nit='901111111-1',
            convenio_activo=True,
        )
        empresas_antes = Empresa.objects.count()
        analizar_mock.return_value = ResultadoAnalisisContrato(
            documento_contratista='123456789',
            empresa_contratante='TECNOLOGÍAS VANGUARDISTAS S.A.S.',
            confianza_general=Decimal('0.83'),
            fuente='openai',
        )
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        sugerencia = response.json()['empresa_sugerida']
        self.assertEqual(sugerencia['empresa_sugerida_id'], empresa_aproximada.id)
        self.assertEqual(sugerencia['match_tipo'], 'aproximado')
        self.assertGreaterEqual(sugerencia['match_score'], 0.82)
        self.assertIn('Revisa y confirma', ' '.join(response.json()['advertencias']))
        self.assertNotIn('no coincide con una empresa activa', ' '.join(response.json()['advertencias']))
        self.assertEqual(Empresa.objects.count(), empresas_antes)

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_match_aproximado_ambiguo_no_selecciona_empresa(self, analizar_mock):
        Empresa.objects.create(nombre='Tecnologia Vanguardia Norte SAS', convenio_activo=True)
        Empresa.objects.create(nombre='Tecnologia Vanguardia Sur SAS', convenio_activo=True)
        analizar_mock.return_value = ResultadoAnalisisContrato(
            documento_contratista='123456789',
            empresa_contratante='Tecnologías Vanguardistas SAS',
            fuente='openai',
        )
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        sugerencia = response.json()['empresa_sugerida']
        self.assertIsNone(sugerencia['empresa_sugerida_id'])
        self.assertEqual(sugerencia['match_tipo'], 'ambiguo')
        self.assertIn('varias empresas similares', ' '.join(response.json()['advertencias']))

    @patch('contractors.services.analisis_contractual_seguro.analizar_contrato_con_openai')
    def test_empresa_sin_match_mantiene_advertencia(self, analizar_mock):
        analizar_mock.return_value = ResultadoAnalisisContrato(
            documento_contratista='123456789',
            empresa_contratante='Organizacion completamente distinta',
            fuente='openai',
        )
        solicitud = self._preparar_solicitud_analisis(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/solicitud/{solicitud.id}/contrato/analizar/',
            HTTP_HOST=self.host,
        )

        sugerencia = response.json()['empresa_sugerida']
        self.assertIsNone(sugerencia['empresa_sugerida_id'])
        self.assertEqual(sugerencia['match_tipo'], 'sin_match')
        self.assertIn('no coincide con una empresa activa', ' '.join(response.json()['advertencias']))

    def test_staff_ve_resumen_seguro_del_analisis_contractual(self):
        solicitud = self._crear_solicitud(self.usuario)
        solicitud.estado_analisis_contractual = 'CON_ADVERTENCIAS'
        solicitud.fecha_analisis_contractual = timezone.now()
        solicitud.metadata_analisis_contractual = {
            'confianza_general': '0.82',
            'empresa_sugerida': {
                'nombre': 'Empresa Convenio SAS',
                'tipo_coincidencia': 'NOMBRE_EXACTO',
            },
            'datos_sugeridos': {
                'cargo_o_servicio': 'Consultoría técnica',
                'fecha_fin_contrato': '2027-01-31',
                'valor_total_contrato': '12000000.00',
            },
            'advertencias': ['Confirma la fecha de finalización.'],
            'bloqueos': [],
        }
        solicitud.save(update_fields=[
            'estado_analisis_contractual',
            'fecha_analisis_contractual',
            'metadata_analisis_contractual',
        ])
        self.client.force_login(self.staff)

        response = self.client.get(
            f'/gestion/prestadores/{solicitud.id}/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Análisis contractual asistido')
        self.assertContains(response, 'Con advertencias')
        self.assertContains(response, 'Empresa Convenio SAS')
        self.assertContains(response, 'Confirma la fecha de finalización.')
        self.assertNotContains(response, 'texto_completo')

    def test_simulador_calcula_cuota_preliminar_con_datos_validos(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()
        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Define las condiciones de tu solicitud')
        self.assertContains(response, '¿Cuánto necesitas?')
        self.assertContains(response, '¿En cuántos meses deseas pagarlo?')
        self.assertContains(response, 'Costo de originación')
        self.assertContains(response, 'IVA sobre costo de originación')
        self.assertContains(response, 'Intereses estimados')
        self.assertContains(response, 'Total a pagar')
        self.assertContains(response, 'Cuota mensual')
        self.assertContains(response, 'class="simulador-hero-section"')
        self.assertContains(response, 'class="simulador-card"')
        self.assertNotContains(response, 'Revisa tu capacidad contractual')
        self.assertNotContains(response, 'Información pendiente')
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def test_simulador_muestra_copy_cta_y_actualizacion_interactiva(self):
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Selecciona el monto y el plazo que mejor se ajusten al valor pendiente de tu contrato.',
        )
        self.assertContains(
            response,
            'La simulación es informativa. La aprobación estará sujeta al análisis documental, '
            'validación de identidad, evaluación de riesgo y consulta ante centrales de información.',
        )
        self.assertContains(response, 'form="simulator-form" data-register-application')
        self.assertContains(response, '>Guardar condiciones solicitadas</button>')
        self.assertContains(response, '<form method="post"', count=1)
        contenido = response.content.decode('utf-8')
        self.assertLess(
            contenido.index('data-register-application'),
            contenido.index('<div class="simulador-card">'),
        )
        self.assertContains(response, 'setTimeout(calculate, 150)')
        self.assertContains(response, 'new AbortController()')
        self.assertContains(response, "amount.addEventListener('input', schedule)")
        self.assertContains(response, "term.addEventListener('input', schedule)")
        self.assertContains(response, 'id="simulator-public-config"')
        self.assertContains(response, 'function previewCalculation()')
        self.assertContains(response, 'previewCalculation();')
        self.assertContains(response, 'paint(data.resultado)')
        self.assertNotContains(response, 'SECRET_KEY')
        self.assertNotContains(response, 'OPENAI_API_KEY')
        self.assertNotContains(response, 'access_token')
        self.assertContains(response, "registerButton.textContent = 'Registrando...'")

    def test_simulador_usa_configuracion_persistida_para_diez_millones(self):
        resultado = simular_credito_prestador_informativo(
            monto=Decimal('10000000'),
            plazo_meses=12,
            configuracion=self.configuracion_simulador,
        )

        self.assertEqual(resultado.costo_originacion, Decimal('1000000.00'))
        self.assertEqual(resultado.iva_costo_originacion, Decimal('190000.00'))
        self.assertEqual(resultado.fondo_garantia, Decimal('200000.00'))
        self.assertEqual(resultado.seguro_vida, Decimal('37110.00'))
        self.assertEqual(resultado.capital_total_financiado, Decimal('11427110.00'))

    def test_simulador_usa_configuracion_activa(self):
        configuracion = self.configuracion_simulador
        configuracion.porcentaje_originacion = Decimal('8')
        configuracion.porcentaje_iva_originacion = Decimal('19')
        configuracion.porcentaje_fondo_garantia = Decimal('1.5')
        configuracion.porcentaje_seguro_vida_primera_cuota = Decimal('0.25')
        configuracion.tasa_mensual = Decimal('2.1')
        configuracion.save()

        resultado = simular_credito_prestador_informativo(
            monto=Decimal('10000000'),
            plazo_meses=12,
            configuracion=configuracion,
        )

        self.assertEqual(resultado.costo_originacion, Decimal('800000.00'))
        self.assertEqual(resultado.iva_costo_originacion, Decimal('152000.00'))
        self.assertEqual(resultado.fondo_garantia, Decimal('150000.00'))
        self.assertEqual(resultado.seguro_vida, Decimal('25000.00'))
        self.assertEqual(resultado.tasa_mensual, Decimal('0.021'))

    def test_endpoint_simulador_usa_configuracion_activa(self):
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)
        self.configuracion_simulador.porcentaje_originacion = Decimal('8')
        self.configuracion_simulador.porcentaje_iva_originacion = Decimal('19')
        self.configuracion_simulador.porcentaje_fondo_garantia = Decimal('1.5')
        self.configuracion_simulador.porcentaje_seguro_vida_primera_cuota = Decimal('0.25')
        self.configuracion_simulador.save()
        response = self.client.post(
            '/simular/calcular/',
            data='{"solicitud_id": %s, "monto": "10000000", "plazo_meses": 12}' % solicitud.id,
            content_type='application/json',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200, response.content)
        resultado = response.json()['resultado']
        self.assertEqual(resultado['costo_originacion'], '800000.00')
        self.assertEqual(resultado['fondo_garantia'], '150000.00')
        self.assertEqual(resultado['seguro_vida'], '25000.00')

    def test_simulador_sin_configuracion_no_usa_fallback_ni_permite_calculo(self):
        ConfiguracionSimuladorPrestador.objects.all().delete()
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = (
            ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        )
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)
        solicitud.estado_analisis_contractual = (
            ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        )
        solicitud.save(update_fields=['estado_analisis_contractual'])
        with self.assertRaises(ConfiguracionSimuladorNoDisponible):
            simular_credito_prestador_informativo(
                monto=Decimal('3000000'),
                plazo_meses=8,
            )

        pagina = self.client.get(
            f'/simular/?solicitud_id={solicitud.id}',
            HTTP_HOST=self.host,
        )
        self.assertContains(pagina, 'temporalmente no disponible')
        self.assertNotContains(pagina, 'max="24"')

        calculo = self.client.post(
            '/simular/calcular/',
            data='{"solicitud_id": %s, "monto": "3000000", "plazo_meses": 8}' % solicitud.id,
            content_type='application/json',
            HTTP_HOST=self.host,
        )
        self.assertEqual(calculo.status_code, 503, calculo.content)

    def test_post_simulador_guarda_monto_plazo_sin_crear_credito(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto=None,
            plazo=None,
        )
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)
        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            f'/simular/?solicitud_id={solicitud.id}',
            {
                'solicitud_id': solicitud.id,
                'monto': '3500000',
                'plazo_meses': '12',
                'cuota_mensual': '1',
                'total_a_pagar': '1',
                'capital_total_financiado': '1',
            },
            HTTP_HOST=self.host,
        )

        self.assertRedirects(response, '/mi-credito/', fetch_redirect_response=False)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.monto_solicitado, Decimal('3500000.00'))
        self.assertEqual(solicitud.plazo_meses, 12)
        self.assertEqual(
            solicitud.estado,
            ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def test_post_simulador_muestra_modal_institucional_una_sola_vez(self):
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.post(
            f'/simular/?solicitud_id={solicitud.id}',
            {'solicitud_id': solicitud.id, 'monto': '3500000', 'plazo_meses': '12'},
            HTTP_HOST=self.host,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="modalSolicitudRegistrada"')
        self.assertContains(response, 'data-auto-open="true"')
        self.assertContains(response, 'Solicitud registrada correctamente')
        self.assertContains(response, 'La simulación no representa una aprobación definitiva.')
        self.assertContains(response, 'static/images/logo.png')

        response_recargada = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertEqual(response_recargada.status_code, 200)
        self.assertNotContains(response_recargada, 'id="modalSolicitudRegistrada"')

    def test_post_simulador_rechaza_rangos_invalidos(self):
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.post(
            f'/simular/?solicitud_id={solicitud.id}',
            {'solicitud_id': solicitud.id, 'monto': '999999999', 'plazo_meses': '99'},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El monto debe estar entre')
        self.assertContains(response, 'El plazo debe estar entre')
        solicitud.refresh_from_db()
        self.assertIsNone(solicitud.monto_solicitado)
        self.assertIsNone(solicitud.plazo_meses)

    def test_calculo_interactivo_exige_ownership_y_devuelve_costos(self):
        solicitud = self._crear_solicitud(self.usuario, monto=None, plazo=None)
        solicitud.estado_analisis_contractual = ContractorApplication.EstadoAnalisisContractual.COMPLETADO
        solicitud.save(update_fields=['estado_analisis_contractual'])
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.post(
            '/simular/calcular/',
            data='{"solicitud_id": %s, "monto": "3000000", "plazo_meses": 12}' % solicitud.id,
            content_type='application/json',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        resultado = response.json()['resultado']
        self.assertIn('costo_originacion', resultado)
        self.assertIn('iva_costo_originacion', resultado)
        self.assertIn('intereses_estimados', resultado)
        self.assertIn('total_a_pagar', resultado)
        self.assertIn('cuota_mensual', resultado)

        self.client.force_login(self.otro_usuario)
        response = self.client.post(
            '/simular/calcular/',
            data='{"solicitud_id": %s, "monto": "3000000", "plazo_meses": 12}' % solicitud.id,
            content_type='application/json',
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 404)

    def test_resultado_ia_publico_no_muestra_diagnostico_tecnico(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertNotContains(response, 'Motor:')
        self.assertNotContains(response, 'Confianza:')
        self.assertNotContains(response, 'Estado:</strong>')
        self.assertContains(response, 'Datos detectados desde el contrato. Revísalos antes de continuar.')

    def test_simulador_no_muestra_panel_tecnico_por_monto_mayor_al_pendiente(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='5000000',
            valor_pendiente='2000000',
            monto='3000000',
            plazo=12,
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Información pendiente')
        self.assertNotContains(response, 'Revisa tu capacidad contractual')

    def test_simulador_no_muestra_panel_tecnico_por_contrato_vencido(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='5000000',
            valor_pendiente='3000000',
            monto='2000000',
            plazo=10,
            fecha_fin=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Información pendiente')
        self.assertNotContains(response, 'Revisa tu capacidad contractual')

    def test_usuario_no_autenticado_no_accede_a_bandeja_staff(self):
        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])

    def test_usuario_autenticado_no_staff_no_accede_a_bandeja(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 403)

    def test_staff_accede_a_bandeja_y_ve_solicitud(self):
        solicitud = self._crear_solicitud(self.usuario)
        RevisionManualPrestador.objects.create(
            solicitud=solicitud,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        self.client.force_login(self.staff)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{solicitud.id}')
        self.assertContains(response, 'P.')
        self.assertContains(response, solicitud.empresa.nombre)
        self.assertContains(response, 'Ver evaluacion')

    def test_staff_accede_al_detalle(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.staff)

        response = self.client.get(f'/gestion/prestadores/{solicitud.id}/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Datos basicos sanitizados')
        self.assertContains(response, 'Capacidad contractual preliminar')
        self.assertContains(response, 'Ultima predecision auditada')
        self.assertContains(response, 'Revisiones manuales')
        self.assertNotContains(response, solicitud.numero_documento)

    def test_staff_puede_ver_documentos_cargados(self):
        solicitud = self._crear_solicitud(self.usuario)
        documento = ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 contrato', content_type='application/pdf'),
            uploaded_by=self.usuario,
        )
        self.client.force_login(self.staff)

        detalle = self.client.get(f'/gestion/prestadores/{solicitud.id}/', HTTP_HOST=self.host)
        descarga = self.client.get(
            f'/gestion/prestadores/documentos/{documento.id}/descargar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, 'Abrir documento')
        self.assertEqual(descarga.status_code, 200)

    def test_staff_puede_iniciar_revision_sin_crear_credito(self):
        solicitud = self._crear_solicitud(self.usuario)
        revision = RevisionManualPrestador.objects.create(
            solicitud=solicitud,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        self.client.force_login(self.staff)
        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            f'/gestion/prestadores/revisiones/{revision.id}/accion/',
            {'accion': 'INICIAR'},
            HTTP_HOST=self.host,
        )

        revision.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.EN_ANALISIS)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def test_usuario_normal_no_puede_operar_revision(self):
        solicitud = self._crear_solicitud(self.usuario)
        revision = RevisionManualPrestador.objects.create(
            solicitud=solicitud,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/gestion/prestadores/revisiones/{revision.id}/accion/',
            {'accion': 'INICIAR'},
            HTTP_HOST=self.host,
        )

        revision.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.ABIERTA)

    def test_predecision_no_evaluable_si_faltan_datos_criticos(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente=None,
            monto=None,
            plazo=None,
        )

        resultado = evaluar_predecision_prestador(solicitud, documentos_completos=True)

        self.assertEqual(resultado.resultado, RESULTADO_NO_EVALUABLE)
        self.assertIsNone(resultado.puntaje_informativo)
        self.assertTrue(resultado.datos_faltantes)

    def test_predecision_requiere_revision_si_documentos_incompletos(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )

        resultado = evaluar_predecision_prestador(solicitud, documentos_completos=False)

        self.assertEqual(resultado.resultado, RESULTADO_REQUIERE_REVISION)
        self.assertIn('Faltan documentos obligatorios para completar la evaluacion.', resultado.alertas)

    def test_predecision_requiere_revision_si_contrato_vencido(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
            fecha_fin=timezone.localdate() - timedelta(days=1),
        )

        resultado = evaluar_predecision_prestador(solicitud, documentos_completos=True)

        self.assertEqual(resultado.resultado, RESULTADO_REQUIERE_REVISION)
        self.assertIn('El contrato registrado se encuentra vencido.', resultado.alertas)

    def test_predecision_requiere_revision_si_monto_supera_valor_pendiente(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='2000000',
            monto='3000000',
            plazo=12,
        )

        resultado = evaluar_predecision_prestador(solicitud, documentos_completos=True)

        self.assertEqual(resultado.resultado, RESULTADO_REQUIERE_REVISION)
        self.assertIn('El monto solicitado supera el valor pendiente por cobrar del contrato.', resultado.alertas)

    def test_predecision_preaprobable_si_datos_y_documentos_completos(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )

        resultado = evaluar_predecision_prestador(solicitud, documentos_completos=True)

        self.assertEqual(resultado.resultado, RESULTADO_PREAPROBABLE)
        self.assertEqual(resultado.alertas, [])
        self.assertEqual(resultado.datos_faltantes, [])
        self.assertIsNotNone(resultado.puntaje_informativo)

    def test_detalle_staff_muestra_espacio_de_predecision_formal(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)
        self.client.force_login(self.staff)

        response = self.client.get(f'/gestion/prestadores/{solicitud.id}/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ultima predecision auditada')
        self.assertContains(response, 'No constituye aprobacion, originacion ni desembolso.')
        self.assertNotContains(response, 'Puntaje informativo')

    def test_predecision_no_crea_creditos_ni_cambia_estado(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )
        estado_inicial = solicitud.estado
        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()

        evaluar_predecision_prestador(solicitud, documentos_completos=True)

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, estado_inicial)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def _payload_solicitud(self):
        return {
            'escenario_credito': ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            'tipo_documento': ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            'numero_documento': '123456789',
            'nombres': 'Ana Maria',
            'apellidos': 'Perez Gomez',
            'celular': '3001234567',
            'correo': 'ana@example.com',
            'direccion': 'Calle 1 # 2-3',
            'cargo': 'Consultora',
            'tipo_contrato': ContractorApplication.TipoContrato.PRESTACION_SERVICIOS,
            'empresa': self.empresa.id,
            'fecha_inicio_contrato': '2026-01-01',
            'fecha_fin_contrato': '2026-12-31',
            'valor_total_contrato': '12000000',
            'valor_pagado_contrato': '4000000',
            'valor_pendiente_cobrar': '8000000',
            'observaciones_contrato': 'Contrato confirmado por el prestador.',
            'acepta_terminos': 'on',
            'acepta_politica_privacidad': 'on',
            'autoriza_analisis_contractual_asistido': 'on',
            'autoriza_consulta_centrales': 'on',
        }

    def _payload_solicitud_con_documentos(self):
        contenido_contrato = b'%PDF-1.4 contrato'
        self._analizar_contrato_transitorio(contenido_contrato)
        payload = self._payload_solicitud()
        payload.update({
            'origen_documento_identidad_frontal': 'capture',
            'origen_documento_identidad_reverso': 'capture',
            'documento_identidad_frontal': SimpleUploadedFile('cedula-frontal.jpg', b'imagen-frontal', content_type='image/jpeg'),
            'documento_identidad_reverso': SimpleUploadedFile('cedula-trasera.jpg', b'imagen-trasera', content_type='image/jpeg'),
            'certificado_bancario': SimpleUploadedFile('certificado.pdf', b'%PDF-1.4 certificado', content_type='application/pdf'),
            'contrato_actual': SimpleUploadedFile('contrato.pdf', contenido_contrato, content_type='application/pdf'),
        })
        return payload

    def _analizar_contrato_transitorio(self, contenido, numero_documento='123456789'):
        return self.client.post(
            '/contrato/analizar/',
            {
                'numero_documento': numero_documento,
                'autoriza_analisis_contractual_asistido': '1',
                'contrato_actual': SimpleUploadedFile(
                    'contrato.pdf',
                    contenido,
                    content_type='application/pdf',
                ),
            },
            HTTP_HOST=self.host,
        )

    def _crear_solicitud(
        self,
        usuario,
        empresa=None,
        documento='123456789',
        valor_total=None,
        valor_pendiente=None,
        monto='3000000',
        plazo=12,
        fecha_fin=None,
    ):
        return ContractorApplication.objects.create(
            usuario=usuario,
            empresa=empresa or self.empresa,
            escenario_credito=ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento=documento,
            nombres='Ana Maria',
            apellidos='Perez Gomez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1 # 2-3',
            cargo='Consultora',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=fecha_fin or (timezone.localdate() + timedelta(days=180)),
            valor_total_contrato=valor_total,
            valor_pendiente_cobrar=valor_pendiente,
            monto_solicitado=monto,
            plazo_meses=plazo,
        )

    def _cargar_documento(self, solicitud, tipo_documento, nombre, contenido=b'%PDF-1.4 documento'):
        return self.client.post(
            f'/solicitud/{solicitud.id}/documentos/',
            {
                'tipo_documento': tipo_documento,
                'archivo': SimpleUploadedFile(nombre, contenido),
            },
            HTTP_HOST=self.host,
        )

    def _cargar_documentos_obligatorios(self, solicitud):
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL, 'frontal.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA, 'trasera.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO, 'certificado.pdf')

    def _preparar_solicitud_analisis(self, usuario, *, autorizado=True):
        solicitud = self._crear_solicitud(self.usuario if usuario is None else usuario)
        solicitud.autoriza_analisis_contractual_asistido = autorizado
        solicitud.save(update_fields=['autoriza_analisis_contractual_asistido'])
        ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile(
                'contrato.pdf',
                b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF',
                content_type='application/pdf',
            ),
            uploaded_by=usuario,
        )
        return solicitud

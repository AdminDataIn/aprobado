from datetime import date
from decimal import Decimal
import hashlib
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ConfiguracionPortalContratistas,
    InformacionLaboralSolicitudContratista,
    TimelinePrestador,
)
from contractors.forms import (
    calcular_hash_analisis_contractual_desde_datos,
    MENSAJE_ANALISIS_CONTRACTUAL_OBSOLETO,
)
from contractors.services.analisis_contrato_ia import ResultadoAnalisisContratoIAOpenAI
from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CreditoReglaEspecialAudit,
    Empresa,
    HistorialEstado,
    HistorialPago,
    Pagare,
)


@override_settings(
    PRIMARY_DOMAIN_HOST='aprobado.com.co',
    CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
    ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    CONTRACTORS_CONTRACT_AI_ENABLED=False,
)
class SolicitudContratistaViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='contratistas',
        )
        self.otra_organizacion = ContractorOrganization.objects.create(
            name='Beta Contractors',
            slug='beta',
            subdomain='beta',
        )
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Acme Credito',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            color_primario='#112233',
            color_secundario='#445566',
            texto_landing='Credito para contratistas Acme.',
            monto_minimo=Decimal('100000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.empresa_core = Empresa.objects.create(
            nombre='Empresa Convenio Contratistas',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            nit='900123456',
            correo_contacto='pagador@example.com',
            telefono_contacto='6011234567',
        )
        self.empresa_no_elegible = Empresa.objects.create(
            nombre='Empresa Sin Convenio',
            convenio_activo=False,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Credito',
            primary_color='#112233',
            secondary_color='#445566',
            landing_copy='Credito para contratistas Acme.',
        )
        ContractorBranding.objects.create(
            organization=self.otra_organizacion,
            display_name='Beta Credito',
            primary_color='#778899',
            secondary_color='#aabbcc',
            landing_copy='Credito para contratistas Beta.',
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('5000000.00'),
            min_term_months=3,
            max_term_months=24,
            monthly_rate=Decimal('2.5000'),
            commission_rate=Decimal('5.0000'),
            commission_amount=Decimal('100000.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.otra_configuracion = ContractorProductConfig.objects.create(
            organization=self.otra_organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('50000.00'),
            max_amount=Decimal('2000000.00'),
            min_term_months=1,
            max_term_months=12,
            monthly_rate=Decimal('7.0000'),
            commission_rate=Decimal('1.0000'),
            commission_amount=Decimal('0.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.usuario = get_user_model().objects.create_user(
            username='contratista-test',
            email='contratista@example.com',
            password='password-test',
        )
        self.client.force_login(self.usuario)

    def _payload(self, **overrides):
        datos = {
            'escenario_credito': ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            'monto': '1000000.00',
            'plazo_meses': '12',
            'tipo_documento': 'CC',
            'numero_documento': '1020304050',
            'nombres': 'Ana',
            'apellidos': 'Perez',
            'celular': '3001234567',
            'correo': 'ana@example.com',
            'direccion': 'Calle 1 # 2-3',
            'cargo': 'Consultora comercial',
            'empresa': str(self.empresa_core.id),
            'empresa_busqueda': self.empresa_core.nombre,
            'tipo_contrato': InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            'fecha_inicio_contrato': '2026-01-01',
            'fecha_fin_contrato': '2026-12-31',
            'valor_total_contrato': '12000000.00',
            'valor_pagado_contrato': '4000000.00',
            'valor_pendiente_cobrar': '8000000.00',
            'observaciones': 'Contrato vigente.',
            'terminos_aceptados': 'on',
            'tratamiento_datos_analisis_ia': 'on',
            'documento_identidad_frontal_capturado': '1',
            'documento_identidad_reverso_capturado': '1',
            'documento_identidad_frontal': SimpleUploadedFile(
                'cedula-frontal.jpg',
                b'frontal',
                content_type='image/jpeg',
            ),
            'documento_identidad_reverso': SimpleUploadedFile(
                'cedula-reverso.jpg',
                b'reverso',
                content_type='image/jpeg',
            ),
            'contrato_actual': SimpleUploadedFile(
                'contrato.pdf',
                b'%PDF-contrato',
                content_type='application/pdf',
            ),
            'certificado_bancario': SimpleUploadedFile(
                'certificado.pdf',
                b'%PDF-certificado',
                content_type='application/pdf',
            ),
        }
        datos.update(overrides)
        if 'analisis_contractual_metadata' not in overrides:
            datos['analisis_contractual_metadata'] = json.dumps(
                self._metadata_analisis_vigente(
                    datos,
                    contenido_contrato=self._contenido_archivo_payload(datos.get('contrato_actual')),
                )
            )
        return datos

    def _contenido_archivo_payload(self, archivo):
        if not archivo:
            return b''
        posicion = None
        if hasattr(archivo, 'tell') and hasattr(archivo, 'seek'):
            posicion = archivo.tell()
            archivo.seek(0)
        contenido = archivo.read()
        if posicion is not None:
            archivo.seek(posicion)
        return contenido

    def _hash_analisis_para_payload(self, datos, contenido_contrato=b'%PDF-contrato'):
        return calcular_hash_analisis_contractual_desde_datos(
            datos,
            archivo_hash=hashlib.sha256(contenido_contrato).hexdigest(),
        )

    def _metadata_analisis_vigente(self, datos, *, bloqueos=None, contenido_contrato=b'%PDF-contrato'):
        return {
            'metadata': {},
            'advertencias': [],
            'bloqueos': bloqueos or [],
            'eventos': [],
            'sugerencia_empresa': {},
            'requiere_revision_manual': False,
            'analysis_input_hash': self._hash_analisis_para_payload(datos, contenido_contrato=contenido_contrato),
            'analysis_generated_at': '2026-06-21T10:00:00-05:00',
        }

    def test_get_muestra_formulario_en_subdominio_valido(self):
        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acme Credito')
        self.assertContains(response, 'Solicita tu Crédito para Prestadores de Servicios')
        self.assertNotContains(response, 'Crédito contratista')
        self.assertNotContains(response, 'Crédito para contratistas')
        self.assertNotContains(response, 'Monto solicitado')
        self.assertContains(response, 'Confirma la información de tu contrato')
        self.assertContains(response, 'Información contractual / Confirmación')
        self.assertContains(response, 'Documentos obligatorios')
        self.assertContains(response, 'Cédula frontal')
        self.assertContains(response, 'Certificado bancario PDF')
        self.assertContains(response, 'Contrato vigente PDF')
        self.assertLess(
            response.content.decode().index('Certificado bancario PDF'),
            response.content.decode().index('Contrato vigente PDF'),
        )
        self.assertContains(response, 'open-camera-btn')
        self.assertContains(response, 'data-target="id_documento_identidad_frontal"')
        self.assertContains(response, 'data-target="id_documento_identidad_reverso"')
        self.assertContains(response, 'camera_modal')
        self.assertContains(response, 'navigator.mediaDevices.getUserMedia')
        self.assertContains(response, 'Este documento debe capturarse en vivo desde la camara')
        self.assertNotContains(response, 'Cargar imagen')
        self.assertContains(response, 'No se ha capturado documento.')
        self.assertContains(response, 'No se ha cargado certificado.')
        self.assertContains(response, 'No se ha cargado contrato.')
        self.assertContains(response, 'Autorización para análisis asistido')
        self.assertContains(response, 'Debes elegir una empresa de la lista de resultados.')
        self.assertContains(response, 'company-selected-card')
        self.assertNotContains(response, 'Nombre del pagador')
        self.assertNotContains(response, 'Correo del pagador')
        self.assertContains(response, 'window.validarPasoContratista')
        self.assertContains(response, 'validarPasoActual')
        self.assertContains(response, 'Preparando documento...')
        self.assertContains(response, 'Analizando contrato...')
        self.assertContains(response, 'Validando información...')
        self.assertContains(response, 'window.analisisContratoEnProceso')
        self.assertContains(response, 'button-spinner')
        self.assertContains(response, 'id_tratamiento_datos_analisis_ia')
        self.assertContains(response, 'Debes autorizar el analisis asistido del contrato para continuar.')
        self.assertContains(response, 'Debes analizar el contrato antes de continuar.')
        self.assertContains(response, 'Certificado seleccionado')
        self.assertContains(response, 'Contrato seleccionado')
        self.assertContains(response, 'Registrando solicitud...')
        self.assertContains(response, 'authorization-grid-linear')
        self.assertContains(response, 'authorization-card-simple')
        self.assertContains(response, 'Fecha final inferida - requiere confirmacion.')
        self.assertContains(response, 'Valor pendiente inferido - requiere confirmacion.')
        self.assertContains(response, 'Contrato vencido detectado')
        self.assertContains(response, 'No podemos calcular capacidad contractual disponible con este documento')
        self.assertNotContains(response, 'mostrarPaso(4)')
        self.assertContains(response, 'data-next-step="2"')
        self.assertNotContains(response, 'id="step-5"')
        self.assertNotContains(response, 'step-indicator-5')
        self.assertNotContains(response, 'data-next-step="5"')
        self.assertContains(response, 'id="step-4"')
        self.assertContains(response, 'id_terminos_aceptados')
        self.assertContains(response, 'id_autorizacion_datacredito_aceptada')
        self.assertNotContains(response, 'Ver t')
        self.assertNotContains(response, 'consulta crediticia. Solo se usa')
        self.assertContains(response, 'Autorizo la consulta de mi informacion financiera y crediticia')

    def test_post_incompleto_no_crea_solicitud(self):
        response = self.client.post(
            '/solicitar/',
            {},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese los nombres.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_tratamiento_datos_analisis_ia_obligatorio(self):
        payload = self._payload()
        payload.pop('tratamiento_datos_analisis_ia')

        response = self.client.post('/solicitar/', payload, HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes autorizar el analisis asistido del contrato para continuar.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_autorizacion_ia_sin_analisis_no_crea_solicitud(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(analisis_contractual_metadata=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes analizar el contrato antes de continuar.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_con_analisis_contractual_obsoleto_no_crea_solicitud(self):
        metadata_obsoleta = {
            'metadata': {},
            'advertencias': [],
            'bloqueos': [],
            'eventos': [],
            'sugerencia_empresa': {},
            'requiere_revision_manual': False,
            'analysis_input_hash': 'hash-anterior',
            'analysis_generated_at': '2026-06-21T10:00:00-05:00',
        }

        response = self.client.post(
            '/solicitar/',
            self._payload(analisis_contractual_metadata=json.dumps(metadata_obsoleta)),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MENSAJE_ANALISIS_CONTRACTUAL_OBSOLETO)
        self.assertEqual(ContractorApplication.objects.count(), 0)

        evento = TimelinePrestador.objects.get()
        self.assertEqual(evento.tipo_evento, 'ANALISIS_IA_CONTRATO')
        self.assertEqual(evento.estado_resultante, 'CONTRATO_ANALISIS_OBSOLETO')
        self.assertEqual(evento.titulo, 'COMPORTAMIENTO_DIGITAL_ANALISIS_CONTRATO_OBSOLETO')
        self.assertTrue(evento.metadata['requires_reanalysis'])
        self.assertEqual(evento.metadata['reason'], 'analysis_input_hash_mismatch')
        self.assertNotIn('1020304050', str(evento.metadata))
        self.assertNotIn('%PDF', str(evento.metadata))
        self.assertNotIn('prompt', str(evento.metadata).lower())
        self.assertNotIn('base64', str(evento.metadata).lower())

    def test_empresa_detectada_distinta_a_seleccionada_bloquea_y_registra_timeline(self):
        otra_empresa = Empresa.objects.create(
            nombre='Otra Empresa Convenio',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        datos = self._payload(empresa=str(otra_empresa.id), empresa_busqueda=otra_empresa.nombre)
        metadata = self._metadata_analisis_vigente(datos)
        metadata['sugerencia_empresa'] = {
            'empresa_id': self.empresa_core.id,
            'nombre': self.empresa_core.nombre,
            'tipo_coincidencia': 'nit_exacto',
        }

        response = self.client.post(
            '/solicitar/',
            self._payload(
                empresa=str(otra_empresa.id),
                empresa_busqueda=otra_empresa.nombre,
                analisis_contractual_metadata=json.dumps(metadata),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La empresa seleccionada no coincide con la empresa detectada en el contrato.')
        self.assertEqual(ContractorApplication.objects.count(), 0)
        evento = TimelinePrestador.objects.get(estado_resultante='EMPRESA_CONTRATO_NO_COINCIDE')
        self.assertEqual(evento.titulo, 'COMPORTAMIENTO_DIGITAL_EMPRESA_NO_COINCIDE')
        self.assertTrue(evento.metadata['requiere_revision_manual'])
        self.assertNotIn('1020304050', str(evento.metadata))
        self.assertNotIn('%PDF', str(evento.metadata))

    def test_cambio_documento_invalida_analisis_y_limpia_bloqueo_anterior(self):
        datos_originales = self._payload()
        metadata = self._metadata_analisis_vigente(datos_originales, bloqueos=['contrato_vencido_detectado'])

        response = self.client.post(
            '/solicitar/',
            self._payload(
                numero_documento='1020304051',
                analisis_contractual_metadata=json.dumps(metadata),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MENSAJE_ANALISIS_CONTRACTUAL_OBSOLETO)
        self.assertNotContains(response, 'El contrato detectado esta vencido')
        self.assertEqual(TimelinePrestador.objects.filter(estado_resultante='CONTRATO_ANALISIS_OBSOLETO').count(), 1)

    def test_cambio_empresa_invalida_analisis_anterior(self):
        otra_empresa = Empresa.objects.create(
            nombre='Otra Empresa Convenio',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        datos_originales = self._payload()
        metadata = self._metadata_analisis_vigente(datos_originales)

        response = self.client.post(
            '/solicitar/',
            self._payload(
                empresa=str(otra_empresa.id),
                empresa_busqueda=otra_empresa.nombre,
                analisis_contractual_metadata=json.dumps(metadata),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MENSAJE_ANALISIS_CONTRACTUAL_OBSOLETO)
        self.assertEqual(TimelinePrestador.objects.filter(estado_resultante='CONTRATO_ANALISIS_OBSOLETO').count(), 1)

    def test_cambio_contrato_pdf_invalida_analisis_anterior(self):
        datos_originales = self._payload()
        metadata = self._metadata_analisis_vigente(datos_originales, contenido_contrato=b'%PDF-contrato-original')

        response = self.client.post(
            '/solicitar/',
            self._payload(
                contrato_actual=SimpleUploadedFile(
                    'contrato-nuevo.pdf',
                    b'%PDF-contrato-nuevo',
                    content_type='application/pdf',
                ),
                analisis_contractual_metadata=json.dumps(metadata),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MENSAJE_ANALISIS_CONTRACTUAL_OBSOLETO)
        self.assertEqual(TimelinePrestador.objects.filter(estado_resultante='CONTRATO_ANALISIS_OBSOLETO').count(), 1)

    @override_settings(
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-v1',
        DATACREDITO_AUTHORIZATION_TEXT='Texto aprobado para consulta DataCredito de prestadores.',
    )
    def test_post_con_autorizacion_datacredito_crea_evidencia_versionada(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(autorizacion_datacredito_aceptada='on'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContractorApplication.objects.count(), 1)
        autorizacion = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual(autorizacion.solicitud, ContractorApplication.objects.get())
        self.assertEqual(autorizacion.usuario, self.usuario)
        self.assertEqual(autorizacion.version_texto, 'uat-v1')
        self.assertEqual(autorizacion.source, AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO)

    def test_post_solo_terminos_generales_no_crea_autorizacion_datacredito(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContractorApplication.objects.count(), 1)
        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)

    def test_formulario_tiene_links_terminos_y_privacidad(self):
        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/terminos-y-condiciones/')
        self.assertContains(response, '/politica-de-privacidad/')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener"')

    def test_post_sin_documentos_obligatorios_rechaza(self):
        payload = self._payload()
        for campo in (
            'documento_identidad_frontal',
            'documento_identidad_reverso',
            'contrato_actual',
            'certificado_bancario',
        ):
            payload.pop(campo)

        response = self.client.post(
            '/solicitar/',
            payload,
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cargue la cedula frontal.')
        self.assertContains(response, 'Cargue la cedula trasera.')
        self.assertContains(response, 'Cargue el contrato vigente en PDF.')
        self.assertContains(response, 'Cargue el certificado bancario en PDF.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_documentos_pdf_e_imagen_invalidos_rechaza(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(
                documento_identidad_frontal=SimpleUploadedFile(
                    'cedula-frontal.pdf',
                    b'%PDF-frontal',
                    content_type='application/pdf',
                ),
                contrato_actual=SimpleUploadedFile(
                    'contrato.jpg',
                    b'imagen',
                    content_type='image/jpeg',
                ),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La cedula frontal debe ser imagen JPG o PNG.')
        self.assertContains(response, 'El contrato vigente debe cargarse en PDF.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_documentos_repetidos_rechaza(self):
        contenido = b'mismo-archivo'
        response = self.client.post(
            '/solicitar/',
            self._payload(
                documento_identidad_frontal=SimpleUploadedFile(
                    'cedula.jpg',
                    contenido,
                    content_type='image/jpeg',
                ),
                documento_identidad_reverso=SimpleUploadedFile(
                    'cedula.jpg',
                    contenido,
                    content_type='image/jpeg',
                ),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No puedes cargar el mismo archivo en documentos diferentes.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_contrato_y_certificado_mismo_archivo_rechaza(self):
        contenido = b'%PDF-mismo-archivo'
        response = self.client.post(
            '/solicitar/',
            self._payload(
                contrato_actual=SimpleUploadedFile(
                    'LC_FACTURAS.pdf',
                    contenido,
                    content_type='application/pdf',
                ),
                certificado_bancario=SimpleUploadedFile(
                    'LC_FACTURAS.pdf',
                    contenido,
                    content_type='application/pdf',
                ),
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No puedes cargar el mismo archivo en documentos diferentes.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_cedula_manual_sin_camara_rechaza(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(
                documento_identidad_frontal_capturado='',
                documento_identidad_reverso_capturado='',
            ),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La cedula frontal debe capturarse en vivo desde la camara.')
        self.assertContains(response, 'La cedula trasera debe capturarse en vivo desde la camara.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_tipo_documento_solo_permite_cc_ce(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(tipo_documento='cedula'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seleccione un tipo de documento valido.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_numero_documento_invalido_bloqueado(self):
        for numero_documento in ('111111', '1111111', '11111111', '222222', '123456', '123456789', '000000'):
            with self.subTest(numero_documento=numero_documento):
                response = self.client.post(
                    '/solicitar/',
                    self._payload(numero_documento=numero_documento),
                    HTTP_HOST='contratistas.aprobado.com.co',
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Ingresa un numero de documento valido.')

        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_busqueda_empresa_devuelve_resultados_de_convenio(self):
        response = self.client.get(
            '/empresas/buscar/?q=Empresa',
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'][0]['id'], self.empresa_core.id)

    def test_busqueda_empresa_prioriza_nit_exacto(self):
        response = self.client.get(
            '/empresas/buscar/?q=900123456',
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'][0]['id'], self.empresa_core.id)
        self.assertEqual(data['results'][0]['tipo_coincidencia'], 'nit_exacto')

    def test_busqueda_empresa_normaliza_nombre_exactamente(self):
        response = self.client.get(
            '/empresas/buscar/?q=empresa convenio contratistas sas',
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'][0]['id'], self.empresa_core.id)
        self.assertEqual(data['results'][0]['tipo_coincidencia'], 'nombre_exacto')

    def test_busqueda_empresa_coincidencia_aproximada_solo_sugiere(self):
        response = self.client.get(
            '/empresas/buscar/?q=Convenio Contra',
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'][0]['id'], self.empresa_core.id)
        self.assertEqual(data['results'][0]['tipo_coincidencia'], 'sugerencia')

    def test_post_empresa_busqueda_sin_seleccion_rechaza(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(empresa='', empresa_busqueda='Empresa Convenio'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes elegir una empresa de la lista de resultados.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_anonimo_redirige_a_login(self):
        self.client.logout()

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/login/?next=/solicitar/')

    def test_post_valido_crea_contractor_application(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
            HTTP_USER_AGENT='Navegador Prueba',
            HTTP_X_FORWARDED_FOR='10.0.0.9, 10.0.0.10',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertIsNone(solicitud.organization)
        self.assertIsNone(solicitud.product_config)
        self.assertEqual(solicitud.configuracion_portal, self.configuracion_portal)
        self.assertEqual(solicitud.usuario, self.usuario)
        self.assertEqual(solicitud.status, ContractorApplication.Estado.RECIBIDA)
        self.assertEqual(solicitud.escenario_credito, ContractorApplication.EscenarioCredito.NUEVO_CREDITO)
        self.assertEqual(solicitud.source_subdomain, 'contratistas')
        self.assertEqual(solicitud.ip_address, '10.0.0.9')
        self.assertEqual(solicitud.user_agent, 'Navegador Prueba')
        self.assertIsNone(solicitud.requested_amount)
        self.assertIsNone(solicitud.term_months)
        self.assertEqual(solicitud.estimated_monthly_payment, Decimal('0.00'))
        self.assertTrue(solicitud.simulation_payload['simulacion_pendiente'])
        self.assertIn('analisis_contrato_ia', solicitud.simulation_payload)
        self.assertFalse(solicitud.simulation_payload['analisis_contrato_ia']['enabled'])
        self.assertTrue(hasattr(solicitud, 'informacion_laboral'))
        self.assertEqual(solicitud.informacion_laboral.cargo, 'Consultora comercial')
        self.assertEqual(solicitud.informacion_laboral.empresa, self.empresa_core)
        self.assertEqual(solicitud.informacion_laboral.empresa_contratante_nombre, self.empresa_core.nombre)
        self.assertEqual(ContractorApplicationDocument.objects.filter(application=solicitud).count(), 4)

    def test_post_valido_no_requiere_modelos_legacy(self):
        ContractorBranding.objects.all().delete()
        ContractorProductConfig.objects.all().delete()
        ContractorOrganization.objects.all().delete()

        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertEqual(solicitud.configuracion_portal, self.configuracion_portal)
        self.assertIsNone(solicitud.organization)
        self.assertIsNone(solicitud.product_config)
        self.assertEqual(solicitud.escenario_credito, ContractorApplication.EscenarioCredito.NUEVO_CREDITO)

    def test_solicitud_no_valida_monto_y_plazo_en_formulario_inicial(self):
        self.configuracion.max_amount = Decimal('5000000.00')
        self.configuracion.max_term_months = 36
        self.configuracion.save(update_fields=['max_amount', 'max_term_months'])
        self.configuracion_portal.monto_maximo = Decimal('2000000.00')
        self.configuracion_portal.plazo_maximo_meses = 12
        self.configuracion_portal.save(update_fields=['monto_maximo', 'plazo_maximo_meses'])

        response = self.client.post(
            '/solicitar/',
            self._payload(monto='3000000.00', plazo_meses='18'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertIsNone(solicitud.requested_amount)
        self.assertIsNone(solicitud.term_months)

    def test_solicitud_guarda_escenario_credito_recogida(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(escenario_credito=ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertEqual(solicitud.escenario_credito, ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA)

    def test_post_valido_redirige_a_simulacion(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        solicitud = ContractorApplication.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/simular/?solicitud_id={solicitud.id}')

    def test_post_invalido_no_crea_solicitud(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(nombres=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese los nombres.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_sin_empresa_rechaza(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(empresa=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seleccione la empresa contratante.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_post_empresa_no_elegible_rechaza(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(empresa=str(self.empresa_no_elegible.id)),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La empresa seleccionada no es valida.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_documento_obligatorio(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(numero_documento=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese el numero de documento.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_telefono_obligatorio(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(celular=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese el celular.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_email_valido_obligatorio(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(correo='correo-invalido'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese un correo electronico valido.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_direccion_obligatoria(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(direccion=''),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingrese la direccion.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_terminos_obligatorios(self):
        payload = self._payload()
        payload.pop('terminos_aceptados')

        response = self.client.post('/solicitar/', payload, HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debe aceptar terminos y condiciones.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_ia_deshabilitada_no_rompe_flujo(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        metadata = solicitud.simulation_payload['analisis_contrato_ia']
        self.assertFalse(metadata['enabled'])
        self.assertFalse(metadata['attempted'])
        self.assertFalse(metadata['success'])
        self.assertEqual(metadata['error_tipo'], 'analisis_en_endpoint_independiente')

    def test_endpoint_analisis_contrato_requiere_login(self):
        self.client.logout()

        response = self.client.post(
            '/contrato/analizar/',
            {'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf')},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/login/?next=/contrato/analizar/')

    def test_endpoint_analisis_contrato_rechaza_no_pdf(self):
        response = self.client.post(
            '/contrato/analizar/',
            {
                'contrato_actual': SimpleUploadedFile('contrato.jpg', b'imagen', content_type='image/jpeg'),
                'tratamiento_datos_analisis_ia': '1',
            },
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_endpoint_analisis_contrato_ia_deshabilitada_permite_manual(self):
        response = self.client.post(
            '/contrato/analizar/',
            {
                'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf'),
                'tratamiento_datos_analisis_ia': '1',
            },
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data['success'])
        self.assertTrue(data['manual_allowed'])
        self.assertEqual(data['metadata']['error_tipo'], 'ia_deshabilitada')
        self.assertEqual(ContractorApplication.objects.count(), 0)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_endpoint_analisis_contrato_no_contrato_devuelve_error_seguro(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=False,
            tipo_documento_detectado='certificado',
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self.client.post(
                '/contrato/analizar/',
                {
                    'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-certificado', content_type='application/pdf'),
                    'tratamiento_datos_analisis_ia': '1',
                },
                HTTP_HOST='contratistas.aprobado.com.co',
            )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data['success'])
        self.assertFalse(data['manual_allowed'])
        self.assertFalse(data['es_contrato'])
        self.assertEqual(data['error'], 'El documento cargado no parece ser un contrato valido.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_endpoint_analisis_contrato_devuelve_json_normalizado(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=True,
            empresa_contratante='Empresa Convenio Contratistas',
            nit_empresa='900123456',
            nombre_contratista='Ana Perez',
            documento_contratista='1020304050',
            cargo_o_servicio='Consultoria',
            fecha_inicio_contrato=date(2026, 1, 1),
            fecha_fin_contrato=date(2026, 12, 31),
            valor_total_contrato=Decimal('12000000.00'),
            valor_mensual_o_honorarios=Decimal('1000000.00'),
            valor_pendiente_estimado=Decimal('8000000.00'),
            moneda='COP',
            campos_no_encontrados=(),
            advertencias=('Confirmar valores.',),
            confianza_general=Decimal('0.85'),
            requiere_confirmacion_usuario=True,
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )
        empresas_antes = Empresa.objects.count()

        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self.client.post(
                '/contrato/analizar/',
                {
                    'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf'),
                    'tratamiento_datos_analisis_ia': '1',
                },
                HTTP_HOST='contratistas.aprobado.com.co',
            )

        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['es_contrato'])
        self.assertEqual(data['datos']['cargo_o_servicio'], 'Consultoria')
        self.assertEqual(data['datos']['fecha_inicio_contrato'], '2026-01-01')
        self.assertEqual(data['datos']['valor_pendiente_estimado'], '8000000.00')
        self.assertIn('analisis_contractual_seguro', data)
        self.assertIn('sugerencia_empresa', data)
        self.assertIn('valor_pendiente_fuente', data['analisis_contractual_seguro']['metadata'])
        self.assertEqual(data['confianza_general'], 0.85)
        self.assertEqual(Empresa.objects.count(), empresas_antes)
        serializado = str(data).lower()
        self.assertNotIn('prompt', serializado)
        self.assertNotIn('base64', serializado)
        self.assertNotIn('%pdf-contrato', serializado)

    def test_formulario_tiene_endpoint_y_autocompletado_ia(self):
        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertContains(response, 'data-analisis-contrato-url="/contrato/analizar/"')
        self.assertContains(response, 'Analizar contrato')
        self.assertContains(response, 'documentoDetectado')
        self.assertContains(response, 'El documento detectado en el contrato no coincide')

    def test_metadata_ia_no_guarda_prompt_texto_completo_base64_ni_api_key(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        metadata = solicitud.simulation_payload['analisis_contrato_ia']
        metadata_serializada = str(metadata).lower()
        self.assertNotIn('prompt', metadata_serializada)
        self.assertNotIn('base64', metadata_serializada)
        self.assertNotIn('api_key', metadata_serializada)
        self.assertNotIn('%pdf-contrato', metadata_serializada)
        self.assertEqual(metadata['error_tipo'], 'analisis_en_endpoint_independiente')
        self.assertFalse(metadata['attempted'])

    @override_settings(CONTRACTORS_CONTRACT_AI_ENABLED=True, OPENAI_API_KEY='sk-test')
    def test_ia_es_contrato_false_bloquea(self):
        resultado = ResultadoAnalisisContratoIAOpenAI(
            es_contrato=False,
            tipo_documento_detectado='certificado',
            habilitado=True,
            exito=True,
            modelo_usado='gpt-4o-mini',
        )
        with patch('contractors.views.analizar_contrato_con_openai', return_value=resultado):
            response = self.client.post(
                '/contrato/analizar/',
                {
                    'contrato_actual': SimpleUploadedFile(
                        'certificado.pdf',
                        b'%PDF-certificado',
                        content_type='application/pdf',
                    ),
                    'tratamiento_datos_analisis_ia': '1',
                    'numero_documento': '1020304050',
                },
                HTTP_HOST='contratistas.aprobado.com.co',
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertFalse(data['manual_allowed'])
        self.assertEqual(data['error'], 'El documento cargado no parece ser un contrato valido.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_formulario_inicial_no_exige_monto_positivo(self):
        for indice, monto in enumerate(('0', '-1000'), start=1):
            with self.subTest(monto=monto):
                response = self.client.post(
                    '/solicitar/',
                    self._payload(monto=monto, numero_documento=f'102030405{indice}'),
                    HTTP_HOST='contratistas.aprobado.com.co',
                )

                self.assertEqual(response.status_code, 302)

        self.assertEqual(ContractorApplication.objects.count(), 2)
        self.assertFalse(
            ContractorApplication.objects.exclude(requested_amount__isnull=True).exists(),
        )

    def test_formulario_inicial_no_exige_plazo_positivo(self):
        for indice, plazo in enumerate(('0', '-1'), start=1):
            with self.subTest(plazo=plazo):
                response = self.client.post(
                    '/solicitar/',
                    self._payload(plazo_meses=plazo, numero_documento=f'102030406{indice}'),
                    HTTP_HOST='contratistas.aprobado.com.co',
                )

                self.assertEqual(response.status_code, 302)

        self.assertEqual(ContractorApplication.objects.count(), 2)
        self.assertFalse(
            ContractorApplication.objects.exclude(term_months__isnull=True).exists(),
        )

    def test_formulario_inicial_no_valida_monto_y_plazo_fuera_de_configuracion(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(monto='6000000.00', plazo_meses='25'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertIsNone(solicitud.requested_amount)
        self.assertIsNone(solicitud.term_months)

    def test_doble_post_actualmente_crea_dos_pre_solicitudes(self):
        primera = self.client.post('/solicitar/', self._payload(), HTTP_HOST='contratistas.aprobado.com.co')
        segunda = self.client.post('/solicitar/', self._payload(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(primera.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(ContractorApplication.objects.count(), 2)

    def test_organizacion_inactiva_devuelve_404(self):
        self.configuracion_portal.activo = False
        self.configuracion_portal.save(update_fields=['activo'])

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_subdominio_inexistente_devuelve_404(self):
        response = self.client.get('/solicitar/', HTTP_HOST='inexistente.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_configuracion_inactiva_devuelve_404(self):
        self.configuracion_portal.activo = False
        self.configuracion_portal.save(update_fields=['activo'])

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_dominio_raiz_no_expone_solicitud_contratista(self):
        response = self.client.get('/solicitar/', HTTP_HOST='aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_contratista_a_usa_configuracion_y_branding_a(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertEqual(solicitud.configuracion_portal, self.configuracion_portal)

    def test_contratista_a_nunca_usa_configuracion_b(self):
        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        solicitud = ContractorApplication.objects.get()
        self.assertEqual(solicitud.configuracion_portal, self.configuracion_portal)
        self.assertNotIn('tasa_mensual', solicitud.simulation_payload)

    def test_no_crea_modelos_financieros_del_flujo(self):
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
            'auditoria_regla_especial': CreditoReglaEspecialAudit.objects.count(),
        }

        response = self.client.post(
            '/solicitar/',
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])
        self.assertEqual(CreditoReglaEspecialAudit.objects.count(), conteos_antes['auditoria_regla_especial'])


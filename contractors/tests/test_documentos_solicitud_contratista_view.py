import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ConfiguracionPortalContratistas,
    TAMANO_MAXIMO_DOCUMENTO_BYTES,
)
from contractors.services.documentos import DatosDocumentoSolicitudContratista, registrar_documento_solicitud_contratista
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago, Pagare


MEDIA_ROOT_TEMPORAL = tempfile.mkdtemp()


@override_settings(
    PRIMARY_DOMAIN_HOST='aprobado.com.co',
    CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
    ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    MEDIA_ROOT=MEDIA_ROOT_TEMPORAL,
)
class DocumentosSolicitudContratistaViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT_TEMPORAL, ignore_errors=True)

    def setUp(self):
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
            monto_minimo=Decimal('100000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )
        ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Credito',
            primary_color='#112233',
            secondary_color='#445566',
        )
        ContractorBranding.objects.create(
            organization=self.otra_organizacion,
            display_name='Beta Credito',
            primary_color='#778899',
            secondary_color='#aabbcc',
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
        self.solicitud = self._crear_solicitud(self.organizacion, self.configuracion, '123456789', self.configuracion_portal)
        self.otra_solicitud = self._crear_solicitud(self.otra_organizacion, self.otra_configuracion, '987654321')
        self.usuario = get_user_model().objects.create_user(
            username='contratista-docs',
            email='contratista-docs@example.com',
            password='password-test',
        )
        self.solicitud.usuario = self.usuario
        self.solicitud.save(update_fields=['usuario'])
        self.client.force_login(self.usuario)

    def test_anonimo_redirige_a_login(self):
        self.client.logout()

        response = self.client.get(
            f'/solicitud/{self.solicitud.id}/documentos/',
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/login/?next=/solicitud/{self.solicitud.id}/documentos/')

    def _crear_solicitud(self, organizacion, configuracion, documento, configuracion_portal=None):
        return ContractorApplication.objects.create(
            organization=organizacion,
            configuracion_portal=configuracion_portal,
            product_config=configuracion,
            status=ContractorApplication.Estado.RECIBIDA,
            requested_amount=Decimal('1000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number=documento,
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain=organizacion.subdomain,
        )

    def _crear_solicitud_portal(self, documento='555555555'):
        return ContractorApplication.objects.create(
            organization=None,
            configuracion_portal=self.configuracion_portal,
            product_config=None,
            status=ContractorApplication.Estado.RECIBIDA,
            requested_amount=Decimal('1000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number=documento,
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
            usuario=getattr(self, 'usuario', None),
        )

    def _archivo(self, nombre='documento.pdf', content_type='application/pdf', contenido=b'archivo'):
        return SimpleUploadedFile(nombre, contenido, content_type=content_type)

    def _url(self, solicitud=None):
        solicitud = solicitud or self.solicitud
        return f'/solicitud/{solicitud.id}/documentos/'

    def _payload(self, **overrides):
        datos = {
            'tipo_documento': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
            'archivo': self._archivo(),
        }
        datos.update(overrides)
        return datos

    def test_get_muestra_formulario_y_documentos_existentes(self):
        registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                archivo=self._archivo('contrato.pdf'),
                nombre_original='contrato.pdf',
                content_type='application/pdf',
                tamano_archivo=7,
            ),
        )

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cargar documento')
        self.assertContains(response, 'contrato.pdf')
        self.assertContains(response, 'Acme Credito')
        self.assertContains(response, 'certificado bancario se carga en PDF')

    def test_usuario_no_puede_ver_documentos_de_solicitud_de_otro_usuario(self):
        otro_usuario = get_user_model().objects.create_user(
            username='otro-contratista-docs',
            email='otro-docs@example.com',
            password='password-test',
        )
        self.solicitud.usuario = otro_usuario
        self.solicitud.save(update_fields=['usuario'])

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_post_valido_crea_documento(self):
        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)
        documento = ContractorApplicationDocument.objects.get()
        self.assertEqual(documento.application, self.solicitud)
        self.assertEqual(documento.status, ContractorApplicationDocument.Estado.RECIBIDO)
        self.assertContains(response, 'Documento recibido')

    def test_documentos_funcionan_con_solicitud_solo_configuracion_portal(self):
        solicitud = self._crear_solicitud_portal()

        response_get = self.client.get(self._url(solicitud), HTTP_HOST='contratistas.aprobado.com.co')
        response_post = self.client.post(
            self._url(solicitud),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, 'Acme Credito')
        self.assertEqual(response_post.status_code, 200)
        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)
        self.assertEqual(ContractorApplicationDocument.objects.get().application, solicitud)
        self.assertIsNone(solicitud.organization)
        self.assertIsNone(solicitud.product_config)

    def test_post_invalido_no_crea_documento(self):
        response = self.client.post(
            self._url(),
            {'tipo_documento': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seleccione un archivo.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_solicitud_de_otra_organizacion_devuelve_404(self):
        response = self.client.get(self._url(self.otra_solicitud), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_subdominio_inexistente_devuelve_404(self):
        response = self.client.get(self._url(), HTTP_HOST='inexistente.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_organizacion_inactiva_devuelve_404(self):
        self.configuracion_portal.activo = False
        self.configuracion_portal.save(update_fields=['activo'])

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_dominio_raiz_no_expone_ruta(self):
        response = self.client.get(self._url(), HTTP_HOST='aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_tipo_invalido_rechazado(self):
        response = self.client.post(
            self._url(),
            self._payload(tipo_documento='tipo_invalido'),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El tipo de documento no esta permitido.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_content_type_invalido_rechazado(self):
        response = self.client.post(
            self._url(),
            self._payload(archivo=self._archivo('archivo.exe', 'application/octet-stream')),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contrato y certificado bancario deben cargarse en PDF.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_tamano_excedido_rechazado(self):
        response = self.client.post(
            self._url(),
            self._payload(archivo=self._archivo(contenido=b'a' * (TAMANO_MAXIMO_DOCUMENTO_BYTES + 1))),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El archivo supera el tamano maximo permitido.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_solicitud_convertida_no_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])

        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La solicitud no permite carga de documentos en su estado actual.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_solicitud_rechazada_no_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.RECHAZADA
        self.solicitud.save(update_fields=['status'])

        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La solicitud no permite carga de documentos en su estado actual.')
        self.assertEqual(ContractorApplicationDocument.objects.count(), 0)

    def test_solicitud_recibida_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.RECIBIDA
        self.solicitud.save(update_fields=['status'])

        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)

    def test_solicitud_en_revision_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.EN_REVISION
        self.solicitud.save(update_fields=['status'])

        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)

    def test_template_no_muestra_file_path(self):
        documento = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                archivo=self._archivo('privado.pdf'),
                nombre_original='privado.pdf',
                content_type='application/pdf',
                tamano_archivo=7,
            ),
        ).documento

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'privado.pdf')
        self.assertNotContains(response, documento.file.path)
        self.assertNotContains(response, str(documento.file))

    def test_no_crea_modelos_financieros_del_flujo(self):
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        response = self.client.post(
            self._url(),
            self._payload(),
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])


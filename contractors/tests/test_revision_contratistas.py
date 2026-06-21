import shutil
import tempfile
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
)
from contractors.services.documentos import DatosDocumentoSolicitudContratista, registrar_documento_solicitud_contratista
from contractors.services.revision import (
    aprobar_documento_solicitud,
    marcar_solicitud_en_revision,
    rechazar_documento_solicitud,
    rechazar_solicitud_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago, Pagare


User = get_user_model()
MEDIA_ROOT_TEMPORAL = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT_TEMPORAL)
class RevisionContratistasTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT_TEMPORAL, ignore_errors=True)

    def setUp(self):
        self.usuario = User.objects.create_user(username='revisor', password='x', is_staff=True)
        self.usuario_sin_permiso = User.objects.create_user(username='sin_permiso', password='x', is_staff=True)
        self.usuario.user_permissions.add(Permission.objects.get(codename='can_review_contractor_application'))
        self.usuario.user_permissions.add(Permission.objects.get(codename='can_review_contractor_document'))
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='acme',
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
        self.solicitud = self._crear_solicitud()

    def _crear_solicitud(self, estado=ContractorApplication.Estado.RECIBIDA):
        return ContractorApplication.objects.create(
            organization=self.organizacion,
            product_config=self.configuracion,
            status=estado,
            requested_amount=Decimal('1000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number='123456789',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='acme',
        )

    def _documento(self, solicitud=None):
        solicitud = solicitud or self.solicitud
        archivo = SimpleUploadedFile('documento.pdf', b'archivo', content_type='application/pdf')
        return registrar_documento_solicitud_contratista(
            solicitud=solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                archivo=archivo,
                nombre_original='documento.pdf',
                content_type='application/pdf',
                tamano_archivo=7,
            ),
        ).documento

    def test_solicitud_recibida_pasa_a_en_revision(self):
        solicitud = marcar_solicitud_en_revision(self.solicitud, self.usuario, observacion='Revision inicial')

        self.assertEqual(solicitud.status, ContractorApplication.Estado.EN_REVISION)
        self.assertEqual(solicitud.revisado_por, self.usuario)
        self.assertIsNotNone(solicitud.revisado_en)
        self.assertEqual(solicitud.notas_revision, 'Revision inicial')

    def test_solicitud_recibida_pasa_a_rechazada_con_motivo(self):
        solicitud = rechazar_solicitud_contratista(self.solicitud, self.usuario, motivo='Datos insuficientes')

        self.assertEqual(solicitud.status, ContractorApplication.Estado.RECHAZADA)
        self.assertEqual(solicitud.revisado_por, self.usuario)
        self.assertIsNotNone(solicitud.revisado_en)
        self.assertEqual(solicitud.notas_revision, 'Datos insuficientes')

    def test_solicitud_en_revision_pasa_a_rechazada(self):
        self.solicitud.status = ContractorApplication.Estado.EN_REVISION
        self.solicitud.save(update_fields=['status'])

        solicitud = rechazar_solicitud_contratista(self.solicitud, self.usuario, motivo='No cumple')

        self.assertEqual(solicitud.status, ContractorApplication.Estado.RECHAZADA)

    def test_solicitud_convertida_no_se_modifica(self):
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ValidationError):
            marcar_solicitud_en_revision(self.solicitud, self.usuario)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, ContractorApplication.Estado.CONVERTIDA)

    def test_documento_recibido_pasa_a_aprobado(self):
        documento = self._documento()

        documento = aprobar_documento_solicitud(documento, self.usuario, observacion='Legible')

        self.assertEqual(documento.status, ContractorApplicationDocument.Estado.APROBADO)
        self.assertEqual(documento.reviewed_by, self.usuario)
        self.assertIsNotNone(documento.reviewed_at)
        self.assertEqual(documento.review_notes, 'Legible')

    def test_documento_recibido_pasa_a_rechazado_con_motivo(self):
        documento = self._documento()

        documento = rechazar_documento_solicitud(documento, self.usuario, motivo='No legible')

        self.assertEqual(documento.status, ContractorApplicationDocument.Estado.RECHAZADO)
        self.assertEqual(documento.reviewed_by, self.usuario)
        self.assertIsNotNone(documento.reviewed_at)
        self.assertEqual(documento.review_notes, 'No legible')

    def test_documento_de_solicitud_convertida_no_se_modifica(self):
        documento = self._documento()
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ValidationError):
            aprobar_documento_solicitud(documento, self.usuario)

        documento.refresh_from_db()
        self.assertEqual(documento.status, ContractorApplicationDocument.Estado.RECIBIDO)

    def test_documento_de_solicitud_rechazada_no_se_modifica(self):
        documento = self._documento()
        self.solicitud.status = ContractorApplication.Estado.RECHAZADA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ValidationError):
            rechazar_documento_solicitud(documento, self.usuario, motivo='No aplica')

        documento.refresh_from_db()
        self.assertEqual(documento.status, ContractorApplicationDocument.Estado.RECIBIDO)

    def test_usuario_sin_permiso_no_revisa_solicitud(self):
        with self.assertRaises(PermissionDenied):
            marcar_solicitud_en_revision(self.solicitud, self.usuario_sin_permiso)

    def test_usuario_sin_permiso_no_revisa_documento(self):
        documento = self._documento()

        with self.assertRaises(PermissionDenied):
            aprobar_documento_solicitud(documento, self.usuario_sin_permiso)

    def test_no_crea_modelos_financieros_del_flujo(self):
        documento = self._documento()
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        marcar_solicitud_en_revision(self.solicitud, self.usuario)
        aprobar_documento_solicitud(documento, self.usuario)

        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])


class AdminRevisionContratistasTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.usuario = User.objects.create_user(username='admin_revisor', password='x', is_staff=True)
        self.usuario_sin_permiso = User.objects.create_user(username='admin_sin_permiso', password='x', is_staff=True)
        self.usuario.user_permissions.add(Permission.objects.get(codename='can_review_contractor_application'))
        self.usuario.user_permissions.add(Permission.objects.get(codename='can_review_contractor_document'))

    def _request(self, usuario):
        request = self.factory.get('/admin/')
        request.user = usuario
        return request

    def test_usuario_sin_permiso_no_ve_acciones_admin_de_solicitud(self):
        admin_modelo = admin.site._registry[ContractorApplication]
        acciones = admin_modelo.get_actions(self._request(self.usuario_sin_permiso))

        self.assertNotIn('accion_marcar_en_revision', acciones)
        self.assertNotIn('accion_rechazar_solicitud', acciones)

    def test_usuario_con_permiso_ve_acciones_admin_de_solicitud(self):
        admin_modelo = admin.site._registry[ContractorApplication]
        acciones = admin_modelo.get_actions(self._request(self.usuario))

        self.assertIn('accion_marcar_en_revision', acciones)
        self.assertIn('accion_rechazar_solicitud', acciones)

    def test_usuario_sin_permiso_no_ve_acciones_admin_de_documento(self):
        admin_modelo = admin.site._registry[ContractorApplicationDocument]
        acciones = admin_modelo.get_actions(self._request(self.usuario_sin_permiso))

        self.assertNotIn('accion_aprobar_documento', acciones)
        self.assertNotIn('accion_rechazar_documento', acciones)

    def test_usuario_con_permiso_ve_acciones_admin_de_documento(self):
        admin_modelo = admin.site._registry[ContractorApplicationDocument]
        acciones = admin_modelo.get_actions(self._request(self.usuario))

        self.assertIn('accion_aprobar_documento', acciones)
        self.assertIn('accion_rechazar_documento', acciones)

import shutil
import tempfile
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
    TAMANO_MAXIMO_DOCUMENTO_BYTES,
)
from contractors.selectors import (
    listar_documentos_solicitud_contratista,
    obtener_ultimo_documento_por_tipo,
    solicitud_tiene_documento_tipo,
)
from contractors.services.documentos import (
    DatosDocumentoSolicitudContratista,
    registrar_documento_solicitud_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago, Pagare


MEDIA_ROOT_TEMPORAL = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT_TEMPORAL)
class DocumentosSolicitudContratistaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT_TEMPORAL, ignore_errors=True)

    def setUp(self):
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
        self.solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            product_config=self.configuracion,
            status=ContractorApplication.Estado.RECIBIDA,
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

    def _archivo(self, nombre='documento.pdf', content_type='application/pdf', contenido=b'archivo'):
        return SimpleUploadedFile(nombre, contenido, content_type=content_type)

    def _datos(self, **overrides):
        archivo = overrides.pop('archivo', self._archivo())
        datos = {
            'tipo_documento': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
            'archivo': archivo,
            'nombre_original': archivo.name,
            'content_type': archivo.content_type,
            'tamano_archivo': archivo.size,
        }
        datos.update(overrides)
        return DatosDocumentoSolicitudContratista(**datos)

    def test_crea_documento_valido(self):
        resultado = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=self._datos(),
        )

        self.assertIsNotNone(resultado.documento_id)
        self.assertEqual(resultado.estado, ContractorApplicationDocument.Estado.RECIBIDO)
        self.assertEqual(resultado.documento.application, self.solicitud)
        self.assertEqual(resultado.documento.content_type, 'application/pdf')

    def test_rechaza_tipo_invalido(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(tipo_documento='tipo_invalido'),
            )

        self.assertIn('document_type', contexto.exception.message_dict)

    def test_rechaza_tamano_excedido(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(tamano_archivo=TAMANO_MAXIMO_DOCUMENTO_BYTES + 1),
            )

        self.assertIn('file_size', contexto.exception.message_dict)

    def test_rechaza_content_type_invalido(self):
        archivo = self._archivo(nombre='documento.exe', content_type='application/octet-stream')

        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(archivo=archivo),
            )

        self.assertIn('content_type', contexto.exception.message_dict)

    def test_rechaza_file_size_no_positivo(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(tamano_archivo=0),
            )

        self.assertIn('file_size', contexto.exception.message_dict)

    def test_rechaza_nombre_original_vacio(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(nombre_original=''),
            )

        self.assertIn('original_filename', contexto.exception.message_dict)

    def test_rechaza_content_type_vacio(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(content_type=''),
            )

        self.assertIn('content_type', contexto.exception.message_dict)

    def test_rechaza_archivo_sin_extension(self):
        archivo = self._archivo(nombre='documento', content_type='application/pdf')

        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(archivo=archivo),
            )

        self.assertIn('original_filename', contexto.exception.message_dict)

    def test_rechaza_tamano_inconsistente_con_archivo(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(tamano_archivo=999),
            )

        self.assertIn('file_size', contexto.exception.message_dict)

    def test_rechaza_solicitud_de_organizacion_inactiva(self):
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])

        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(
                solicitud=self.solicitud,
                datos=self._datos(),
            )

        self.assertIn('application', contexto.exception.message_dict)

    def test_lista_documentos_solo_de_una_solicitud(self):
        otro = ContractorApplication.objects.create(
            organization=self.organizacion,
            product_config=self.configuracion,
            status=ContractorApplication.Estado.RECIBIDA,
            requested_amount=Decimal('900000.00'),
            term_months=10,
            document_type='CC',
            document_number='987654321',
            first_name='Luis',
            last_name='Gomez',
            phone='3007654321',
            email='luis@example.com',
            address='Calle 4',
            accepted_terms=True,
            source_subdomain='acme',
        )
        documento = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=self._datos(nombre_original='uno.pdf'),
        ).documento
        registrar_documento_solicitud_contratista(
            solicitud=otro,
            datos=self._datos(archivo=self._archivo('dos.pdf'), nombre_original='dos.pdf'),
        )

        documentos = list(listar_documentos_solicitud_contratista(self.solicitud))

        self.assertEqual(documentos, [documento])

    def test_solicitud_tiene_documento_tipo(self):
        self.assertFalse(
            solicitud_tiene_documento_tipo(
                self.solicitud,
                ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
            ),
        )

    def test_permite_varios_documentos_mismo_tipo_y_selector_devuelve_ultimo(self):
        primero = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=self._datos(nombre_original='primero.pdf'),
        ).documento
        segundo = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=self._datos(archivo=self._archivo('segundo.pdf'), nombre_original='segundo.pdf'),
        ).documento

        ultimo = obtener_ultimo_documento_por_tipo(
            self.solicitud,
            ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
        )

        self.assertNotEqual(primero.id, segundo.id)
        self.assertEqual(ultimo, segundo)

    def test_solicitud_convertida_no_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertIn('application', contexto.exception.message_dict)

    def test_solicitud_rechazada_no_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.RECHAZADA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ValidationError) as contexto:
            registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertIn('application', contexto.exception.message_dict)

    def test_solicitud_recibida_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.RECIBIDA
        self.solicitud.save(update_fields=['status'])

        resultado = registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertEqual(resultado.estado, ContractorApplicationDocument.Estado.RECIBIDO)

    def test_solicitud_en_revision_permite_subir_documentos(self):
        self.solicitud.status = ContractorApplication.Estado.EN_REVISION
        self.solicitud.save(update_fields=['status'])

        resultado = registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertEqual(resultado.estado, ContractorApplicationDocument.Estado.RECIBIDO)

        registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertTrue(
            solicitud_tiene_documento_tipo(
                self.solicitud,
                ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
            ),
        )

    def test_no_crea_credito_ni_flujos_productivos(self):
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        registrar_documento_solicitud_contratista(solicitud=self.solicitud, datos=self._datos())

        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])

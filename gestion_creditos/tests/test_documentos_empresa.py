import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.checks import run_checks
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import DocumentoEmpresa, Empresa
from gestion_creditos.services.documentos_empresa import cargar_documento_empresa


class DocumentoEmpresaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._private_root = tempfile.mkdtemp()
        cls._private_override = override_settings(PRIVATE_DOCUMENTS_ROOT=cls._private_root)
        cls._private_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._private_override.disable()
        shutil.rmtree(cls._private_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre='Empresa Documental',
            convenio_activo=True,
            departamento='Meta',
            municipio='Villavicencio',
        )
        self.admin = get_user_model().objects.create_superuser(
            username='admin_documentos',
            email='admin-documentos@example.com',
            password='clave-segura-test',
        )

    def _pdf(self, nombre='documento.pdf', contenido=b'%PDF-1.4 documento de prueba'):
        return SimpleUploadedFile(nombre, contenido, content_type='application/pdf')

    def _cargar(self, tipo, nombre='documento.pdf'):
        return cargar_documento_empresa(
            empresa=self.empresa,
            tipo_documento=tipo,
            archivo=self._pdf(nombre),
            usuario=self.admin,
        )

    def test_carga_los_tres_tipos_institucionales(self):
        tipos = (
            DocumentoEmpresa.TipoDocumento.RUT,
            DocumentoEmpresa.TipoDocumento.CAMARA_COMERCIO,
            DocumentoEmpresa.TipoDocumento.CEDULA_REPRESENTANTE_LEGAL,
        )

        for indice, tipo in enumerate(tipos):
            documento = self._cargar(tipo, f'documento-{indice}.pdf')
            self.assertEqual(documento.estado, DocumentoEmpresa.EstadoDocumento.VIGENTE)
            self.assertTrue(documento.activo)
            self.assertEqual(documento.cargado_por, self.admin)

        self.assertEqual(DocumentoEmpresa.objects.filter(empresa=self.empresa).count(), 3)

    def test_reemplazo_conserva_historico_y_un_solo_documento_activo(self):
        anterior = self._cargar(DocumentoEmpresa.TipoDocumento.RUT, 'rut-anterior.pdf')
        nuevo = self._cargar(DocumentoEmpresa.TipoDocumento.RUT, 'rut-nuevo.pdf')

        anterior.refresh_from_db()
        self.assertFalse(anterior.activo)
        self.assertEqual(anterior.estado, DocumentoEmpresa.EstadoDocumento.REEMPLAZADO)
        self.assertTrue(nuevo.activo)
        self.assertEqual(nuevo.estado, DocumentoEmpresa.EstadoDocumento.VIGENTE)
        self.assertEqual(
            DocumentoEmpresa.objects.filter(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
            ).count(),
            2,
        )
        self.assertEqual(
            DocumentoEmpresa.objects.filter(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
                activo=True,
            ).count(),
            1,
        )

    def test_fallo_de_storage_conserva_version_anterior_activa(self):
        anterior = self._cargar(DocumentoEmpresa.TipoDocumento.RUT, 'rut-anterior.pdf')
        storage = DocumentoEmpresa._meta.get_field('archivo').storage

        with patch.object(storage, '_save', side_effect=OSError('storage no disponible')):
            with self.assertRaises(OSError):
                self._cargar(DocumentoEmpresa.TipoDocumento.RUT, 'rut-fallido.pdf')

        anterior.refresh_from_db()
        self.assertTrue(anterior.activo)
        self.assertEqual(anterior.estado, DocumentoEmpresa.EstadoDocumento.VIGENTE)
        self.assertEqual(
            DocumentoEmpresa.objects.filter(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
            ).count(),
            1,
        )

    def test_reemplazar_archivo_de_version_existente_esta_bloqueado(self):
        documento = self._cargar(DocumentoEmpresa.TipoDocumento.RUT)
        documento.archivo = self._pdf('reemplazo-directo.pdf')

        with self.assertRaises(ValidationError):
            documento.save()

    def test_archivo_invalido_es_rechazado(self):
        with self.assertRaises(ValidationError):
            cargar_documento_empresa(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
                archivo=SimpleUploadedFile('rut.exe', b'contenido arbitrario'),
                usuario=self.admin,
            )

        with self.assertRaises(ValidationError):
            cargar_documento_empresa(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
                archivo=self._pdf('rut-falso.pdf', b'no es un pdf'),
                usuario=self.admin,
            )

        with self.assertRaises(ValidationError):
            cargar_documento_empresa(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
                archivo=self._pdf('rut-grande.pdf', b'%PDF-' + b'x' * (8 * 1024 * 1024)),
                usuario=self.admin,
            )

    def test_usuario_sin_permiso_no_puede_cargar(self):
        usuario = get_user_model().objects.create_user(
            username='usuario_sin_permiso',
            password='clave-segura-test',
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            cargar_documento_empresa(
                empresa=self.empresa,
                tipo_documento=DocumentoEmpresa.TipoDocumento.RUT,
                archivo=self._pdf(),
                usuario=usuario,
            )

        self.assertFalse(DocumentoEmpresa.objects.exists())

    def test_archivo_no_tiene_url_publica(self):
        documento = self._cargar(DocumentoEmpresa.TipoDocumento.CEDULA_REPRESENTANTE_LEGAL)

        with self.assertRaises(ValueError):
            _ = documento.archivo.url

    def test_nombre_original_y_traversal_no_llegan_a_ruta_privada(self):
        documento = cargar_documento_empresa(
            empresa=self.empresa,
            tipo_documento=DocumentoEmpresa.TipoDocumento.CEDULA_REPRESENTANTE_LEGAL,
            archivo=self._pdf('../../900123456-representante.pdf'),
            usuario=self.admin,
        )
        ruta_relativa = Path(documento.archivo.name)
        ruta_absoluta = Path(documento.archivo.path).resolve()
        raiz_privada = Path(self._private_root).resolve()

        self.assertNotIn('900123456', documento.archivo.name)
        self.assertNotIn('representante', ruta_relativa.name)
        self.assertNotIn('..', ruta_relativa.parts)
        self.assertIn(raiz_privada, ruta_absoluta.parents)

    def test_descarga_admin_requiere_permiso_y_no_expone_ruta(self):
        documento = self._cargar(DocumentoEmpresa.TipoDocumento.CEDULA_REPRESENTANTE_LEGAL)
        url = reverse('admin:gestion_creditos_documentoempresa_descargar', args=[documento.pk])
        staff_sin_permiso = get_user_model().objects.create_user(
            username='staff_sin_permiso',
            password='clave-segura-test',
            is_staff=True,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        usuario_no_staff = get_user_model().objects.create_user(
            username='usuario_con_view',
            password='clave-segura-test',
        )
        usuario_no_staff.user_permissions.add(Permission.objects.get(
            codename='view_documentoempresa',
            content_type__app_label='gestion_creditos',
        ))
        self.client.force_login(usuario_no_staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        self.client.force_login(staff_sin_permiso)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        staff_autorizado = get_user_model().objects.create_user(
            username='staff_con_view',
            password='clave-segura-test',
            is_staff=True,
        )
        staff_autorizado.user_permissions.add(Permission.objects.get(
            codename='view_documentoempresa',
            content_type__app_label='gestion_creditos',
        ))
        self.client.force_login(staff_autorizado)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'].split(';')[0], 'attachment')
        self.assertNotIn(documento.archivo.name, response['Content-Disposition'])
        response.close()

    @override_settings(
        PRIVATE_DOCUMENTS_ROOT='C:/tmp/aprobado/media/documentos-privados',
        MEDIA_ROOT='C:/tmp/aprobado/media',
    )
    def test_check_rechaza_storage_privado_dentro_de_media(self):
        errores = [error for error in run_checks() if error.id == 'gestion_creditos.E002']

        self.assertEqual(len(errores), 1)

    def test_documento_privado_no_aparece_en_landing(self):
        documento = self._cargar(DocumentoEmpresa.TipoDocumento.CEDULA_REPRESENTANTE_LEGAL)

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, documento.archivo.name)
        self.assertNotContains(response, 'CEDULA_REPRESENTANTE_LEGAL')
        self.assertNotIn('documentos_empresa', response.context)

    def test_admin_empresa_renderiza_documentacion_sin_ruta_privada(self):
        documento = self._cargar(DocumentoEmpresa.TipoDocumento.RUT)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin:gestion_creditos_empresa_change', args=[self.empresa.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Documentación')
        self.assertContains(response, 'Ver historial documental')
        self.assertContains(response, 'Descargar de forma segura')
        self.assertNotContains(response, documento.archivo.name)

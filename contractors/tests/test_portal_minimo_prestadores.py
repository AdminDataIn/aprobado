from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from contractors.models import ContractorApplication, ContractorApplicationDocument
from gestion_creditos.models import Empresa


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
        self.empresa = Empresa.objects.create(
            nombre='Empresa Convenio',
            convenio_activo=True,
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

    def test_usuario_autenticado_crea_solicitud_basica_con_empresa_existente(self):
        self.client.force_login(self.usuario)
        response = self.client.post(
            '/solicitar/',
            {
                'escenario_credito': ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
                'tipo_documento': ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
                'numero_documento': '123456789',
                'nombres': 'Ana Maria',
                'apellidos': 'Perez Gomez',
                'celular': '3001234567',
                'correo': 'ana@example.com',
                'direccion': 'Calle 1 # 2-3',
                'cargo': 'Consultora',
                'empresa': self.empresa.id,
            },
            HTTP_HOST=self.host,
        )

        solicitud = ContractorApplication.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.usuario, self.usuario)
        self.assertEqual(solicitud.empresa, self.empresa)
        self.assertEqual(response['Location'], f'/solicitud/{solicitud.id}/documentos/')

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

    def _crear_solicitud(self, usuario):
        return ContractorApplication.objects.create(
            usuario=usuario,
            empresa=self.empresa,
            escenario_credito=ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento='123456789',
            nombres='Ana Maria',
            apellidos='Perez Gomez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1 # 2-3',
            cargo='Consultora',
        )

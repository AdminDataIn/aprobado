import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import Empresa


class LibranzaLandingMarketingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._create_company('DataIn', 'datain.png')
        self._create_company('Cluster Orinoco TIC', 'orinoco.png')
        self._create_company('Soll Ortodoncia', 'soll.png')
        self._create_company('Llano al Mundo', 'llano.png')

    def _create_company(self, nombre, filename):
        Empresa.objects.create(
            nombre=nombre,
            convenio_activo=True,
            logo=SimpleUploadedFile(filename, b'logo', content_type='image/png'),
        )

    def test_landing_renderiza_redes_y_empresas_confiables(self):
        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Empresas que confían en nosotros')
        self.assertContains(response, 'DataIn')
        self.assertContains(response, 'Cluster Orinoco TIC')
        self.assertContains(response, 'Soll Ortodoncia')
        self.assertContains(response, 'Llano al Mundo')
        self.assertContains(response, '/media/marketplace/logos/')
        self.assertNotContains(response, 'https://datain.pro/')
        self.assertNotContains(response, 'https://digitalpress.fra1.cdn.digitaloceanspaces.com/')
        self.assertContains(response, 'Conecta con Aprobado en nuestras redes')
        self.assertContains(response, 'Instagram')
        self.assertContains(response, 'Facebook')
        self.assertContains(response, 'Espacio reservado para testimonios')

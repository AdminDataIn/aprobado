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

    def _logo_file(self, filename='logo.png'):
        return SimpleUploadedFile(filename, b'logo', content_type='image/png')

    def test_landing_renderiza_logos_desde_media_para_empresas_activas(self):
        Empresa.objects.create(nombre='Datain', convenio_activo=True, logo=self._logo_file('datain.png'))
        Empresa.objects.create(nombre='Cluster Orinoco TIC', convenio_activo=True, logo=self._logo_file('orinoco.png'))
        Empresa.objects.create(nombre='Soll Ortodoncia', convenio_activo=True, logo=self._logo_file('soll.png'))
        Empresa.objects.create(nombre='Llano al Mundo', convenio_activo=True, logo=self._logo_file('llano.png'))

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ellos confiaron en nosotros')
        self.assertContains(response, 'libranza-company-logo')
        self.assertContains(response, '/media/marketplace/logos/')
        self.assertContains(response, 'Datain')
        self.assertContains(response, 'Cluster Orinoco TIC')
        self.assertNotContains(response, 'https://datain.pro/')
        self.assertNotContains(response, 'https://digitalpress.fra1.cdn.digitaloceanspaces.com/')

    def test_landing_renderiza_fallback_para_empresas_activas_sin_logo(self):
        Empresa.objects.create(nombre='Sin Logo', convenio_activo=True)
        Empresa.objects.create(nombre='Sin Convenio', convenio_activo=False, logo=self._logo_file('inactive.png'))

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sin Logo')
        self.assertContains(response, 'libranza-company-fallback')
        self.assertNotContains(response, 'Sin Convenio')

    def test_landing_renderiza_bloques_visuales_de_paridad(self):
        Empresa.objects.create(nombre='Aliado Demo', convenio_activo=True, logo=self._logo_file('aliado.png'))

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Prestador de Servicios')
        self.assertContains(response, 'https://contratistas.aprobado.com.co/solicitar/')
        self.assertContains(response, 'Presencia nacional')
        self.assertContains(response, 'maps/colombia.geo.json')
        self.assertContains(response, 'Quiénes nos respaldan')
        self.assertContains(response, 'Seguros SURA')
        self.assertNotContains(response, 'Testimonios')
        self.assertNotContains(response, 'Solicitar adelanto')

    def test_landing_renderiza_datain_y_conserva_respaldos_institucionales(self):
        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'DATAIN')
        self.assertContains(response, 'Respaldo tecnológico y desarrollo de software.')
        self.assertContains(response, 'images/respaldos/datain.png')
        self.assertContains(response, 'DataCrédito Experian')
        self.assertContains(response, 'FiGarantías')
        self.assertContains(response, 'Orinoco TIC')
        self.assertContains(response, 'Seguros SURA')

    def test_logo_acepta_proporcion_estrecha_y_svg(self):
        empresa_svg = Empresa(
            nombre='Empresa SVG',
            convenio_activo=True,
            logo=SimpleUploadedFile(
                'marketplace.svg',
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"></svg>',
                content_type='image/svg+xml',
            ),
        )
        empresa_svg.full_clean()
        empresa_svg.save()

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'marketplace.svg')
        self.assertContains(response, 'libranza-company-logo')

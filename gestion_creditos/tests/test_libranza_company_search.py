from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.forms import CreditoLibranzaForm
from gestion_creditos.models import Empresa


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class LibranzaCompanySearchTests(TestCase):
    def test_busca_empresas_con_convenio_activo_por_nombre(self):
        empresa = Empresa.objects.create(
            nombre='FERTOBRA SAS',
            razon_social='FERTOBRA SAS',
            nit='901123456',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        Empresa.objects.create(
            nombre='FERTOBRA Marketplace',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA,
        )

        response = self.client.get(
            reverse('libranza:buscar_empresas'),
            {'q': 'FERTOBRA'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], empresa.id)
        self.assertEqual(results[0]['nombre'], 'FERTOBRA SAS')

    def test_no_devuelve_empresas_sin_convenio_activo(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Inactiva',
            convenio_activo=False,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )

        response = self.client.get(
            reverse('libranza:buscar_empresas'),
            {'q': 'Empresa Inactiva'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'], [])

        form = CreditoLibranzaForm(data={'empresa': empresa.pk})
        self.assertFalse(form.is_valid())
        self.assertIn('empresa', form.errors)

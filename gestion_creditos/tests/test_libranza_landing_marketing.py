from django.test import TestCase
from django.urls import reverse


class LibranzaLandingMarketingTests(TestCase):
    def test_landing_renderiza_redes_y_empresas_confiables(self):
        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Empresas que confían en nosotros')
        self.assertContains(response, 'DataIn')
        self.assertContains(response, 'Cluster Orinoco TIC')
        self.assertContains(response, 'Soll Ortodoncia')
        self.assertContains(response, 'Llano al Mundo')
        self.assertContains(response, 'Conecta con Aprobado en nuestras redes')
        self.assertContains(response, 'Instagram')
        self.assertContains(response, 'Facebook')
        self.assertContains(response, 'Espacio reservado para testimonios')

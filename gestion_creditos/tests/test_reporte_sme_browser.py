import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from gestion_creditos.tests.test_reporte_sme import CierreSMEFixture, fecha


@skipUnless(os.getenv('SME_PLAYWRIGHT_NODE'), 'Navegador SME opcional')
class CierreSMENavegadorTest(CierreSMEFixture, StaticLiveServerTestCase):
    def setUp(self):
        self.preparar_usuarios()
        self.credito(fecha(10))
        self.client.force_login(self.operador)

    def flujo(self, ancho):
        resultado = subprocess.run(
            [os.environ['SME_PLAYWRIGHT_NODE'], str(Path(__file__).parent / 'browser' / 'test_cartera_sme.cjs')],
            input=json.dumps({
                'url': self.live_server_url + reverse('gestion:cartera_sme'),
                'sesion': settings.SESSION_COOKIE_NAME,
                'cookie': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                'ancho': ancho,
            }),
            text=True, capture_output=True, timeout=75,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_escritorio(self):
        self.flujo(1280)

    def test_movil(self):
        self.flujo(390)

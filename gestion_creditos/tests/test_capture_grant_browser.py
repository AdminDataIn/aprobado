"""Opt-in browser integration against an isolated Django test DB, never the local DB."""
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import Client, override_settings

from gestion_creditos.models import Credito, CreditoLibranza, SesionCapturaDocumental, EventoCapturaDocumental
from gestion_creditos.tests.test_captura_documental import CapturaFixture


@skipUnless(os.environ.get('RUN_CAPTURE_GRANT_BROWSER') == '1', 'Requiere Playwright/Node y opt-in explicito.')
@override_settings(ALLOWED_HOSTS=['localhost', '127.0.0.1', 'testserver'], SECURE_SSL_REDIRECT=False,
                   SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, DEBUG=True)
class CaptureGrantBrowserTest(CapturaFixture, StaticLiveServerTestCase):
    def test_dos_navegadores_https_con_backend_real(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        cache.clear()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        folder = Path(self.temporary.name)
        key_path, cert_path = folder / 'test-key.pem', folder / 'test-cert.pem'
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pc = Client()
        pc.force_login(self.usuario)
        config = {'target': self.live_server_url, 'key': str(key_path), 'cert': str(cert_path),
                  'sessionName': settings.SESSION_COOKIE_NAME, 'pcSession': pc.cookies[settings.SESSION_COOKIE_NAME].value}
        script = Path(__file__).parent / 'browser' / 'capture_grant_live.cjs'
        result = subprocess.run(['node', str(script)], input=json.dumps(config), text=True, capture_output=True,
                                timeout=180, cwd=settings.BASE_DIR)
        # The harness emits only stage names and counts, never cookies/URLs/tokens in failures.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('cross-device HTTPS OK', result.stdout)
        self.assertEqual(SesionCapturaDocumental.objects.filter(estado='FINALIZADA').count(), 1)
        self.assertEqual(EventoCapturaDocumental.objects.filter(evento='FINALIZACION_DELEGADA').count(), 1)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

"""Build TLS and vhost fixtures, then test ID-00 inside a networkless Nginx."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


NGINX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NGINX))
from id00_preparar_vhost import ALIAS, HOSTS, preparar  # noqa: E402


def tls(path):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    dn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ID00 synthetic Nginx')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(dn).issuer_name(dn).public_key(llave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h.decode('ascii')) for h in sorted(HOSTS | {ALIAS})]),
                           critical=False).sign(llave, hashes.SHA256()))
    (path / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (path / 'key.pem').write_bytes(llave.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


if __name__ == '__main__':
    with TemporaryDirectory(prefix='id00-fixtures-') as tmp:
        root = Path(tmp)
        tls(root)
        base = (NGINX / 'tests' / 'id00.vhosts.conf').read_bytes()
        (root / 'base.conf').write_bytes(base)
        (root / 'candidato.conf').write_bytes(preparar(base))
        (root / 'id00.sh').write_bytes((NGINX / 'tests' / 'id00.sh').read_bytes().replace(b'\r\n', b'\n'))
        alias_ajeno = base.replace(b'www.aprobado.com.co', b'alias-no-acreditado.invalid')
        try:
            preparar(alias_ajeno)
        except ValueError:
            pass
        else:
            raise AssertionError('El plan debe detenerse si cambia un nombre del vhost')
        subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
            '--tmpfs', '/var/log/nginx', '--env', 'ID00_TEST_CONTAINER=1',
            '--mount', f'type=bind,source={NGINX},target=/id00,readonly',
            '--mount', f'type=bind,source={root},target=/fixtures,readonly',
            '--mount', f'type=bind,source={NGINX / "aprobado_documental_log_server.conf"},'
                       'target=/etc/nginx/snippets/aprobado_documental_log_server.conf,readonly',
            '--entrypoint', '/bin/sh', 'nginx:1.24.0-alpine', '/fixtures/id00.sh'], check=True)

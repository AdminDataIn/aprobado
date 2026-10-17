"""Compare only three preexisting UI assertions, with sanitized output and memory DB."""
from contextlib import ExitStack
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import gettempdir, TemporaryDirectory
import unittest
from unittest.mock import patch


root = Path(sys.argv[1]).resolve()
assert (root / 'manage.py').is_file()
sys.path.insert(0, str(root))
sys.path.append(gettempdir())
os.chdir(root)
os.environ.update(DJANGO_SETTINGS_MODULE='aprobado_uat_sintetica_settings',
                  DATABASE_URL='', USE_SQLITE='true',
                  DATACREDITO_ENABLED='False', DATACREDITO_REAL_ENABLED='False',
                  PRESTADORES_AUTO_EVALUACION_ENABLED='False')
logging.disable(logging.CRITICAL)


def bloquear(*args, **kwargs):
    raise RuntimeError('externos_bloqueados')


class ResultadoSeguro(unittest.TextTestResult):
    def addFailure(self, test, err):
        self.failures.append((test, 'AssertionError_sanitizado'))
        self.stream.write('F')

    def addError(self, test, err):
        self.errors.append((test, 'Error_sanitizado_' + err[0].__name__))
        self.stream.write('E')


with ExitStack() as stack:
    stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
    for target in ('requests.sessions.Session.request', 'httpx.Client.send',
                   'httpx.AsyncClient.send', 'socket.socket.connect',
                   'celery.app.task.Task.apply_async', 'celery.app.base.Celery.send_task',
                   'django.core.mail.message.EmailMessage.send'):
        stack.enter_context(patch(target, side_effect=bloquear))
    import django
    django.setup()
    from django.conf import settings
    from django.test import override_settings
    from django.test.runner import DiscoverRunner
    assert settings.DATABASES['default']['NAME'] == ':memory:'
    storage = stack.enter_context(TemporaryDirectory(prefix='aprobado-ui-legacy-'))
    stack.enter_context(override_settings(
        MEDIA_ROOT=str(Path(storage) / 'media'),
        PRIVATE_DOCUMENTS_ROOT=str(Path(storage) / 'privado'),
    ))

    class RunnerSeguro(DiscoverRunner):
        def get_resultclass(self):
            return ResultadoSeguro

    prefijo = 'contractors.tests.test_revision_manual_prestador.RevisionManualPrestadorTest.'
    labels = [prefijo + name for name in (
        'test_error_controlado_publico_no_expone_detalle_tecnico',
        'test_mi_credito_bloqueado_no_expone_motivo_interno',
        'test_mi_credito_preaprobado_no_expone_score_ni_proveedor',
    )]
    with override_settings(ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
                           SECURE_SSL_REDIRECT=False):
        fallos = RunnerSeguro(verbosity=1, interactive=False).run_tests(labels)
    print(json.dumps({'pruebas':3, 'fallos_o_errores':fallos, 'http':0,
                      'base':'SQLite_en_memoria', 'pii_en_salida':False}))
    raise SystemExit(0 if fallos == 3 else 1)

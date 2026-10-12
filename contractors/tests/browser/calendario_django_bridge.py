"""Disposable Django test client for the WebKit calendar regression, JSON lines only."""
import base64
from contextlib import ExitStack, redirect_stdout
from datetime import date
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def blocked(*args, **kwargs):
    raise AssertionError('External integration forbidden in browser regression')


def emit(value):
    print(json.dumps(value), flush=True)


def cleanup(callback, *args):
    with redirect_stdout(sys.stderr):
        callback(*args)


def main():
    import os
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aprobado_web.settings'
    with ExitStack() as stack:
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        for target in ('requests.sessions.Session.request', 'httpx.Client.send',
                       'httpx.AsyncClient.send', 'urllib.request.urlopen', 'socket.socket.connect',
                       'celery.app.task.Task.apply_async', 'celery.app.base.Celery.send_task'):
            stack.enter_context(patch(target, side_effect=blocked))
        from aprobado_web import settings as project
        project.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:', 'TEST': {'NAME': ':memory:'}}}
        project.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        project.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
        project.CELERY_BROKER_URL = 'memory://'
        project.CELERY_RESULT_BACKEND = 'cache+memory://'
        project.DATACREDITO_ENABLED = project.DATACREDITO_REAL_ENABLED = False
        project.PRESTADORES_AUTO_EVALUACION_ENABLED = False
        project.CONTRACTORS_CONTRACT_AI_ENABLED = False
        project.OPENAI_API_KEY = ''
        project.SECURE_SSL_REDIRECT = False
        project.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
        from django.conf import settings
        # Celery can initialize LazySettings while importing the project package.
        for name in ('DATABASES', 'CACHES', 'EMAIL_BACKEND', 'CELERY_BROKER_URL',
                     'CELERY_RESULT_BACKEND', 'DATACREDITO_ENABLED', 'DATACREDITO_REAL_ENABLED',
                     'PRESTADORES_AUTO_EVALUACION_ENABLED', 'CONTRACTORS_CONTRACT_AI_ENABLED',
                     'OPENAI_API_KEY', 'SECURE_SSL_REDIRECT', 'PASSWORD_HASHERS'):
            setattr(settings, name, getattr(project, name))
        assert settings.DATABASES['default']['NAME'] == ':memory:'
        import django
        django.setup()
        stack.enter_context(patch('django.utils.timezone.localdate', return_value=date(2026, 10, 11)))
        assert settings.DATABASES['default']['NAME'] == ':memory:'
        from django.test.runner import DiscoverRunner
        from contractors.models import ContractorApplication
        from contractors.tests.test_portal_minimo_prestadores import PortalMinimoPrestadoresTest
        from contractors.tests.contrato_uat_fixture import pdf_uat_calendario, pdf_uat_calendario_v2
        runner = DiscoverRunner(verbosity=0, interactive=False)
        with redirect_stdout(sys.stderr):
            runner.setup_test_environment()
            stack.callback(cleanup, runner.teardown_test_environment)
            databases = runner.setup_databases()
            stack.callback(cleanup, runner.teardown_databases, databases)
            PortalMinimoPrestadoresTest.setUpClass()
            stack.callback(cleanup, PortalMinimoPrestadoresTest.tearDownClass)
            case = PortalMinimoPrestadoresTest('test_calendario_uat_sin_adenda_bloquea_y_reabre_documentos')
            case._pre_setup()
            stack.callback(cleanup, case._post_teardown)
            stack.callback(cleanup, case.doCleanups)
            case.setUp()
            stack.callback(cleanup, case.tearDown)
            response = case.client.post('/solicitar/', case._payload_calendario_uat(), HTTP_HOST=case.host)
            assert response.status_code == 302
            solicitud = ContractorApplication.objects.get()
        adenda = (pdf_uat_calendario_v2() if os.environ.get('CALENDAR_VARIANT') == 'dia_fijo'
                  else pdf_uat_calendario(adenda=(
            '\nADENDA SINTETICA: Cada pago se realiza dentro de los primeros cinco (5) '
            'dias habiles del mes siguiente al periodo causado.')))
        emit({'ready': True, 'solicitud_id': solicitud.pk,
              'adenda': base64.b64encode(adenda.read()).decode('ascii')})
        for line in sys.stdin:
            command = json.loads(line)
            if command.get('stop'):
                break
            if command.get('integrity'):
                from gestion_creditos.models import Credito
                from integrations.models import ConsultaDatacreditoSnapshot
                from gestion_creditos.services.horizonte_contractual import extraer_regla_pago
                solicitud.refresh_from_db()
                emit({'applications': ContractorApplication.objects.count(),
                      'documents': solicitud.documentos.count(), 'credits': Credito.objects.count(),
                      'audits': solicitud.auditorias_predecision.count(),
                      'snapshots': ConsultaDatacreditoSnapshot.objects.count(),
                      'regla_pago': extraer_regla_pago(solicitud.evidencia_forma_pago),
                      'simulation_registered': solicitud.simulada_en is not None})
                continue
            try:
                with redirect_stdout(sys.stderr):
                    headers = {'HTTP_HOST': case.host}
                    for name in ('x-csrftoken', 'origin', 'referer', 'x-requested-with'):
                        if name in command.get('headers', {}):
                            headers['HTTP_' + name.upper().replace('-', '_')] = command['headers'][name]
                    if 'multipart' in command:
                        from django.core.files.uploadedfile import SimpleUploadedFile
                        data = {}
                        for field in command['multipart']:
                            value = field['value']
                            if isinstance(value, dict):
                                if not value['name']:
                                    continue
                                value = SimpleUploadedFile(value['name'], base64.b64decode(value['data']),
                                                           content_type=value['type'])
                            data[field['name']] = value
                        response = case.client.post(command['path'], data, secure=True, **headers)
                    else:
                        response = case.client.generic(command['method'], command['path'], secure=True,
                            data=base64.b64decode(command.get('body', '')),
                            content_type=command.get('headers', {}).get('content-type', 'application/octet-stream'),
                            **headers)
                emit({'status': response.status_code, 'headers': {
                    key: response[key] for key in ('Content-Type', 'Location') if key in response},
                    'body': base64.b64encode(response.content).decode('ascii')})
            except Exception as exc:
                emit({'error': type(exc).__name__})


if __name__ == '__main__':
    main()

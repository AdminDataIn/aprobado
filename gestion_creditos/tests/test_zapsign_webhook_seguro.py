"""Synthetic callbacks only. Remote transport and all deliveries are intercepted."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialEstado, Pagare, ZapSignWebhookLog
from gestion_creditos.services.zapsign_client import ZapSignAPIError, ZapSignClient


CONFIG = dict(
    ZAPSIGN_WEBHOOK_SECRET='secreto-sintetico-webhook', ZAPSIGN_WEBHOOK_HEADER='X-ZapSign-Secret',
    ZAPSIGN_PRESTADORES_WEBHOOK_SECRET='secreto-sintetico-prestadores',
    ZAPSIGN_PRESTADORES_WEBHOOK_HEADER='X-ZapSign-Secret',
    ZAPSIGN_API_TOKEN='api-sintetica-no-utilizable', ZAPSIGN_ENVIRONMENT='sandbox',
    ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'], SECURE_SSL_REDIRECT=False,
    CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)


def documento_remoto(token, correo, **extra):
    return dict(token=token, status='signed', sandbox=True, deleted=False,
                signers=[dict(token='firmante-sintetico', status='signed', email=correo,
                              signed_at=timezone.now().isoformat())], **extra)


class FixtureFirma:
    def setUp(self):
        super().setUp()
        temporal = TemporaryDirectory(prefix='firma-segura-test-')
        self.addCleanup(temporal.cleanup)
        storage = override_settings(MEDIA_ROOT=temporal.name, PRIVATE_DOCUMENTS_ROOT=temporal.name)
        storage.enable()
        self.addCleanup(storage.disable)
        for target in ('requests.adapters.HTTPAdapter.send', 'celery.app.task.Task.apply_async'):
            guard = patch(target, side_effect=AssertionError('Red o cola real prohibida en firma sintetica'))
            guard.start()
            self.addCleanup(guard.stop)
        self.usuario = get_user_model().objects.create_user('firma-sintetica', email='titular@example.invalid')
        self.credito = Credito.objects.create(usuario=self.usuario, linea='LIBRANZA',
            estado='PENDIENTE_FIRMA', monto_solicitado=1000000, plazo_solicitado=3)
        empresa = Empresa.objects.create(nombre='EMPRESA SINTETICA', convenio_activo=True)
        self.detalle = CreditoLibranza.objects.create(credito=self.credito, empresa=empresa,
            nombres='PRUEBA', apellidos='SINTETICA', correo_electronico='solicitud@example.invalid')
        self.pagare = Pagare.objects.create(credito=self.credito,
            archivo_pdf=SimpleUploadedFile('prueba.pdf', b'%PDF-1.4 SOLO TEST'),
            zapsign_doc_token='documento-sintetico', estado='SENT', fecha_envio=timezone.now()-timedelta(minutes=2))
        self.payload = {'event_type': 'doc_signed', 'token': self.pagare.zapsign_doc_token,
                        'status': 'signed', 'name': 'PII_NO_PERSISTIR',
                        'signers': [{'document': 'PII_NO_PERSISTIR'}]}
        self.remoto = documento_remoto(self.pagare.zapsign_doc_token, self.detalle.correo_electronico)
        guard = patch('gestion_creditos.services.zapsign_client.descargar_pdf_firmado_pagare', return_value=None)
        guard.start()
        self.addCleanup(guard.stop)

    def post(self, *, payload=None, secret='secreto-sintetico-webhook', client=None, **headers):
        if secret is not None:
            headers['HTTP_X_ZAPSIGN_SECRET'] = secret
        return (client or self.client).post(reverse('zapsign_webhook'),
            data=json.dumps(self.payload if payload is None else payload),
            content_type='application/json', **headers)

    def sin_efectos(self):
        self.credito.refresh_from_db()
        self.pagare.refresh_from_db()
        self.assertEqual(self.credito.estado, 'PENDIENTE_FIRMA')
        self.assertEqual(self.pagare.estado, 'SENT')
        self.assertFalse(HistorialEstado.objects.filter(credito=self.credito).exists())


@override_settings(**CONFIG)
class WebhookZapSignSeguroTest(FixtureFirma, TestCase):
    def test_autenticacion_antes_del_json_sin_http_ni_persistencia(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for secreto in [None, '', 'incorrecto']:
                with self.subTest(secreto_presente=bool(secreto)):
                    self.assertEqual(self.post(secret=secreto).status_code, 403)
            api.assert_not_called()
        self.assertEqual(ZapSignWebhookLog.objects.count(), 0)
        self.sin_efectos()

    def test_configuracion_ausente_o_invalida_falla_cerrado(self):
        for kwargs in [dict(ZAPSIGN_WEBHOOK_SECRET=''),
                       dict(ZAPSIGN_WEBHOOK_SECRET='valor\n'), dict(ZAPSIGN_WEBHOOK_HEADER=''),
                       dict(ZAPSIGN_WEBHOOK_HEADER='Cookie')]:
            with self.subTest(config=list(kwargs)), override_settings(**kwargs), patch.object(ZapSignClient, 'consultar_documento') as api:
                self.assertEqual(self.post().status_code, 503)
                api.assert_not_called()
        self.sin_efectos()

    def test_comparacion_constante_y_header_authorization_compatible(self):
        import hmac
        with patch('gestion_creditos.services.zapsign_webhook.hmac.compare_digest', wraps=hmac.compare_digest) as comparar:
            self.assertEqual(self.post(secret='incorrecto').status_code, 403)
            self.assertEqual(comparar.call_count, 2)
            comparar.assert_any_call(b'incorrecto', b'secreto-sintetico-webhook')
        with override_settings(ZAPSIGN_WEBHOOK_HEADER='Authorization'), patch.object(
                ZapSignClient, 'consultar_documento', return_value=self.remoto):
            r = self.post(HTTP_AUTHORIZATION='Bearer secreto-sintetico-webhook')
        self.assertEqual(r.json()['status'], 'ok')

    def test_json_invalido_y_tokens_invalidos_no_consultan(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for payload in [[], None, {'token': '../secreto'}, {'token': ['valor']}, {'token': 'x', 'event_type': 'desconocido'}]:
                r = self.post(payload=payload if payload is not None else [])
                self.assertIn(r.status_code, [200, 400])
            api.assert_not_called()
        self.sin_efectos()

    def test_documento_inexistente_no_hace_http(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            r = self.post(payload={**self.payload, 'token': 'desconocido'})
            api.assert_not_called()
        self.assertEqual(r.json()['status'], 'document_not_found_ignored')
        self.sin_efectos()

    def test_doc_signed_de_un_firmante_no_cierra_documento_pendiente(self):
        self.remoto['status'] = 'pending'
        self.remoto['signers'].append(dict(token='segundo', status='new', email='otro@example.invalid'))
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto):
            r = self.post()
        self.assertEqual(r.json()['status'], 'remote_pending_ignored')
        self.sin_efectos()

    def test_estado_signed_con_firmantes_pendientes_falla_cerrado(self):
        self.remoto['signers'].append(dict(token='segundo', status='new', email='otro@example.invalid'))
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto):
            self.assertEqual(self.post().status_code, 503)
        self.sin_efectos()

    def test_firmado_verificado_preserva_libranza_y_replay_no_retrocede(self):
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post().json()['status'], 'ok')
            self.assertEqual(self.post().json()['status'], 'already_processed')
            self.assertEqual(self.post(payload={**self.payload, 'event_type': 'doc_refused'}).json()['status'], 'already_processed')
            api.assert_called_once()
        self.credito.refresh_from_db(); self.pagare.refresh_from_db()
        self.assertEqual(self.credito.estado, 'PENDIENTE_TRANSFERENCIA')
        self.assertEqual(self.pagare.estado, 'SIGNED')
        self.assertEqual(HistorialEstado.objects.filter(credito=self.credito).count(), 2)
        self.assertFalse(self.credito.fecha_desembolso)
        self.assertFalse(self.credito.tabla_amortizacion.exists())

    def test_rechazo_remoto_y_firma_tardia_no_reabre(self):
        self.remoto['status'] = 'recusado'
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post().json()['status'], 'refused_recorded')
            self.assertEqual(self.post().json()['status'], 'terminal_state_ignored')
            api.assert_called_once()
        self.pagare.refresh_from_db(); self.credito.refresh_from_db()
        self.assertEqual(self.pagare.estado, 'REFUSED')
        self.assertEqual(self.credito.estado, 'PENDIENTE_FIRMA')

    def test_respuesta_remota_invalida_no_cambia_estados(self):
        variantes = []
        for key, valor in [('token', 'otro-documento'), ('sandbox', False), ('deleted', True),
                           ('status', 'desconocido'), ('signers', []), ('signers', None)]:
            variantes.append({**deepcopy(self.remoto), key: valor})
        for key, valor in [('email', 'otro@example.invalid'), ('token', ''), ('signed_at', None),
                           ('signed_at', (timezone.now()+timedelta(days=1)).isoformat()),
                           ('signed_at', (timezone.now()-timedelta(days=1)).isoformat())]:
            datos=deepcopy(self.remoto); datos['signers'][0][key]=valor; variantes.append(datos)
        for datos in variantes:
            with self.subTest(caso=variantes.index(datos)), patch.object(ZapSignClient, 'consultar_documento', return_value=datos):
                self.assertEqual(self.post().status_code, 503)
                self.sin_efectos()

    def test_error_remoto_no_registra_texto_sensible(self):
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=ZapSignAPIError('TOKEN_PII_SECRETO')):
            self.assertEqual(self.post().status_code, 503)
        log=ZapSignWebhookLog.objects.get()
        self.assertFalse(log.processed)
        self.assertEqual(log.error_message, 'remote_verification_failed')
        self.assertNotIn('TOKEN_PII_SECRETO', str(log.__dict__))
        self.sin_efectos()

    def test_logs_y_evidencias_no_guardan_payload_credenciales_ni_tokens(self):
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto):
            self.post()
        self.pagare.refresh_from_db()
        log=ZapSignWebhookLog.objects.get()
        for valor in ['PII_NO_PERSISTIR', self.usuario.email, self.detalle.correo_electronico,
                      'documento-sintetico', CONFIG['ZAPSIGN_WEBHOOK_SECRET']]:
            self.assertNotIn(valor, str(log.__dict__)+str(self.pagare.evidencias))
        self.assertEqual(log.headers, {})
        self.assertTrue(log.doc_token.startswith('sha256:'))

    def test_api_sin_redirects_y_errores_controlados(self):
        for status in [200, 302, 401, 500]:
            response=Mock(status_code=status)
            response.raise_for_status.return_value=None
            response.json.return_value=self.remoto
            with self.subTest(http=status), patch('gestion_creditos.services.zapsign_client.requests.get', return_value=response) as get:
                r=self.post()
                self.assertEqual(r.status_code, 200 if status==200 else 503)
                self.assertIs(get.call_args.kwargs['allow_redirects'], False)
            if status==200:
                self.pagare.estado='SENT'; self.pagare.fecha_firma=None; self.pagare.save()
                self.credito.estado='PENDIENTE_FIRMA'; self.credito.save()

    def test_cambio_local_durante_http_impide_transicion(self):
        def remote(*args):
            CreditoLibranza.objects.filter(pk=self.detalle.pk).update(correo_electronico='cambio@example.invalid')
            return self.remoto
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remote):
            self.assertEqual(self.post().status_code, 409)
        self.sin_efectos()


@override_settings(**CONFIG)
@skipUnless(connection.vendor=='postgresql', 'Requiere PostgreSQL aislado')
class WebhookZapSignConcurrenciaTest(FixtureFirma, TransactionTestCase):
    def test_doble_callback_verifica_fuera_de_atomic_y_transicion_unica(self):
        barrera=Barrier(2)
        def remoto(*args):
            self.assertFalse(connection.in_atomic_block)
            barrera.wait(timeout=15)
            return self.remoto
        def ejecutar():
            close_old_connections()
            try:
                return self.post(client=Client()).json()['status']
            finally:
                close_old_connections()
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remoto):
            with ThreadPoolExecutor(max_workers=2) as pool:
                resultados=list(pool.map(lambda _: ejecutar(), range(2)))
        self.assertCountEqual(resultados, ['ok','already_processed'])
        self.assertEqual(HistorialEstado.objects.filter(credito=self.credito).count(), 2)
        self.assertEqual(ZapSignWebhookLog.objects.filter(processed=True).count(), 2)

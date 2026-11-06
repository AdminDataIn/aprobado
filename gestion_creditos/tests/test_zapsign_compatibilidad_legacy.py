"""Approved legacy exception never applies to Prestadores or malformed secrets."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings

from contractors.tests.test_webhook_firma_seguro import FixtureFirmaPrestador
from gestion_creditos.models import HistorialEstado, ZapSignWebhookLog
from gestion_creditos.services.zapsign_client import ZapSignAPIError, ZapSignClient
from gestion_creditos.tests.test_zapsign_receptores import callback
from gestion_creditos.tests.test_zapsign_webhook_seguro import CONFIG, FixtureFirma
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


COMPAT = {**CONFIG, 'ZAPSIGN_WEBHOOK_SECRET': None}


@override_settings(**COMPAT)
class CompatibilidadLegacyTest(FixtureFirma, TestCase):
    def test_settings_distingue_variable_ausente_y_vacia(self):
        source = Path(settings.BASE_DIR, 'aprobado_web', 'settings.py')
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'ZAPSIGN_WEBHOOK_SECRET'
                                  for target in node.targets))
        code = compile(ast.Module(body=[assignment], type_ignores=[]), str(source), 'exec')
        for environment, expected in [({}, None), ({'ZAPSIGN_WEBHOOK_SECRET': ''}, ''),
                                      ({'ZAPSIGN_WEBHOOK_SECRET': ' '}, ' ')]:
            with patch.dict(os.environ, environment, clear=True):
                namespace = {'os': os}
                exec(code, namespace)
            self.assertEqual(namespace['ZAPSIGN_WEBHOOK_SECRET'], expected)

    def test_ausente_permite_legacy_pero_verifica_remotamente_y_no_finge_auth(self):
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post(secret=None).json()['status'], 'ok')
            self.assertEqual(self.post(secret=None).json()['status'], 'already_processed')
            self.assertEqual(self.post(secret=None, payload={**self.payload, 'event_type': 'doc_refused'}).json()['status'],
                             'already_processed')
            api.assert_called_once()
        self.pagare.refresh_from_db()
        self.credito.refresh_from_db()
        self.assertEqual(self.pagare.estado, 'SIGNED')
        self.assertEqual(self.credito.estado, 'PENDIENTE_TRANSFERENCIA')
        self.assertEqual(HistorialEstado.objects.filter(credito=self.credito).count(), 2)
        self.assertFalse(self.credito.fecha_desembolso)
        self.assertFalse(self.credito.tabla_amortizacion.exists())
        for log in ZapSignWebhookLog.objects.all():
            self.assertFalse(log.signature_valid)
            self.assertEqual(log.payload['autenticacion'], 'legacy_sin_secreto')
            self.assertEqual(log.headers, {})
            self.assertNotIn('documento-sintetico', str(log.__dict__))

    def test_secretos_presentes_invalidos_no_activan_legacy(self):
        invalidos = ['', ' ', 'valor\n', '\x00', 'x'*513, False, 0, b'bytes', [], {}]
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for secret in invalidos:
                with self.subTest(tipo=type(secret).__name__), override_settings(ZAPSIGN_WEBHOOK_SECRET=secret):
                    for received in [None, 'incorrecto']:
                        self.assertEqual(self.post(secret=received).status_code, 503)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())
        self.sin_efectos()

    def test_secreto_configurado_exige_header_sin_downgrade(self):
        with override_settings(ZAPSIGN_WEBHOOK_SECRET=CONFIG['ZAPSIGN_WEBHOOK_SECRET']), patch.object(
                ZapSignClient, 'consultar_documento') as api:
            for received in [None, '', 'incorrecto', CONFIG['ZAPSIGN_PRESTADORES_WEBHOOK_SECRET']]:
                self.assertEqual(self.post(secret=received).status_code, 403)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())
        self.sin_efectos()

    def test_pendiente_o_firmante_incompleto_no_cierra(self):
        pending = {**deepcopy(self.remoto), 'status': 'pending'}
        incomplete = deepcopy(self.remoto)
        incomplete['signers'].append({'token': 'segundo-sintetico', 'status': 'new', 'email': 'otro@example.invalid'})
        for remote, expected in [(pending, 200), (incomplete, 503)]:
            with self.subTest(status=remote['status']), patch.object(ZapSignClient, 'consultar_documento', return_value=remote):
                self.assertEqual(self.post(secret=None).status_code, expected)
                self.sin_efectos()

    def test_error_remoto_legacy_no_cierra_ni_persiste_mensaje_libre(self):
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=ZapSignAPIError('SENSIBLE_SINTETICO')):
            self.assertEqual(self.post(secret=None).status_code, 503)
        log = ZapSignWebhookLog.objects.get()
        self.assertFalse(log.signature_valid)
        self.assertFalse(log.processed)
        self.assertEqual(log.error_message, 'remote_verification_failed')
        self.assertNotIn('SENSIBLE_SINTETICO', str(log.__dict__))
        self.sin_efectos()

    def test_rechazo_legacy_no_se_reabre_por_firma_tardia(self):
        self.remoto['status'] = 'recusado'
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post(secret=None).json()['status'], 'refused_recorded')
            self.assertEqual(self.post(secret=None).json()['status'], 'terminal_state_ignored')
            api.assert_called_once()
        self.pagare.refresh_from_db()
        self.assertEqual(self.pagare.estado, 'REFUSED')
        self.assertFalse(HistorialEstado.objects.filter(credito=self.credito).exists())

    def test_anonimo_en_prestadores_siempre_rechazado(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for legacy in [None, '', CONFIG['ZAPSIGN_WEBHOOK_SECRET']]:
                with override_settings(ZAPSIGN_WEBHOOK_SECRET=legacy):
                    self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico', secret='').status_code, 403)
                    self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico',
                                              secret=CONFIG['ZAPSIGN_WEBHOOK_SECRET']).status_code, 403)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())
        self.sin_efectos()

    def test_prestadores_sin_secreto_o_invalido_no_tiene_excepcion(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for secret in [None, '', ' ', False, 0, 'valor\n']:
                with self.subTest(tipo=type(secret).__name__), override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=secret):
                    self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico').status_code, 503)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())

    def test_legacy_anonimo_documento_ajeno_no_hace_get(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            self.assertEqual(self.post(secret=None, payload={**self.payload, 'token': 'educativo-ajeno-sintetico'}).json()['status'],
                             'document_not_found_ignored')
            api.assert_not_called()
        self.sin_efectos()


@override_settings(**{**CONFIGURACION_DATACREDITO_PRUEBA, **COMPAT})
class CompatibilidadPrestadoresTest(FixtureFirmaPrestador, TestCase):
    def test_propio_autenticado_funciona_sin_configurar_libranza(self):
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post().json()['status'], 'ok')
            self.assertEqual(self.post().json()['status'], 'already_processed')
            api.assert_called_once()
        log = ZapSignWebhookLog.objects.first()
        self.assertTrue(log.signature_valid)
        self.assertNotIn('autenticacion', log.payload)
        self.formal.refresh_from_db()
        self.assertEqual(self.formal.estado, 'FIRMADO')
        self.assertFalse(self.formal.credito.fecha_desembolso)

    def test_legacy_sin_auth_no_puede_transicionar_documento_prestadores(self):
        antes = HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count()
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for event in ['doc_signed', 'doc_refused']:
                response = callback(self.client, 'libranza', 'documento-prestador-sintetico', secret='', event=event)
                self.assertEqual(response.json()['status'], 'product_mismatch_ignored')
            api.assert_not_called()
        self.sin_cierre()
        self.assertEqual(HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count(), antes)
        self.assertFalse(ZapSignWebhookLog.objects.filter(signature_valid=True).exists())


@override_settings(**COMPAT)
@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL aislado')
class CompatibilidadLegacyConcurrenciaTest(FixtureFirma, TransactionTestCase):
    def test_doble_legacy_sin_header_y_cruzados_no_duplica_transicion(self):
        barrier = Barrier(2)
        def remote(*args):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=15)
            return self.remoto
        def execute(receptor):
            close_old_connections()
            try:
                if receptor == 'libranza':
                    return self.post(client=Client(), secret=None).json()['status']
                return callback(Client(), receptor, self.pagare.zapsign_doc_token).json()['status']
            finally:
                close_old_connections()
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remote) as api:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(execute, ['libranza', 'prestadores', 'libranza', 'prestadores']))
            self.assertEqual(api.call_count, 2)
        self.assertCountEqual(results, ['ok', 'already_processed', 'product_mismatch_ignored', 'product_mismatch_ignored'])
        self.assertEqual(HistorialEstado.objects.filter(credito=self.credito).count(), 2)
        self.assertEqual(ZapSignWebhookLog.objects.filter(payload__receptor='libranza', signature_valid=False).count(), 2)

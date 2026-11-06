"""Route boundaries use persisted ownership, not claims inside the callback."""
from concurrent.futures import ThreadPoolExecutor
import json
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from contractors.models import FormalizacionCreditoPrestador
from contractors.tests.test_webhook_firma_seguro import FixtureFirmaPrestador
from gestion_creditos.models import HistorialEstado, Pagare, ZapSignWebhookLog
from gestion_creditos.services.zapsign_client import ZapSignClient
from gestion_creditos.tests.test_zapsign_webhook_seguro import CONFIG, FixtureFirma
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


def callback(client, receptor, token, *, secret=None, event='doc_signed', **claims):
    name = 'zapsign_webhook' if receptor == 'libranza' else 'zapsign_prestadores_webhook'
    key = 'ZAPSIGN_WEBHOOK_SECRET' if receptor == 'libranza' else 'ZAPSIGN_PRESTADORES_WEBHOOK_SECRET'
    return client.post(reverse(name), content_type='application/json',
        data=json.dumps({'token': token, 'event': event, **claims}),
        HTTP_X_ZAPSIGN_SECRET=CONFIG[key] if secret is None else secret)


@override_settings(**CONFIG)
class ReceptorLibranzaTest(FixtureFirma, TestCase):
    def test_ambas_rutas_rechazan_sin_header_antes_de_parsear_o_auditar(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for name in ['zapsign_webhook', 'zapsign_prestadores_webhook']:
                r=self.client.post(reverse(name), data='not-json', content_type='application/json')
                self.assertEqual(r.status_code, 403)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())
        self.sin_efectos()

    def test_rutas_solo_permiten_post(self):
        for name in ['zapsign_webhook', 'zapsign_prestadores_webhook']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
        self.assertFalse(ZapSignWebhookLog.objects.exists())

    def test_ambas_rutas_rechazan_el_secreto_del_otro_receptor(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for receptor, otro in [('libranza', 'ZAPSIGN_PRESTADORES_WEBHOOK_SECRET'),
                                   ('prestadores', 'ZAPSIGN_WEBHOOK_SECRET')]:
                self.assertEqual(callback(self.client, receptor, 'documento-sintetico',
                                         secret=CONFIG[otro]).status_code, 403)
            api.assert_not_called()
        self.assertFalse(ZapSignWebhookLog.objects.exists())
        self.sin_efectos()

    def test_secretos_iguales_fallan_cerrado_en_ambas_rutas(self):
        with override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=CONFIG['ZAPSIGN_WEBHOOK_SECRET']):
            for receptor in ['libranza', 'prestadores']:
                self.assertEqual(callback(self.client, receptor, 'documento-sintetico').status_code, 503)
        self.assertFalse(ZapSignWebhookLog.objects.exists())

    def test_prestadores_sin_configuracion_no_usa_fallback_legacy(self):
        with override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=''):
            self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico').status_code, 503)
            self.assertEqual(self.post(secret='incorrecto').status_code, 403)
        self.sin_efectos()

    def test_header_propio_y_configuracion_invalida(self):
        for header in ['', 'Cookie', 'X-Invalid\nHeader']:
            with self.subTest(header_valido=False), override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_HEADER=header):
                self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico').status_code, 503)
        with override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_HEADER='X-Prestadores-Secret'):
            self.assertEqual(callback(self.client, 'prestadores', 'documento-sintetico').status_code, 403)
            r=self.client.post(reverse('zapsign_prestadores_webhook'),
                data=json.dumps({'token':'documento-sintetico','event':'doc_signed'}),
                content_type='application/json',
                HTTP_X_PRESTADORES_SECRET=CONFIG['ZAPSIGN_PRESTADORES_WEBHOOK_SECRET'])
            self.assertEqual(r.json()['status'], 'product_mismatch_ignored')
        self.sin_efectos()

    def test_libranza_en_ruta_prestadores_ignorada_sin_http_ni_transicion(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for event in ['doc_signed', 'doc_refused']:
                r=callback(self.client, 'prestadores', self.pagare.zapsign_doc_token, event=event,
                           producto='prestadores', external_id='dato-no-confiable')
                self.assertEqual(r.json()['status'], 'product_mismatch_ignored')
            api.assert_not_called()
        self.sin_efectos()
        for audit in ZapSignWebhookLog.objects.all():
            self.assertEqual(audit.payload['receptor'], 'prestadores')
            self.assertEqual(audit.payload['producto'], 'libranza')
            self.assertTrue(audit.processed)

    def test_documento_educativo_o_desconocido_ignorado_por_ambas_rutas(self):
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for receptor in ['libranza', 'prestadores']:
                for event in ['doc_signed', 'doc_refused']:
                    r=callback(self.client, receptor, 'documento-externo-sintetico', event=event)
                    self.assertEqual(r.json()['status'], 'document_not_found_ignored')
            api.assert_not_called()
        self.sin_efectos()

    def test_legacy_historico_no_necesita_formalizacion_ni_backfill(self):
        self.assertFalse(FormalizacionCreditoPrestador.objects.exists())
        with override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=''), patch.object(
                ZapSignClient, 'consultar_documento', return_value=self.remoto):
            self.assertEqual(self.post().json()['status'], 'ok')
        self.pagare.refresh_from_db()
        self.assertEqual(self.pagare.estado, 'SIGNED')


@override_settings(**{**CONFIGURACION_DATACREDITO_PRUEBA, **CONFIG})
class ReceptorPrestadoresTest(FixtureFirmaPrestador, TestCase):
    def test_token_raw_opcional_contradictorio_falla_cerrado(self):
        Pagare.objects.filter(pk=self.formal.pagare_id).update(zapsign_doc_token='otro-documento-sintetico')
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            self.assertEqual(self.post().status_code, 409)
            api.assert_not_called()
        self.sin_cierre()

    def test_colision_con_otro_pagare_no_redirige_una_transicion(self):
        from gestion_creditos.models import Credito
        ajeno=Credito.objects.create(usuario=self.usuario, linea='LIBRANZA',
            estado='PENDIENTE_FIRMA', monto_solicitado=1000000, plazo_solicitado=3)
        pagare=Pagare.objects.create(credito=ajeno, archivo_pdf='sintetico-no-leer.pdf',
            estado='SENT', zapsign_doc_token='documento-prestador-sintetico')
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            self.assertEqual(self.post().status_code, 409)
            api.assert_not_called()
        self.sin_cierre()
        pagare.refresh_from_db()
        self.assertEqual(pagare.estado, 'SENT')

    def test_credito_libranza_persistido_es_prestador_y_no_transiciona_en_legacy(self):
        self.assertEqual(self.formal.credito.linea, 'LIBRANZA')
        self.assertFalse(self.formal.pagare.zapsign_doc_token)
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            for event in ['doc_signed', 'doc_refused']:
                r=callback(self.client, 'libranza', 'documento-prestador-sintetico', event=event,
                           producto='libranza')
                self.assertEqual(r.json()['status'], 'product_mismatch_ignored')
            api.assert_not_called()
        self.sin_cierre()

    def test_hash_ausente_no_reclasifica_prestador_como_legacy(self):
        Pagare.objects.filter(pk=self.formal.pagare_id).update(zapsign_doc_token='documento-prestador-sintetico')
        FormalizacionCreditoPrestador.objects.filter(pk=self.formal.pk).update(proveedor_document_id_hash=None)
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            self.assertEqual(callback(self.client, 'libranza', 'documento-prestador-sintetico').json()['status'],
                             'product_mismatch_ignored')
            self.assertEqual(self.post().status_code, 409)
            api.assert_not_called()
        self.sin_cierre()

    def test_formalizacion_ausente_con_origen_persistido_no_cae_en_legacy(self):
        Pagare.objects.filter(pk=self.formal.pagare_id).update(zapsign_doc_token='documento-prestador-sintetico')
        FormalizacionCreditoPrestador.objects.filter(pk=self.formal.pk).delete()
        with patch.object(ZapSignClient, 'consultar_documento') as api:
            self.assertEqual(callback(self.client, 'libranza', 'documento-prestador-sintetico').json()['status'],
                             'product_mismatch_ignored')
            self.assertEqual(self.post().status_code, 409)
            api.assert_not_called()
        self.assertEqual(Pagare.objects.get(pk=self.formal.pagare_id).estado, 'SENT')

    def test_contexto_cambia_durante_verificacion_remota_falla_cerrado(self):
        def remoto(*args):
            FormalizacionCreditoPrestador.objects.filter(pk=self.formal.pk).update(proveedor_document_id_hash=None)
            return self.remoto
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remoto):
            self.assertEqual(self.post().status_code, 409)
        self.sin_cierre()

    def test_rechazo_propio_no_origina_ni_cierra_y_no_se_reabre_en_otro_receptor(self):
        self.remoto['status']='recusado'
        with patch.object(ZapSignClient, 'consultar_documento', return_value=self.remoto) as api:
            self.assertEqual(self.post(event='doc_refused').json()['status'], 'refused_recorded')
            self.assertEqual(callback(self.client, 'libranza', 'documento-prestador-sintetico').json()['status'],
                             'product_mismatch_ignored')
            self.assertEqual(self.post().json()['status'], 'terminal_state_ignored')
            api.assert_called_once()
        self.formal.refresh_from_db()
        self.assertNotEqual(self.formal.estado, 'FIRMADO')
        self.assertFalse(self.formal.credito.fecha_desembolso)


@override_settings(**CONFIG)
@skipUnless(connection.vendor=='postgresql', 'Requiere PostgreSQL aislado')
class ReceptoresConcurrenciaTest(FixtureFirma, TransactionTestCase):
    def test_eventos_cruzados_y_duplicados_concurren_sin_doble_transicion(self):
        from threading import Barrier
        barrera=Barrier(2)
        def remoto(*args):
            self.assertFalse(connection.in_atomic_block)
            barrera.wait(timeout=15)
            return self.remoto
        def ejecutar(receptor):
            close_old_connections()
            try:
                return callback(Client(), receptor, self.pagare.zapsign_doc_token).json()['status']
            finally:
                close_old_connections()
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remoto) as api:
            with ThreadPoolExecutor(max_workers=4) as pool:
                resultados=list(pool.map(ejecutar, ['libranza','prestadores','libranza','prestadores']))
            self.assertEqual(api.call_count, 2)
        self.assertCountEqual(resultados, ['ok','already_processed','product_mismatch_ignored','product_mismatch_ignored'])
        self.assertEqual(HistorialEstado.objects.filter(credito=self.credito).count(), 2)
        self.assertEqual(ZapSignWebhookLog.objects.filter(processed=True).count(), 4)


@override_settings(**{**CONFIGURACION_DATACREDITO_PRUEBA, **CONFIG})
@skipUnless(connection.vendor=='postgresql', 'Requiere PostgreSQL aislado')
class ReceptorPrestadorConcurrenciaTest(FixtureFirmaPrestador, TransactionTestCase):
    def test_dos_rutas_concurrentes_solo_permiten_una_transicion_prestador(self):
        from threading import Barrier
        antes=HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count()
        barrera=Barrier(2)
        def remoto(*args):
            self.assertFalse(connection.in_atomic_block)
            barrera.wait(timeout=15)
            return self.remoto
        def ejecutar(receptor):
            close_old_connections()
            try:
                return callback(Client(), receptor, 'documento-prestador-sintetico').json()['status']
            finally:
                close_old_connections()
        with patch.object(ZapSignClient, 'consultar_documento', side_effect=remoto) as api:
            with ThreadPoolExecutor(max_workers=4) as pool:
                resultados=list(pool.map(ejecutar, ['prestadores','libranza','prestadores','libranza']))
            self.assertEqual(api.call_count, 2)
        self.assertCountEqual(resultados, ['ok','already_processed','product_mismatch_ignored','product_mismatch_ignored'])
        self.assertEqual(HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count(), antes+1)
        self.assertEqual(ZapSignWebhookLog.objects.filter(processed=True).count(), 4)
        self.formal.refresh_from_db()
        self.assertEqual(self.formal.estado, 'FIRMADO')
        self.assertFalse(self.formal.credito.fecha_desembolso)

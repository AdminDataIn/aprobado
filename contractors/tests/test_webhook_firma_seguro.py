"""Existing V3 journey and signer callbacks, isolated and entirely synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import AprobacionInternaPrestador, AprobacionPagadorPrestador, FormalizacionCreditoPrestador
from contractors.services.aprobacion_pagador import CONFIRMACIONES_REQUERIDAS, decidir_aprobacion_pagador_prestador
from contractors.services.formalizacion import (
    preparar_formalizacion_credito_prestador, registrar_resultado_validacion_identidad_prestador,
    enviar_formalizacion_prestador_a_firma,
)
from contractors.services.originacion import originar_credito_prestador_desde_gate
from contractors.services.postfirma import preparar_transferencia_credito_prestador, confirmar_desembolso_credito_prestador
from contractors.tests.test_decision_definitiva import FixtureDecisionV3
from contractors.tests.test_aprobacion_interna_prestador import ClienteFirmaPrueba
from gestion_creditos.models import Credito, HistorialEstado, OrigenCreditoPrestador, Pagare
from gestion_creditos.services.zapsign_client import ZapSignClient
from gestion_creditos.tests.test_zapsign_webhook_seguro import CONFIG, documento_remoto
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from usuarios.models import PerfilPagador


class FixtureFirmaPrestador(FixtureDecisionV3):
    def setUp(self):
        super().setUp()
        audit=self.evaluar()
        gate=AprobacionInternaPrestador.objects.get(pk=self.decision(audit).gate_id)
        payer=get_user_model().objects.create_user('pagador-firma-sintetico')
        PerfilPagador.objects.create(usuario=payer,empresa=self.empresa)
        payer.user_permissions.add(Permission.objects.get(content_type__app_label='contractors',
            codename='can_decide_contractor_payer_approval'))
        decidir_aprobacion_pagador_prestador(AprobacionPagadorPrestador.objects.get(aprobacion_interna=gate),
            actor=payer,decision='APROBADO',confirmaciones={c:True for c in CONFIRMACIONES_REQUERIDAS})
        origen=originar_credito_prestador_desde_gate(gate,actor=self.staff).origen
        self.formal=preparar_formalizacion_credito_prestador(origen,actor=self.staff).formalizacion
        self.formal=registrar_resultado_validacion_identidad_prestador(self.formal,usuario=self.usuario,
            referencia_proveedor='identidad-sintetica',expira_en=timezone.now()+timedelta(minutes=15))
        self.formal=enviar_formalizacion_prestador_a_firma(self.formal,actor=self.staff,
            cliente=ClienteFirmaPrueba(token='documento-prestador-sintetico')).formalizacion
        self.remoto=documento_remoto('documento-prestador-sintetico',self.formal.credito_libranza.correo_electronico,
                                    external_id=self.formal.clave_idempotencia)

    def post(self, client=None, secret='secreto-sintetico-prestadores', event='doc_signed'):
        return (client or self.client).post('/api/webhooks/zapsign/prestadores/',
            data=json.dumps({'token':'documento-prestador-sintetico','event':event,'status':'signed'}),
            content_type='application/json', HTTP_X_ZAPSIGN_SECRET=secret)

    def sin_cierre(self):
        self.formal.refresh_from_db()
        self.assertEqual(self.formal.estado,'PENDIENTE_FIRMA')
        self.assertEqual(Credito.objects.get(pk=self.formal.credito_id).estado,'PENDIENTE_FIRMA')
        self.assertEqual(Pagare.objects.get(pk=self.formal.pagare_id).estado,'SENT')


@override_settings(**{**CONFIGURACION_DATACREDITO_PRUEBA, **CONFIG})
class FirmaPrestadorSeguraTest(FixtureFirmaPrestador, TestCase):
    def test_firma_verificada_y_replay_no_activa_credito_ni_modifica_gate(self):
        gates=list(AprobacionInternaPrestador.objects.values())
        with patch.object(ZapSignClient,'consultar_documento',return_value=self.remoto) as api:
            self.assertEqual(self.post().json()['status'],'ok')
            self.assertEqual(self.post().json()['status'],'already_processed')
            self.assertEqual(self.post(event='doc_refused').json()['status'],'already_processed')
            api.assert_called_once()
        self.formal.refresh_from_db()
        self.assertEqual(self.formal.estado,'FIRMADO')
        self.assertEqual(Credito.objects.get(pk=self.formal.credito_id).estado,'FIRMADO')
        self.assertEqual(gates,list(AprobacionInternaPrestador.objects.values()))
        self.assertFalse(self.formal.credito.fecha_desembolso)

    def test_falsificacion_y_configuracion_ausente_no_cierran(self):
        originales=(list(AprobacionInternaPrestador.objects.values()),
                    Credito.objects.count(), OrigenCreditoPrestador.objects.count())
        with patch.object(ZapSignClient,'consultar_documento') as api:
            self.assertEqual(self.post(secret='falso').status_code,403)
            with override_settings(ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=''):
                self.assertEqual(self.post().status_code,503)
            api.assert_not_called()
        self.sin_cierre()
        self.assertEqual(originales,(list(AprobacionInternaPrestador.objects.values()),
                                     Credito.objects.count(),OrigenCreditoPrestador.objects.count()))

    def test_webhook_verificado_hasta_postfirma_y_cierre_sintetico(self):
        with patch.object(ZapSignClient,'consultar_documento',return_value=self.remoto):
            self.assertEqual(self.post().json()['status'],'ok')
        credito=Credito.objects.get(pk=self.formal.credito_id)
        preparar_transferencia_credito_prestador(credito,actor=self.staff)
        cierre=confirmar_desembolso_credito_prestador(credito,actor=self.staff,
            comprobante=SimpleUploadedFile('comprobante-sintetico.pdf',b'%PDF-1.4 SOLO PRUEBA AISLADA'))
        self.assertEqual(cierre.credito.estado,'ACTIVO')
        self.assertEqual(self.post(event='doc_refused').json()['status'],'already_processed')

    def test_pendiente_remoto_no_cierra(self):
        self.remoto['status']='pending'
        self.remoto['signers'][0]['status']='new'
        with patch.object(ZapSignClient,'consultar_documento',return_value=self.remoto):
            self.assertEqual(self.post().json()['status'],'remote_pending_ignored')
        self.sin_cierre()

    def test_external_id_y_titular_deben_coincidir(self):
        self.remoto['external_id']='otra-originacion'
        with patch.object(ZapSignClient,'consultar_documento',return_value=self.remoto):
            self.assertEqual(self.post().status_code,503)
        self.sin_cierre()

    def test_identidad_local_incompleta_revierte_callback_y_estados(self):
        FormalizacionCreditoPrestador.objects.filter(pk=self.formal.pk).update(identidad_selfie_validada=False)
        with patch.object(ZapSignClient,'consultar_documento',return_value=self.remoto):
            self.assertEqual(self.post().status_code,500)
        self.sin_cierre()


@override_settings(**{**CONFIGURACION_DATACREDITO_PRUEBA, **CONFIG})
@skipUnless(connection.vendor=='postgresql','Requiere PostgreSQL aislado')
class FirmaPrestadorConcurrenciaTest(FixtureFirmaPrestador, TransactionTestCase):
    def test_dos_callbacks_una_firma_y_un_evento(self):
        antes=HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count()
        barrera=Barrier(2)
        def remote(*args):
            self.assertFalse(connection.in_atomic_block)
            barrera.wait(timeout=15)
            return self.remoto
        def ejecutar():
            close_old_connections()
            try:
                return self.post(client=Client()).json()['status']
            finally:
                close_old_connections()
        with patch.object(ZapSignClient,'consultar_documento',side_effect=remote):
            with ThreadPoolExecutor(max_workers=2) as pool:
                resultados=list(pool.map(lambda _:ejecutar(),range(2)))
        self.assertCountEqual(resultados,['ok','already_processed'])
        self.assertEqual(HistorialEstado.objects.filter(credito_id=self.formal.credito_id).count(),antes+1)

import io
import tempfile
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection, close_old_connections, IntegrityError, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from gestion_creditos.credit_services import registrar_pago_credito
from gestion_creditos.models import (
    NotificacionPagoBREB, PagoBREB, PagoBREBDetalle, HistorialPago,
    DetalleContablePago, CuotaAmortizacion, Credito,
)
from gestion_creditos.services.breb_notifications import (
    enviar_alerta_interna, reencolar_alerta_interna,
)
from gestion_creditos.services.breb_payments import aprobar_pago_breb
from gestion_creditos.tasks import enviar_alerta_breb_interna_task
from gestion_creditos.tests.test_pagos_breb import PagoBREBBaseMixin


class AlertFixture(PagoBREBBaseMixin):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(
            PRIVATE_DOCUMENTS_ROOT=folder.name,
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
            CREDIT_INTERNAL_NOTIFICATION_EMAILS=['operacion@aprobado.test', 'revision@aprobado.test'],
            PRIMARY_DOMAIN_HOST='aprobado.test',
            ALLOWED_HOSTS=['testserver', 'localhost'],
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self._crear_escenario_base()

    def evento(self, **kwargs):
        return self._reportar(**kwargs).notificaciones.get()


class BrebNotificationsTest(AlertFixture, TransactionTestCase):
    def test_creacion_y_retry_generan_un_evento_y_un_despacho(self):
        evento = self.evento()
        repetido = self._reportar()
        self.assertEqual(repetido.pk, evento.pago_breb_id)
        self.assertEqual(NotificacionPagoBREB.objects.count(), 1)
        self.assertEqual(PagoBREBDetalle.objects.count(), 1)
        self.broker_breb.assert_called_once_with(args=[evento.pk], retry=False)
        self.assertEqual(evento.estado, 'PENDIENTE')
        self.assertEqual(evento.numero_intentos, 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificacionPagoBREB.objects.create(pago_breb=repetido)

    def test_rollback_no_deja_evento_ni_encola(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.evento()
                self.broker_breb.assert_not_called()
                raise RuntimeError('rollback')
        self.assertFalse(PagoBREB.objects.exists())
        self.assertFalse(NotificacionPagoBREB.objects.exists())
        self.broker_breb.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_solo_envia_despues_commit_y_duplicado_task_no_reenvia(self):
        def worker(*, args, retry):
            self.assertFalse(connection.in_atomic_block)
            return enviar_alerta_breb_interna_task.run(args[0])
        self.broker_breb.side_effect = worker
        with transaction.atomic():
            evento = self.evento()
            self.assertEqual(len(mail.outbox), 0)
            self.broker_breb.assert_not_called()
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'ENVIADA')
        self.assertEqual(evento.numero_intentos, 1)
        self.assertIsNotNone(evento.enviado_en)
        self.assertIsNotNone(evento.ultimo_intento_en)
        self.assertEqual(enviar_alerta_interna(evento.pk), 'ENVIADA')
        self.assertEqual(len(mail.outbox), 1)

    def test_no_permite_smtp_dentro_transaccion_externa(self):
        evento = self.evento()
        with transaction.atomic(), self.assertRaises(RuntimeError):
            enviar_alerta_interna(evento.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_correo_minimizado_destinatarios_y_enlace_protegido(self):
        evento = self.evento(referencia_reportada='CEDULA-999123456-TOKEN-SECRETO')
        enviar_alerta_interna(evento.pk)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['operacion@aprobado.test', 'revision@aprobado.test'])
        self.assertEqual(email.attachments, [])
        self.assertIn(f'Reporte #{evento.pago_breb_id}', email.subject)
        contenido = email.body + email.alternatives[0][0]
        for requerido in ('EMPRESA BREB', '29/08/2026', 'Referencia disponible en la plataforma',
                          'https://aprobado.test/gestion/pagos-breb/'):
            self.assertIn(requerido, contenido)
        for prohibido in ('CEDULA-999123456', 'TOKEN-SECRETO', self.credito.detalle_libranza.cedula,
                          self.credito.nombre_cliente, evento.pago_breb.comprobante.name,
                          str(evento.pago_breb.clave_idempotencia), 'pagador@aprobado.test'):
            self.assertNotIn(prohibido, contenido)
        url = reverse('gestion:pagos_breb')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.revisor)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_cantidad_creditos_distintos_y_cuotas(self):
        cuota_dos = CuotaAmortizacion.objects.create(
            credito=self.credito, numero_cuota=2, fecha_vencimiento='2026-10-01',
            valor_cuota='110000', capital_a_pagar='100000', interes_a_pagar='10000',
            saldo_capital_pendiente='0',
        )
        evento = self.evento(obligaciones=[
            {'credito_id': self.credito.pk, 'cuota_id': self.cuota.pk},
            {'credito_id': self.credito.pk, 'cuota_id': cuota_dos.pk},
        ])
        enviar_alerta_interna(evento.pk)
        self.assertIn('Creditos: 1\nCuotas: 2', mail.outbox[0].body)

    def test_error_smtp_no_revierte_y_logs_no_filtran_excepcion(self):
        evento = self.evento(referencia_reportada='REFERENCIA-SENSIBLE')
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=TimeoutError('TOKEN-PII-PRIVADO')):
            with self.assertLogs('gestion_creditos.services.breb_notifications') as logs:
                self.assertEqual(enviar_alerta_interna(evento.pk), 'INCIERTA')
        evento.refresh_from_db()
        self.assertEqual(evento.codigo_error, 'SMTP_RESULTADO_INCIERTO')
        self.assertTrue(PagoBREB.objects.filter(pk=evento.pago_breb_id).exists())
        self.assertEqual(evento.pago_breb.estado, 'PENDIENTE_VERIFICACION')
        self.assertNotIn('TOKEN-PII', str(logs.output))
        self.assertNotIn('REFERENCIA-SENSIBLE', str(logs.output))
        self.assertFalse(reencolar_alerta_interna(evento.pk))
        enviar_alerta_interna(evento.pk)
        evento.refresh_from_db()
        self.assertEqual(evento.numero_intentos, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_cero_no_se_considera_enviado_ni_se_reintenta(self):
        evento = self.evento()
        with patch('django.core.mail.EmailMultiAlternatives.send', return_value=0):
            self.assertEqual(enviar_alerta_interna(evento.pk), 'INCIERTA')
        self.assertFalse(reencolar_alerta_interna(evento.pk))

    def test_caida_despues_smtp_conserva_reserva_incierta(self):
        evento = self.evento()
        save = NotificacionPagoBREB.save
        def fallo_al_confirmar(obj, *args, **kwargs):
            if obj.estado == 'ENVIADA':
                raise RuntimeError('NO-PERSISTIDO')
            return save(obj, *args, **kwargs)
        with patch.object(NotificacionPagoBREB, 'save', fallo_al_confirmar):
            self.assertEqual(enviar_alerta_interna(evento.pk), 'INCIERTA')
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'INCIERTA')
        self.assertIsNone(evento.enviado_en)
        enviar_alerta_interna(evento.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_falta_destinatarios_y_reintento_explicito(self):
        evento = self.evento()
        with override_settings(CREDIT_INTERNAL_NOTIFICATION_EMAILS=[]):
            self.assertEqual(enviar_alerta_interna(evento.pk), 'FALLIDA')
        evento.refresh_from_db()
        self.assertEqual(evento.codigo_error, 'SIN_DESTINATARIOS')
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(reencolar_alerta_interna(evento.pk))
        enviar_alerta_interna(evento.pk)
        evento.refresh_from_db()
        self.assertEqual(evento.numero_intentos, 2)
        self.assertEqual(evento.estado, 'ENVIADA')

    def test_fallo_preparacion_es_reintentable_sin_smtp(self):
        evento = self.evento()
        with patch('gestion_creditos.email_service.construir_alerta_interna_pago_breb', side_effect=ValueError('PII')):
            self.assertEqual(enviar_alerta_interna(evento.pk), 'FALLIDA')
        self.assertTrue(reencolar_alerta_interna(evento.pk))
        self.assertEqual(len(mail.outbox), 0)

    def test_fallo_broker_no_revierte_reporte_y_admite_recuperacion(self):
        self.broker_breb.side_effect = RuntimeError('BROKER-SECRET')
        evento = self.evento()
        self.assertEqual(evento.estado, 'FALLIDA')
        self.assertEqual(evento.codigo_error, 'COLA_NO_CONFIRMADA')
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.broker_breb.side_effect = None
        call_command('reintentar_alerta_breb', evento.pk, stdout=io.StringIO())
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'FALLIDA')
        call_command('reintentar_alerta_breb', evento.pk, confirmar=True, stdout=io.StringIO())
        enviar_alerta_interna(evento.pk)
        self.assertEqual(len(mail.outbox), 1)
        with self.assertRaises(CommandError):
            call_command('reintentar_alerta_breb', evento.pk, confirmar=True)

    def test_broker_falla_tras_worker_no_pisa_estado_enviado(self):
        def worker_y_error(*, args, retry):
            enviar_alerta_interna(args[0])
            raise RuntimeError('confirmacion perdida')
        self.broker_breb.side_effect = worker_y_error
        evento = self.evento()
        self.assertEqual(evento.estado, 'ENVIADA')
        self.assertEqual(len(mail.outbox), 1)

    def test_rechazo_neutral_no_modifica_cartera_ni_evidencia(self):
        pago = self._reportar()
        registrar_pago_credito(
            credito=self.credito, monto=self.cuota.valor_cuota,
            referencia_pago='MANUAL-ANTES-REVISION',
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
            usuario=self.pagador, empresa=self.empresa,
        )
        mail.outbox.clear()
        modelos = (HistorialPago, DetalleContablePago, CuotaAmortizacion, Credito, PagoBREBDetalle)
        antes = [list(model.objects.order_by('pk').values()) for model in modelos]
        evidencia = (pago.comprobante.name, pago.hash_comprobante, pago.fingerprint_reporte, pago.clave_idempotencia)
        motivo = ('Pago ya aplicado manualmente en cartera. Este reporte se cierra sin una nueva aplicacion. '
                  'No requiere realizar una nueva transferencia ni volver a reportar este pago.')
        with self.assertRaises(ValidationError):
            aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)
        self.client.force_login(self.revisor)
        response = self.client.post(reverse('gestion:pago_breb_decidir', args=[pago.pk]),
                                    {'accion': 'rechazar', 'motivo_rechazo': motivo})
        self.assertEqual(response.status_code, 302)
        pago.refresh_from_db()
        self.assertEqual(antes, [list(model.objects.order_by('pk').values()) for model in modelos])
        self.assertEqual(evidencia, (pago.comprobante.name, pago.hash_comprobante, pago.fingerprint_reporte, pago.clave_idempotencia))
        self.assertEqual(pago.estado, 'RECHAZADO')
        self.assertEqual(pago.revisado_por, self.revisor)
        self.assertIsNotNone(pago.revisado_en)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Actualizaci\u00f3n de tu reporte de pago BRE-B', mail.outbox[0].subject)
        self.assertIn(motivo, mail.outbox[0].body)
        self.assertNotIn('no pudo ser validado', mail.outbox[0].alternatives[0][0])


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales.')
class BrebNotificationsPostgresTest(AlertFixture, TransactionTestCase):
    def concurrir(self, funcion):
        barrera, errores, resultados = Barrier(2), Queue(), Queue()
        def ejecutar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                resultados.put(funcion())
            except Exception as exc:
                errores.put(type(exc).__name__)
            finally:
                close_old_connections()
        hilos = [Thread(target=ejecutar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=20)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(list(errores.queue), [])
        return list(resultados.queue)

    def test_reportes_concurrentes_un_evento(self):
        ids = self.concurrir(lambda: self._reportar().pk)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.assertEqual(NotificacionPagoBREB.objects.count(), 1)
        self.broker_breb.assert_called_once()

    def test_workers_concurrentes_solo_un_envio(self):
        evento = self.evento()
        self.concurrir(lambda: enviar_alerta_interna(evento.pk))
        evento.refresh_from_db()
        self.assertEqual(evento.numero_intentos, 1)
        self.assertEqual(evento.estado, 'ENVIADA')
        self.assertEqual(len(mail.outbox), 1)

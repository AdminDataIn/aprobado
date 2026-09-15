import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from queue import Queue
from threading import Event, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.db import connection, transaction, close_old_connections
from django.test import TestCase, TransactionTestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.models import (
    Credito, CreditoLibranza, CondicionOriginacionLibranza, Empresa, Pagare,
    HistorialEstado, HistorialPago, DetalleContablePago, CuotaAmortizacion,
    ZapSignWebhookLog, AprobacionPagadorLibranza,
)
from gestion_creditos.services.anulacion_credito import anular_credito_por_error_datos
from gestion_creditos.services.desembolso_credito import confirmar_desembolso_credito
from gestion_creditos.services.costo_originacion_libranza import crear_snapshot_originacion_libranza
from gestion_creditos.services.aprobacion_pagador_libranza import (
    decidir_solicitud_libranza_por_pagador, puede_decidir_solicitud_libranza_por_pagador,
)
from usuarios.models import PerfilPagador, ProductAccessProfile

User = get_user_model()
MOTIVO = ('Originacion invalida: solicitud creada mediante usuario pagador en lugar del colaborador. '
          'Se requiere nueva solicitud individual con validacion documental completa.')


class IntegridadFixture:
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name + '/media',
                                          PRIVATE_DOCUMENTS_ROOT=self.media.name + '/privado')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.actor = User.objects.create_user('staff-incidente', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(
            content_type__app_label='gestion_creditos', codename='change_credito',
        ))
        self.usuario = User.objects.create_user('cliente-incidente')
        self.empresa = Empresa.objects.create(nombre='Empresa de prueba', convenio_activo=True)
        self.credito = Credito.objects.create(
            numero_credito='CR-INCIDENTE-1', usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA, estado=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            monto_solicitado=1000000, monto_aprobado=1000000, plazo_solicitado=3, plazo=3,
            tasa_interes=Decimal('1.90'),
        )
        CreditoLibranza.objects.create(
            credito=self.credito, empresa=self.empresa, nombres='Cliente', apellidos='Prueba',
            cedula='10000001', direccion='Calle 123', telefono='3000000000',
            correo_electronico='cliente@example.test',
        )
        crear_snapshot_originacion_libranza(credito=self.credito)
        self.pagare = Pagare.objects.create(
            credito=self.credito, numero_pagare='PAG-INCIDENTE-1', estado=Pagare.EstadoPagare.SIGNED,
            fecha_firma=timezone.now(), zapsign_status='signed', zapsign_doc_token='token-prueba',
            archivo_pdf=SimpleUploadedFile('original.pdf', b'%PDF-original'),
            archivo_pdf_firmado=SimpleUploadedFile('firmado.pdf', b'%PDF-firmado'),
            evidencias={'status': 'signed', 'historico': True},
        )
        self.webhook = ZapSignWebhookLog.objects.create(
            doc_token=self.pagare.zapsign_doc_token, event='doc_signed', payload={'status': 'signed'},
            signature_valid=True, processed=True, ip_address='127.0.0.1',
        )

    def anular(self, **kwargs):
        return anular_credito_por_error_datos(
            credito=self.credito, actor=kwargs.get('actor', self.actor), motivo=kwargs.get('motivo', MOTIVO),
        )

    def desembolsar(self):
        return confirmar_desembolso_credito(
            credito=self.credito, actor=self.actor,
            comprobante=SimpleUploadedFile('comprobante.pdf', b'%PDF-prueba'),
        )

    def cuota(self, **kwargs):
        return CuotaAmortizacion.objects.create(
            credito=self.credito, numero_cuota=1, fecha_vencimiento=date(2026, 10, 1),
            valor_cuota=100, capital_a_pagar=90, interes_a_pagar=10,
            saldo_capital_pendiente=0, **kwargs,
        )


class IntegridadOriginacionAnulacionTests(IntegridadFixture, TestCase):
    def test_postfirma_conserva_toda_evidencia_y_reintento(self):
        pagare = Pagare.objects.values().get(pk=self.pagare.pk)
        webhook = ZapSignWebhookLog.objects.values().get(pk=self.webhook.pk)
        with patch('gestion_creditos.services.zapsign_client.ZapSignClient') as api:
            self.anular()
            self.assertTrue(self.anular().ya_estaba_anulado)
            api.assert_not_called()
        self.assertEqual(Pagare.objects.values().get(pk=self.pagare.pk), pagare)
        self.assertEqual(ZapSignWebhookLog.objects.values().get(pk=self.webhook.pk), webhook)
        with self.pagare.archivo_pdf_firmado.open('rb') as archivo:
            self.assertEqual(archivo.read(), b'%PDF-firmado')
        evento = HistorialEstado.objects.get(credito=self.credito)
        self.assertEqual(evento.estado_anterior, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA)
        self.assertEqual(evento.estado_nuevo, Credito.EstadoCredito.ANULADO)
        self.assertEqual(evento.usuario_modificacion, self.actor)
        self.assertEqual(evento.motivo, MOTIVO)
        self.assertIsNotNone(evento.fecha)
        self.assertFalse(HistorialPago.objects.exists())
        self.assertFalse(DetalleContablePago.objects.exists())

    def test_precondiciones_financieras(self):
        casos = {
            'desembolso': lambda: Credito.objects.filter(pk=self.credito.pk).update(fecha_desembolso=timezone.now()),
            'pago': lambda: HistorialPago.objects.create(credito=self.credito, monto=1, estado='EXITOSO'),
            'cuota_pagada': lambda: self.cuota(pagada=True),
            'cuota_parcial': lambda: self.cuota(monto_pagado=Decimal('0.01')),
            'activacion_previa': lambda: HistorialEstado.objects.create(credito=self.credito, estado_nuevo='ACTIVO'),
            'soporte_desembolso': lambda: HistorialEstado.objects.create(
                credito=self.credito, estado_nuevo='PENDIENTE_TRANSFERENCIA', comprobante_pago='historico.pdf'),
        }
        for nombre, preparar in casos.items():
            with self.subTest(caso=nombre), transaction.atomic():
                preparar()
                with self.assertRaises(ValidationError):
                    self.anular()
                self.assertEqual(Credito.objects.get(pk=self.credito.pk).estado, 'PENDIENTE_TRANSFERENCIA')
                transaction.set_rollback(True)

    def test_detalle_contable_incluso_si_pago_no_pertenece_al_credito(self):
        otro = Credito.objects.create(usuario=self.usuario, linea='LIBRANZA', numero_credito='CR-INCIDENTE-2',
                                      monto_solicitado=1000000, plazo_solicitado=3)
        pago = HistorialPago.objects.create(credito=otro, monto=1)
        DetalleContablePago.objects.create(credito=self.credito, pago=pago, monto_total_aplicado=1)
        with self.assertRaisesMessage(ValidationError, 'contabilidad'):
            self.anular()

    def test_actor_y_motivo_obligatorios(self):
        for actor in (None, self.usuario, User.objects.create_user('sin-permiso', is_staff=True)):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                self.anular(actor=actor)
        with self.assertRaises(ValidationError):
            self.anular(motivo='  ')
        PerfilPagador.objects.create(usuario=self.actor, empresa=self.empresa)
        with self.assertRaises(PermissionDenied):
            self.anular(actor=User.objects.get(pk=self.actor.pk))

    def test_estado_incompatible_y_rollback(self):
        for estado in ('FIRMADO', 'ACTIVO', 'EN_MORA', 'PAGADO', 'RECHAZADO'):
            with self.subTest(estado=estado), transaction.atomic():
                Credito.objects.filter(pk=self.credito.pk).update(estado=estado)
                with self.assertRaises(ValidationError):
                    self.anular()
                transaction.set_rollback(True)
        with patch('gestion_creditos.services.anulacion_credito.HistorialEstado.objects.create', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.anular()
        self.assertEqual(Credito.objects.get(pk=self.credito.pk).estado, 'PENDIENTE_TRANSFERENCIA')
        self.assertFalse(HistorialEstado.objects.exists())

    def test_anulacion_ganadora_bloquea_desembolso_con_objeto_obsoleto(self):
        self.anular()
        with self.assertRaises(ValidationError):
            self.desembolsar()
        self.credito.refresh_from_db()
        self.assertIsNone(self.credito.fecha_desembolso)
        self.assertFalse(self.credito.tabla_amortizacion.exists())

    @patch('gestion_creditos.email_service.enviar_notificacion_cambio_estado')
    def test_desembolso_ganador_bloquea_anulacion_y_reintento(self, notificar):
        self.desembolsar()
        with self.assertRaises(ValidationError):
            self.anular()
        with self.assertRaises(ValidationError):
            self.desembolsar()
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, 'ACTIVO')
        self.assertIsNotNone(self.credito.fecha_desembolso)
        self.assertEqual(self.credito.tabla_amortizacion.count(), 3)
        self.assertEqual(HistorialEstado.objects.count(), 1)

    def test_desembolso_rollback_y_vista_delega_servicio(self):
        with patch('gestion_creditos.credit_services.activar_credito', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.desembolsar()
        self.assertEqual(Credito.objects.get(pk=self.credito.pk).estado, 'PENDIENTE_TRANSFERENCIA')
        self.client.force_login(self.actor)
        with patch('gestion_creditos.services.desembolso_credito.confirmar_desembolso_credito') as servicio:
            response = self.client.post(reverse('gestion:credito_desembolsar', args=[self.credito.pk]), {
                'comprobante_pago': SimpleUploadedFile('prueba.pdf', b'%PDF-prueba'),
            })
        self.assertEqual(response.status_code, 302)
        servicio.assert_called_once()

    def test_admin_postfirma_requiere_motivo_y_no_permite_editar_estado(self):
        from django.contrib import admin
        self.client.force_login(self.actor)
        url = reverse('admin:gestion_creditos_credito_changelist')
        datos = {'action': 'anular_por_error_datos', '_selected_action': [self.credito.pk]}
        self.client.post(url, datos)
        self.assertFalse(HistorialEstado.objects.exists())
        model_admin = admin.site._registry[Credito]
        request = RequestFactory().get(url)
        request.user = self.actor
        self.assertIn('estado', model_admin.get_readonly_fields(request, self.credito))
        form = model_admin.get_form(request, self.credito)
        self.assertNotIn('estado', form.base_fields)
        self.client.post(url, {**datos, 'motivo_anulacion': MOTIVO})
        self.assertEqual(HistorialEstado.objects.get().motivo, MOTIVO)

    def test_comando_exige_actor_y_conserva_signed(self):
        args = ['--numero-credito', self.credito.numero_credito, '--motivo', MOTIVO, '--apply']
        with self.assertRaises(CommandError):
            call_command('anular_credito_por_error_datos', *args, stdout=StringIO())
        call_command('anular_credito_por_error_datos', *args, '--actor-id', str(self.actor.pk), stdout=StringIO())
        self.assertEqual(Pagare.objects.get(pk=self.pagare.pk).estado, 'SIGNED')

    @override_settings(ZAPSIGN_WEBHOOK_SECRET='secreto-test')
    def test_webhook_tardio_signed_no_reactiva_ni_modifica_pagare(self):
        from gestion_creditos.views.integrations import zapsign_webhook_view
        self.anular()
        anterior = Pagare.objects.values().get(pk=self.pagare.pk)
        for _ in range(2):
            request = RequestFactory().post('/webhook/', json.dumps({
                'event': 'doc_signed', 'token': self.pagare.zapsign_doc_token, 'status': 'signed',
            }), content_type='application/json', HTTP_X_ZAPSIGN_SECRET='secreto-test')
            response = zapsign_webhook_view(request)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.get(pk=self.credito.pk).estado, 'ANULADO')
        self.assertEqual(Pagare.objects.values().get(pk=self.pagare.pk), anterior)
        self.assertEqual(ZapSignWebhookLog.objects.filter(processed=True).count(), 3)
        self.assertFalse(self.credito.tabla_amortizacion.exists())

    def test_pagador_no_origina_get_post_ni_ajax_sin_escrituras_de_dominio(self):
        PerfilPagador.objects.create(usuario=self.usuario, empresa=self.empresa)
        self.client.force_login(self.usuario)
        anteriores = (Credito.objects.count(), CondicionOriginacionLibranza.objects.count(), ProductAccessProfile.objects.count())
        for ruta in ('libranza:solicitar', 'libranza:adelanto_nomina'):
            for metodo in ('get', 'post'):
                with self.subTest(ruta=ruta, metodo=metodo):
                    response = getattr(self.client, metodo)(reverse(ruta), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                    self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertEqual(anteriores, (Credito.objects.count(), CondicionOriginacionLibranza.objects.count(), ProductAccessProfile.objects.count()))

    def test_usuario_normal_y_staff_sin_perfil_pueden_abrir_solicitud(self):
        for usuario in (self.usuario, self.actor):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('libranza:solicitar')).status_code, 200)

    @patch('gestion_creditos.views.solicitudes.procesar_certificado_bancario')
    def test_usuario_normal_y_staff_sin_perfil_pueden_originar(self, procesar):
        from gestion_creditos.tests.captura_fixtures import sesion_finalizada
        for numero, usuario in enumerate((self.usuario, self.actor), start=2):
            self.client.force_login(usuario)
            response = self.client.post(reverse('libranza:solicitar'), {
                'valor_credito': '1000000', 'ingresos_mensuales': '3000000', 'plazo': '3',
                'nombres': 'Cliente', 'apellidos': 'Individual', 'cedula': f'1000000{numero}',
                'direccion': 'Calle Principal 123', 'telefono': '3001234567',
                'correo_electronico': f'individual{numero}@example.test', 'empresa': self.empresa.pk,
                'sesion_documental_id': str(sesion_finalizada(usuario).pk),
                'certificado_bancario': SimpleUploadedFile('banco.pdf', b'%PDF-banco', content_type='application/pdf'),
            })
            self.assertRedirects(response, reverse('libranza:mi_credito'), fetch_redirect_response=False)
            nuevo = Credito.objects.exclude(pk=self.credito.pk).get(usuario=usuario)
            self.assertEqual(nuevo.estado, 'EN_REVISION')
            self.assertTrue(CondicionOriginacionLibranza.objects.filter(credito=nuevo).exists())

    def test_originacion_especial_no_reutiliza_cuenta_pagador_ni_la_modifica(self):
        from libranza.services.special_case_originator import _get_or_create_user, SpecialCaseOriginationError
        self.usuario.email = 'pagador@example.test'
        self.usuario.save(update_fields=['email'])
        PerfilPagador.objects.create(usuario=self.usuario, empresa=self.empresa)
        with self.assertRaises(SpecialCaseOriginationError):
            _get_or_create_user({'correo': self.usuario.email, 'nombres': 'Tercero', 'apellidos': 'Otro'})
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.first_name, '')
        self.assertEqual(Credito.objects.count(), 1)

    def test_autoaprobacion_prohibida_independiente_de_empresa_y_nivel(self):
        PerfilPagador.objects.create(usuario=self.usuario, empresa=self.empresa)
        for doble in (False, True):
            for distintos in (False, True):
                self.empresa.requiere_doble_aprobacion_libranza = doble
                self.empresa.requiere_aprobadores_distintos_libranza = distintos
                self.empresa.save()
                for estado in ('EN_REVISION', 'PENDIENTE_APROBACION_FINAL'):
                    self.credito.estado = estado
                    self.credito.save(update_fields=['estado'])
                    with self.subTest(doble=doble, distintos=distintos, estado=estado):
                        with self.assertRaises(PermissionDenied):
                            decidir_solicitud_libranza_por_pagador(self.credito, self.usuario, 'approve')
                        self.assertFalse(puede_decidir_solicitud_libranza_por_pagador(self.credito, self.usuario))
        self.assertFalse(AprobacionPagadorLibranza.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real en VPS de pruebas.')
class AnulacionDesembolsoConcurrenciaTests(IntegridadFixture, TransactionTestCase):
    def competir(self, ganador):
        bloqueado, intento = Event(), Event()
        resultados, errores = Queue(), Queue()

        def ejecutar(operacion, primero):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                if not primero:
                    if not bloqueado.wait(10):
                        raise RuntimeError('No se obtuvo el lock inicial')
                    intento.set()
                with transaction.atomic():
                    if primero:
                        Credito.objects.select_for_update().get(pk=self.credito.pk)
                        bloqueado.set()
                        if not intento.wait(10):
                            raise RuntimeError('No inicio el competidor')
                    if operacion == 'anular':
                        self.anular()
                    else:
                        self.desembolsar()
                resultados.put(operacion)
            except ValidationError:
                resultados.put('rechazado')
            except Exception as exc:
                errores.put(repr(exc))
            finally:
                connection.close()

        perdedor = 'desembolsar' if ganador == 'anular' else 'anular'
        hilos = [Thread(target=ejecutar, args=(ganador, True)), Thread(target=ejecutar, args=(perdedor, False))]
        with patch('gestion_creditos.email_service.enviar_notificacion_cambio_estado'):
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join(30)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(list(errores.queue), [])
        self.assertCountEqual(list(resultados.queue), [ganador, 'rechazado'])
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, 'ANULADO' if ganador == 'anular' else 'ACTIVO')
        self.assertEqual(self.credito.fecha_desembolso is None, ganador == 'anular')
        self.assertEqual(self.credito.tabla_amortizacion.count(), 0 if ganador == 'anular' else 3)
        self.assertEqual(HistorialEstado.objects.count(), 1)

    def test_anulacion_gana(self):
        self.competir('anular')

    def test_desembolso_gana(self):
        self.competir('desembolsar')

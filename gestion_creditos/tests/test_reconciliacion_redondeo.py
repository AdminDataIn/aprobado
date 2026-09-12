import json
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from gestion_creditos import credit_services
from gestion_creditos.models import (
    Credito, CreditoLibranza, CuotaAmortizacion, DetalleContablePago,
    Empresa, HistorialEstado, HistorialPago,
)
from gestion_creditos.services.dashboard_metrics import get_admin_obligaciones_context
from usuarios.models import PerfilPagador


User = get_user_model()


class ReconciliacionFixture:
    def setUp(self):
        self.staff = User.objects.create_user('revisor-redondeo', is_staff=True)
        self.permiso = Permission.objects.get(
            content_type__app_label='gestion_creditos', codename='reconcile_manual_payment_rounding',
        )
        self.staff.user_permissions.add(self.permiso)
        self.empresa = Empresa.objects.create(nombre='Empresa redondeo')
        self.cliente = User.objects.create_user('cliente-redondeo')
        self.credito, self.cuota, self.siguiente, self.pago = self.crear_caso()

    def crear_caso(self):
        credito = Credito.objects.create(
            numero_credito=f'CR-RND-{Credito.objects.count() + 1:04}', usuario=self.cliente,
            linea=Credito.LineaCredito.LIBRANZA, estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=200, monto_aprobado=200, plazo=2, plazo_solicitado=2,
            valor_cuota=100, saldo_pendiente=Decimal('100.20'), capital_pendiente=Decimal('100.20'),
            total_a_pagar=200, tasa_interes=0, comision=0, iva_comision=0,
            fecha_proximo_pago=date(2026, 9, 1),
        )
        CreditoLibranza.objects.create(
            credito=credito, empresa=self.empresa, nombres='Cliente', apellidos='Prueba',
            cedula='10000001', telefono='3000000000', correo_electronico='prueba@example.test',
            direccion='Direccion de prueba',
        )
        cuotas = [CuotaAmortizacion.objects.create(
            credito=credito, numero_cuota=n, fecha_vencimiento=date(2026, 8 + n, 1),
            valor_cuota=100, capital_a_pagar=100, interes_a_pagar=0,
            saldo_capital_pendiente=100 if n == 1 else 0,
            monto_pagado=Decimal('99.80') if n == 1 else 0,
        ) for n in (1, 2)]
        pago = HistorialPago.objects.create(
            credito=credito, monto=Decimal('99.80'), estado=HistorialPago.EstadoPago.EXITOSO,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            referencia_pago=f'REF-RND-{credito.pk}', fecha_aplicacion=timezone.now(),
        )
        DetalleContablePago.objects.create(
            credito=credito, pago=pago, cuota=cuotas[0], fecha_aplicacion=pago.fecha_aplicacion,
            monto_total_aplicado=Decimal('99.80'), capital_aplicado=Decimal('99.80'),
            capital_principal_aplicado=Decimal('99.80'),
        )
        return credito, cuotas[0], cuotas[1], pago

    def reconciliar(self, **kwargs):
        return credit_services.reconciliar_pago_manual_por_tolerancia(
            kwargs.get('pago', self.pago), cuota=kwargs.get('cuota', self.cuota),
            usuario=kwargs.get('usuario', self.staff),
        )

    def eventos(self):
        return HistorialEstado.objects.filter(clave_idempotencia__startswith='reconciliacion-redondeo:')


@override_settings(MANUAL_PAYMENT_ROUNDING_TOLERANCE='100.00')
class ReconciliacionRedondeoTests(ReconciliacionFixture, TestCase):
    def test_pagador_explicito_conserva_contabilidad_y_audita_cuota(self):
        detalles = list(DetalleContablePago.objects.values())
        pago_original = HistorialPago.objects.values().get(pk=self.pago.pk)
        seleccionadas, resumen = self.reconciliar()
        self.assertEqual([q.pk for q in seleccionadas], [self.cuota.pk])
        self.cuota.refresh_from_db()
        self.siguiente.refresh_from_db()
        self.credito.refresh_from_db()
        self.assertTrue(self.cuota.pagada)
        self.assertFalse(self.siguiente.pagada)
        self.assertEqual(self.cuota.monto_pagado, Decimal('99.80'))
        self.assertEqual(self.cuota.fecha_pago, self.pago.fecha_aplicacion)
        self.assertEqual(self.credito.fecha_proximo_pago, self.siguiente.fecha_vencimiento)
        self.assertEqual(resumen['saldo_pendiente'], Decimal('100'))
        self.assertEqual(self.credito.saldo_pendiente, Decimal('100'))
        self.assertEqual(self.credito.capital_pendiente, Decimal('100'))
        self.assertEqual(list(DetalleContablePago.objects.values()), detalles)
        pago_nuevo = HistorialPago.objects.values().get(pk=self.pago.pk)
        for campo, valor in pago_original.items():
            if campo != 'notas':
                self.assertEqual(pago_nuevo[campo], valor, campo)
        self.assertIn('Cuota 1 cerrada por tolerancia', pago_nuevo['notas'])
        evento = self.eventos().get()
        datos = json.loads(evento.motivo)
        self.assertEqual((datos['pago_id'], datos['credito_id'], datos['cuota_id']),
                         (self.pago.pk, self.credito.pk, self.cuota.pk))
        self.assertEqual(Decimal(datos['diferencia']), Decimal('0.20'))
        self.assertEqual(Decimal(datos['tolerancia']), Decimal('100'))
        self.assertEqual(datos['resultado'], 'RECONCILIADA')
        self.assertEqual(datos['actor_id'], self.staff.pk)
        self.assertTrue(datos['timestamp'])
        self.assertIsNotNone(evento.fecha)
        self.assertEqual(evento.usuario_modificacion, self.staff)

    def test_admin_y_legacy_siguen_admitidos(self):
        for origen in (HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN, HistorialPago.OrigenRegistro.LEGACY):
            with self.subTest(origen=origen):
                _credito, cuota, _siguiente, pago = self.crear_caso()
                pago.origen_registro = origen
                pago.save(update_fields=['origen_registro'])
                self.reconciliar(pago=pago, cuota=cuota)
                cuota.refresh_from_db()
                self.assertTrue(cuota.pagada)

    def test_breb_y_otros_origenes_son_rechazados(self):
        for origen, metodo in (
            (HistorialPago.OrigenRegistro.REPORTE_BREB, HistorialPago.MetodoPago.BREB),
            (HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR, HistorialPago.MetodoPago.BREB),
            (HistorialPago.OrigenRegistro.PASARELA_WOMPI, HistorialPago.MetodoPago.WOMPI),
            (HistorialPago.OrigenRegistro.CARGA_MASIVA_EMPRESA, HistorialPago.MetodoPago.OFFLINE_MANUAL),
        ):
            with self.subTest(origen=origen, metodo=metodo):
                HistorialPago.objects.filter(pk=self.pago.pk).update(origen_registro=origen, metodo_pago=metodo)
                with self.assertRaises(ValidationError):
                    self.reconciliar()
        self.assertFalse(self.eventos().exists())

    def test_rechaza_cero_exceso_satisfecha_fallido_y_sin_detalle(self):
        casos = (
            ('cero', {'monto_pagado': Decimal('100')}, {}),
            ('exceso', {'valor_cuota': Decimal('200')}, {}),
            ('pagada', {'pagada': True}, {}),
            ('fallido', {}, {'estado': HistorialPago.EstadoPago.FALLIDO}),
            ('pendiente', {}, {'estado': HistorialPago.EstadoPago.PENDIENTE}),
            ('sin_detalle', {}, {}),
        )
        for nombre, cambios_cuota, cambios_pago in casos:
            with self.subTest(caso=nombre):
                _credito, cuota, _otra, pago = self.crear_caso()
                if cambios_cuota:
                    CuotaAmortizacion.objects.filter(pk=cuota.pk).update(**cambios_cuota)
                if cambios_pago:
                    HistorialPago.objects.filter(pk=pago.pk).update(**cambios_pago)
                if nombre == 'sin_detalle':
                    pago.detalles_contables.all().delete()
                with self.assertRaises(ValidationError):
                    self.reconciliar(pago=pago, cuota=cuota)
        self.assertFalse(self.eventos().exists())

    @override_settings(MANUAL_PAYMENT_ROUNDING_TOLERANCE='0.20')
    def test_limite_inclusivo_y_tolerancia_revalidada(self):
        with override_settings(MANUAL_PAYMENT_ROUNDING_TOLERANCE='0.19'):
            with self.assertRaises(ValidationError):
                self.reconciliar()
        self.reconciliar()
        self.assertEqual(Decimal(json.loads(self.eventos().get().motivo)['tolerancia']), Decimal('0.20'))

    def test_solo_cuota_seleccionada_aunque_pago_tenga_dos_detalles(self):
        self.siguiente.monto_pagado = Decimal('99.70')
        self.siguiente.save(update_fields=['monto_pagado'])
        DetalleContablePago.objects.create(
            pago=self.pago, credito=self.credito, cuota=self.siguiente,
            monto_total_aplicado=Decimal('99.70'), capital_aplicado=Decimal('99.70'),
            capital_principal_aplicado=Decimal('99.70'), secuencia_aplicacion=2,
        )
        self.pago.monto = Decimal('199.50')
        self.pago.save(update_fields=['monto'])
        self.reconciliar(cuota=self.siguiente)
        self.cuota.refresh_from_db()
        self.siguiente.refresh_from_db()
        self.assertFalse(self.cuota.pagada)
        self.assertTrue(self.siguiente.pagada)
        with self.assertRaises(ValidationError):
            self.reconciliar(cuota=None)

    def test_rechaza_cuota_de_otro_credito_y_credito_terminal(self):
        _otro_credito, otra, _siguiente, _pago = self.crear_caso()
        with self.assertRaises(ValidationError):
            self.reconciliar(cuota=otra)
        for estado in (Credito.EstadoCredito.ANULADO, Credito.EstadoCredito.PAGADO):
            Credito.objects.filter(pk=self.credito.pk).update(estado=estado)
            with self.assertRaises(ValidationError):
                self.reconciliar()
        self.assertFalse(self.eventos().exists())

    def test_permiso_en_servicio_y_vista_y_no_basta_clave(self):
        url = reverse('gestion:pago_reconciliar_redondeo', args=[self.pago.pk])
        sin_permiso = User.objects.create_user('staff-sin-permiso', is_staff=True)
        sin_staff = User.objects.create_user('no-staff-con-permiso')
        sin_staff.user_permissions.add(self.permiso)
        for usuario in (None, AnonymousUser(), sin_permiso, sin_staff):
            with self.subTest(usuario=usuario):
                with self.assertRaises(PermissionDenied):
                    self.reconciliar(usuario=usuario)
        self.client.force_login(sin_permiso)
        with override_settings(MANUAL_PAYMENT_AUTH_KEY='clave-test'):
            self.assertEqual(self.client.post(url, {
                'cuota_id': self.cuota.pk, 'confirmar': 'si', 'auth_key': 'clave-test',
            }).status_code, 403)
        self.assertFalse(self.eventos().exists())

    def test_perfil_pagador_staff_con_permiso_rechazado(self):
        PerfilPagador.objects.create(usuario=self.staff, empresa=self.empresa)
        self.staff = User.objects.get(pk=self.staff.pk)
        with self.assertRaises(PermissionDenied):
            self.reconciliar()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse('gestion:pago_reconciliar_redondeo', args=[self.pago.pk]), {
            'cuota_id': self.cuota.pk, 'confirmar': 'si',
        }).status_code, 403)
        self.assertFalse(self.eventos().exists())

    def test_reintento_no_duplica_efectos_ni_auditoria(self):
        self.reconciliar()
        notas = HistorialPago.objects.get(pk=self.pago.pk).notas
        cuotas = list(CuotaAmortizacion.objects.values())
        seleccionadas, _resumen = self.reconciliar()
        self.assertEqual(seleccionadas, [])
        self.assertEqual(list(CuotaAmortizacion.objects.values()), cuotas)
        self.assertEqual(HistorialPago.objects.get(pk=self.pago.pk).notas, notas)
        self.assertEqual(self.eventos().count(), 1)

    def test_rollback_completo_si_falla_recalculo_o_auditoria(self):
        for objetivo in ('obtener_resumen_pagos_credito', 'HistorialEstado.objects.create'):
            with self.subTest(objetivo=objetivo):
                credito = Credito.objects.values().get(pk=self.credito.pk)
                cuota = CuotaAmortizacion.objects.values().get(pk=self.cuota.pk)
                pago = HistorialPago.objects.values().get(pk=self.pago.pk)
                detalles = list(DetalleContablePago.objects.values())
                with patch(f'gestion_creditos.credit_services.{objetivo}', side_effect=RuntimeError('fallo de prueba')):
                    with self.assertRaises(RuntimeError):
                        self.reconciliar()
                self.assertEqual(Credito.objects.values().get(pk=self.credito.pk), credito)
                self.assertEqual(CuotaAmortizacion.objects.values().get(pk=self.cuota.pk), cuota)
                self.assertEqual(HistorialPago.objects.values().get(pk=self.pago.pk), pago)
                self.assertEqual(list(DetalleContablePago.objects.values()), detalles)
                self.assertFalse(self.eventos().exists())

    def test_cierre_final_y_reintento_sobre_credito_pagado(self):
        self.siguiente.pagada = True
        self.siguiente.monto_pagado = 100
        self.siguiente.save(update_fields=['pagada', 'monto_pagado'])
        with patch('gestion_creditos.email_service.enviar_notificacion_cambio_estado') as notificar:
            self.reconciliar()
            self.reconciliar()
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.PAGADO)
        self.assertEqual(self.credito.saldo_pendiente, 0)
        self.assertEqual(self.credito.capital_pendiente, 0)
        self.assertIsNone(self.credito.fecha_proximo_pago)
        self.assertEqual(self.eventos().count(), 1)
        self.assertEqual(notificar.call_count, 1)

    def test_pagador_no_activa_tolerancia_automatica(self):
        self.pago.delete()
        self.cuota.monto_pagado = 0
        self.cuota.save(update_fields=['monto_pagado'])
        pago, _ = credit_services.registrar_pago_credito(
            credito=self.credito, monto=Decimal('99.80'), referencia_pago='PAGADOR-SIN-AUTO',
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
        )
        self.cuota.refresh_from_db()
        self.assertFalse(self.cuota.pagada)
        self.assertEqual(self.cuota.monto_pagado, Decimal('99.80'))
        self.assertNotIn('tolerancia', pago.notas)

    def test_ui_elegibilidad_y_confirmacion_post_con_filtros(self):
        self.client.force_login(self.staff)
        url = reverse('gestion:obligaciones_pendientes')
        response = self.client.get(url, {'credito': self.credito.numero_credito})
        self.assertContains(response, f'data-bs-target="#redondeo{self.cuota.pk}"')
        for label in ('Valor cuota', 'Monto pagado', 'Diferencia', 'Tolerancia configurada', self.empresa.nombre):
            self.assertContains(response, label)
        endpoint = reverse('gestion:pago_reconciliar_redondeo', args=[self.pago.pk])
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        self.client.post(endpoint, {'cuota_id': self.cuota.pk})
        self.assertFalse(self.eventos().exists())
        response = self.client.post(endpoint, {
            'cuota_id': self.cuota.pk, 'confirmar': 'si', 'filtros': 'vista=detalle&page=1&credito=CR-RND-0001',
        })
        self.assertEqual(response['Location'], url + '?vista=detalle&page=1&credito=CR-RND-0001')
        self.assertEqual(self.eventos().count(), 1)
        self.assertNotContains(self.client.get(url), f'data-bs-target="#redondeo{self.cuota.pk}"')

    def test_ui_no_muestra_accion_sin_permiso_ni_para_breb(self):
        self.client.force_login(User.objects.create_user('observador-redondeo', is_staff=True))
        url = reverse('gestion:obligaciones_pendientes')
        self.assertNotContains(self.client.get(url), 'Reconciliar redondeo')
        self.client.force_login(self.staff)
        self.pago.origen_registro = HistorialPago.OrigenRegistro.REPORTE_BREB
        self.pago.save(update_fields=['origen_registro'])
        self.assertNotContains(self.client.get(url), 'Reconciliar redondeo')

    @override_settings(USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE='es-CO')
    def _verificar_ids_renderizados_y_post(self, numero_cuota):
        class Formularios(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elementos = []
                self.forms = []
                self.actual = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                self.elementos.append((tag, attrs))
                if tag == 'form' and attrs.get('method') == 'post':
                    self.actual = {'action': attrs['action'], 'inputs': {}}
                    self.forms.append(self.actual)
                if tag == 'input' and self.actual is not None and attrs.get('name'):
                    self.actual['inputs'][attrs['name']] = attrs.get('value', '')

            def handle_endtag(self, tag):
                if tag == 'form':
                    self.actual = None

        self.cuota.pagada = True
        self.cuota.save(update_fields=['pagada'])
        casos = [self.crear_caso() for _ in range(3)]
        esperadas = {}
        # PK altos explicitos sin depender de las secuencias SQLite/PostgreSQL.
        for pk, (_credito, cuota, siguiente, pago) in zip((999, 1000, 1234), casos):
            siguiente.numero_cuota = 3
            siguiente.save(update_fields=['numero_cuota'])
            anterior_pk = cuota.pk
            CuotaAmortizacion.objects.filter(pk=anterior_pk).update(numero_cuota=4)
            cuota.pk = pk
            cuota.numero_cuota = numero_cuota
            cuota.save(force_insert=True)
            DetalleContablePago.objects.filter(cuota_id=anterior_pk).update(cuota_id=pk)
            CuotaAmortizacion.objects.filter(pk=anterior_pk).delete()
            esperadas[str(pk)] = pago.pk

        self.client.force_login(self.staff)
        with translation.override('es-co'):
            response = self.client.get(reverse('gestion:obligaciones_pendientes'))
            self.assertEqual(response.status_code, 200)
            html = Formularios()
            html.feed(response.content.decode())
            self.assertCountEqual([f['inputs']['cuota_id'] for f in html.forms], esperadas)
            ids = [a['id'] for _, a in html.elementos if 'id' in a]
            self.assertEqual(len(ids), len(set(ids)))
            for valor in esperadas:
                modal_id = 'redondeo' + valor
                self.assertIn(modal_id, ids)
                self.assertIn(('button', modal_id), [
                    (tag, a.get('data-bs-target', '').removeprefix('#'))
                    for tag, a in html.elementos
                ])
                modal = next(a for _, a in html.elementos if a.get('id') == modal_id)
                self.assertEqual(modal['aria-labelledby'], 'redondeoTitulo' + valor)
                self.assertIn(modal['aria-labelledby'], ids)
                self.assertIn('confirmarRedondeo' + valor, ids)
                self.assertTrue(any(a.get('for') == 'confirmarRedondeo' + valor
                                    for _, a in html.elementos))
            for identificador in ids:
                if identificador.startswith(('redondeo', 'confirmarRedondeo')):
                    self.assertNotIn('.', identificador)
                    self.assertNotIn(',', identificador)

            completadas = set()
            for form in html.forms:
                valor = form['inputs']['cuota_id']
                self.assertEqual(form['action'], reverse(
                    'gestion:pago_reconciliar_redondeo', args=[esperadas[valor]],
                ))
                result = self.client.post(form['action'], form['inputs'])
                self.assertEqual(result.status_code, 302)
                completadas.add(int(valor))
                self.assertSetEqual(set(CuotaAmortizacion.objects.filter(
                    pk__in=esperadas, pagada=True,
                ).values_list('pk', flat=True)), completadas)
                self.assertEqual(self.eventos().count(), len(completadas))

    def test_ids_no_localizados_en_primera_cuota(self):
        self._verificar_ids_renderizados_y_post(numero_cuota=1)

    def test_ids_no_localizados_en_segunda_cuota(self):
        self._verificar_ids_renderizados_y_post(numero_cuota=2)

    def test_csrf_y_revalidacion_no_confian_en_preview(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        endpoint = reverse('gestion:pago_reconciliar_redondeo', args=[self.pago.pk])
        self.assertEqual(client.post(endpoint, {'cuota_id': self.cuota.pk, 'confirmar': 'si'}).status_code, 403)
        self.client.force_login(self.staff)
        self.client.get(reverse('gestion:obligaciones_pendientes'))
        self.cuota.monto_pagado = 100
        self.cuota.save(update_fields=['monto_pagado'])
        self.client.post(endpoint, {'cuota_id': self.cuota.pk, 'confirmar': 'si', 'diferencia': '0.20'})
        self.assertFalse(self.eventos().exists())

    def test_opciones_no_agregan_consultas_por_fila(self):
        request = RequestFactory().get('/gestion/obligaciones-pendientes/')
        request.user = self.staff
        credit_services.puede_reconciliar_redondeo(self.staff)
        with CaptureQueriesContext(connection) as inicial:
            get_admin_obligaciones_context(request)
        for _ in range(4):
            self.crear_caso()
        with CaptureQueriesContext(connection) as final:
            context = get_admin_obligaciones_context(request)
        self.assertEqual(len(context['obligaciones']), 5)
        self.assertTrue(all(o['reconciliacion'] for o in context['obligaciones']))
        self.assertEqual(len(final), len(inicial))


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real en VPS de pruebas.')
class ReconciliacionConcurrenciaPostgresTests(ReconciliacionFixture, TransactionTestCase):
    def test_dos_transacciones_reconcilian_una_sola_vez(self):
        barrera = Barrier(2)
        resultados, errores = Queue(), Queue()

        def ejecutar():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                barrera.wait(timeout=10)
                cuotas, _resumen = credit_services.reconciliar_pago_manual_por_tolerancia(
                    HistorialPago.objects.get(pk=self.pago.pk),
                    cuota=CuotaAmortizacion.objects.get(pk=self.cuota.pk),
                    usuario=User.objects.get(pk=self.staff.pk),
                )
                resultados.put(len(cuotas))
            except Exception as exc:
                errores.put(exc)
            finally:
                connections.close_all()

        hilos = [Thread(target=ejecutar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(list(errores.queue), [])
        self.assertCountEqual(list(resultados.queue), [0, 1])
        self.assertEqual(self.eventos().count(), 1)
        self.assertEqual(HistorialPago.objects.count(), 1)
        self.assertEqual(DetalleContablePago.objects.count(), 1)
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.monto, Decimal('99.80'))
        self.assertEqual(self.pago.notas.count('cerrada por tolerancia'), 1)

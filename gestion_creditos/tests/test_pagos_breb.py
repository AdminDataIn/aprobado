import io
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest import skipIf
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from gestion_creditos.models import (
    ConfiguracionPagoBREB,
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    DetalleContablePago,
    Empresa,
    HistorialPago,
    PagoBREB,
    PagoBREBDetalle,
)
from gestion_creditos.services.breb_payments import (
    aprobar_pago_breb,
    rechazar_pago_breb,
    reportar_pago_breb,
)
from usuarios.models import PerfilPagador


User = get_user_model()


class PagoBREBBaseMixin:
    @staticmethod
    def _pdf(nombre='comprobante.pdf', contenido=b'%PDF-1.4\ncomprobante valido'):
        return SimpleUploadedFile(nombre, contenido, content_type='application/pdf')

    @staticmethod
    def _imagen(nombre='qr.png'):
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'white').save(buffer, format='PNG')
        return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')

    def _crear_credito(self, numero, *, empresa=None, cuota='110000.00'):
        empresa = empresa or self.empresa
        usuario = User.objects.create_user(
            username=f'cliente-{numero.lower()}',
            email=f'{numero.lower()}@aprobado.test',
            password='test1234',
        )
        credito = Credito.objects.create(
            usuario=usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito=numero,
            monto_solicitado=Decimal('200000.00'),
            monto_aprobado=Decimal('200000.00'),
            plazo_solicitado=2,
            plazo=2,
            tasa_interes=Decimal('2.00'),
            comision=Decimal('0.00'),
            iva_comision=Decimal('0.00'),
            total_a_pagar=Decimal('220000.00'),
            saldo_pendiente=Decimal('220000.00'),
            capital_pendiente=Decimal('200000.00'),
            valor_cuota=Decimal(cuota),
            fecha_proximo_pago=date(2026, 9, 1),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa,
            nombres='CLIENTE',
            apellidos=numero[-4:],
            cedula=f'10{credito.pk:08d}',
            direccion='Direccion de prueba',
            telefono='3000000000',
            correo_electronico=usuario.email,
        )
        cuota_obj = CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 9, 1),
            capital_a_pagar=Decimal('100000.00'),
            interes_a_pagar=Decimal('10000.00'),
            valor_cuota=Decimal(cuota),
            saldo_capital_pendiente=Decimal('100000.00'),
            monto_pagado=Decimal('0.00'),
        )
        return credito, cuota_obj

    def _reportar(self, obligaciones=None, **kwargs):
        datos = {
            'empresa': self.empresa,
            'usuario': self.pagador,
            'configuracion': self.configuracion,
            'obligaciones': obligaciones or [
                {'credito_id': self.credito.pk, 'valor_reportado': '110000.00'}
            ],
            'fecha_pago_reportada': date(2026, 8, 29),
            'comprobante': self._pdf(),
            'referencia_reportada': 'TRANSFERENCIA-001',
            'notas': 'Pago de nomina agrupado.',
        }
        datos.update(kwargs)
        return reportar_pago_breb(**datos)

    def _crear_escenario_base(self):
        self.empresa = Empresa.objects.create(nombre='EMPRESA BREB', convenio_activo=True)
        self.pagador = User.objects.create_user(
            'pagador-breb', email='pagador@aprobado.test', password='test1234'
        )
        PerfilPagador.objects.create(usuario=self.pagador, empresa=self.empresa)
        self.revisor = User.objects.create_user(
            'revisor-breb', email='revisor@aprobado.test', password='test1234', is_staff=True
        )
        self.revisor.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        self.configuracion = ConfiguracionPagoBREB.objects.create(
            activo=True,
            nombre_receptor='APROBADO SAS',
            entidad_financiera='BANCO PRUEBA',
            tipo_llave=ConfiguracionPagoBREB.TipoLlave.ALFANUMERICA,
            llave_mostrable='APROBADO-TEST',
            qr=self._imagen(),
            monto_minimo=Decimal('1000.00'),
        )
        self.credito, self.cuota = self._crear_credito('CR-BREB-0001')


class PagoBREBAgrupadoTest(PagoBREBBaseMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp(prefix='aprobado-breb-private-')
        cls.override = override_settings(
            PRIVATE_DOCUMENTS_ROOT=cls.private_root,
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        )
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._crear_escenario_base()

    def test_reporte_agrupado_crea_cabecera_y_detalles_sin_afectar_cartera(self):
        segundo, segunda_cuota = self._crear_credito('CR-BREB-0002', cuota='90000.00')
        pago = self._reportar(obligaciones=[
            {'credito_id': self.credito.pk, 'valor_reportado': '110000.00'},
            {'credito_id': segundo.pk, 'valor_reportado': '90000.00'},
        ])

        self.assertIsNone(pago.credito_id)
        self.assertIsNone(pago.historial_pago_id)
        self.assertEqual(pago.valor_reportado, Decimal('200000.00'))
        self.assertEqual(pago.detalles.count(), 2)
        self.assertEqual(
            set(pago.detalles.values_list('cuota_id', flat=True)),
            {self.cuota.pk, segunda_cuota.pk},
        )
        self.assertFalse(HistorialPago.objects.exists())
        self.assertFalse(DetalleContablePago.objects.exists())
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.saldo_pendiente, Decimal('220000.00'))

    def test_snapshots_de_obligacion_son_correctos(self):
        detalle = self._reportar().detalles.get()
        self.assertEqual(detalle.numero_cuota_snapshot, 1)
        self.assertEqual(detalle.fecha_vencimiento_snapshot, date(2026, 9, 1))
        self.assertEqual(detalle.valor_cuota_snapshot, Decimal('110000.00'))

    def test_doble_post_mismo_comprobante_y_obligaciones_reutiliza_cabecera(self):
        primero = self._reportar()
        segundo = self._reportar()
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.assertEqual(PagoBREBDetalle.objects.count(), 1)

    def test_rechazado_permite_nuevo_intento_con_nueva_referencia(self):
        anterior = self._reportar(
            referencia_reportada='INTENTO-001',
            comprobante=self._pdf('intento-001.pdf', b'%PDF-1.4\nmisma evidencia'),
        )
        rechazar_pago_breb(
            pago_breb=anterior,
            usuario=self.revisor,
            motivo='La transferencia no pudo ser conciliada.',
        )

        nuevo = self._reportar(
            referencia_reportada='INTENTO-002',
            comprobante=self._pdf('intento-002.pdf', b'%PDF-1.4\nmisma evidencia'),
        )

        anterior.refresh_from_db()
        self.assertNotEqual(nuevo.pk, anterior.pk)
        self.assertEqual(anterior.estado, PagoBREB.Estado.RECHAZADO)
        self.assertEqual(nuevo.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertEqual(anterior.hash_comprobante, nuevo.hash_comprobante)
        self.assertNotEqual(anterior.fingerprint_reporte, nuevo.fingerprint_reporte)
        self.assertEqual(PagoBREB.objects.count(), 2)
        self.assertEqual(PagoBREBDetalle.objects.count(), 2)

        self.client.force_login(self.pagador)
        historial = self.client.get(reverse('pagador:dashboard'))
        ids = [item['id'] for item in historial.context['reportes_breb_ui']]
        self.assertEqual(ids[:2], [nuevo.pk, anterior.pk])
        self.assertEqual(historial.context['reportes_breb_ui'][0]['estado'], 'Pendiente de verificación')
        self.assertEqual(historial.context['reportes_breb_ui'][1]['estado'], 'Rechazado')

    def test_obligacion_pagada_no_admite_nuevo_reporte(self):
        pago = self._reportar(referencia_reportada='PAGO-APROBADO-001')
        aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)

        with self.assertRaisesRegex(
            ValidationError,
            'no admite pagos BRE-B en su estado actual',
        ):
            self._reportar(
                referencia_reportada='PAGO-POSTERIOR-002',
                comprobante=self._pdf('posterior.pdf', b'%PDF-1.4\nnuevo intento'),
            )

        self.assertEqual(PagoBREB.objects.count(), 1)

    def test_constraint_no_duplica_misma_cuota_y_permite_dos_cuotas_del_credito(self):
        pago = self._reportar()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagoBREBDetalle.objects.create(
                    pago_breb=pago,
                    credito=self.credito,
                    cuota=self.cuota,
                    valor_reportado=Decimal('1.00'),
                )
        cuota_dos = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 10, 1),
            capital_a_pagar=Decimal('100000.00'),
            interes_a_pagar=Decimal('10000.00'),
            valor_cuota=Decimal('110000.00'),
            saldo_capital_pendiente=Decimal('0.00'),
        )
        PagoBREBDetalle.objects.create(
            pago_breb=pago,
            credito=self.credito,
            cuota=cuota_dos,
            valor_reportado=Decimal('110000.00'),
        )
        self.assertEqual(pago.detalles.filter(credito=self.credito).count(), 2)

    def test_servicio_admite_varias_cuotas_distintas_del_mismo_credito(self):
        cuota_dos = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 10, 1),
            capital_a_pagar=Decimal('100000.00'),
            interes_a_pagar=Decimal('10000.00'),
            valor_cuota=Decimal('110000.00'),
            saldo_capital_pendiente=Decimal('0.00'),
        )
        pago = self._reportar(obligaciones=[
            {'credito_id': self.credito.pk, 'cuota_id': self.cuota.pk, 'valor_reportado': '110000.00'},
            {'credito_id': self.credito.pk, 'cuota_id': cuota_dos.pk, 'valor_reportado': '110000.00'},
        ])
        self.assertEqual(pago.detalles.count(), 2)
        self.assertEqual(pago.valor_reportado, Decimal('220000.00'))

        aprobado, aplicado = aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)

        self.assertTrue(aplicado)
        self.assertEqual(aprobado.detalles.filter(historial_pago__isnull=False).count(), 2)
        self.assertEqual(HistorialPago.objects.filter(credito=self.credito).count(), 2)

    def test_aprobacion_rechaza_cuota_posterior_si_la_anterior_sigue_pendiente(self):
        cuota_dos = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 10, 1),
            capital_a_pagar=Decimal('100000.00'),
            interes_a_pagar=Decimal('10000.00'),
            valor_cuota=Decimal('110000.00'),
            saldo_capital_pendiente=Decimal('0.00'),
        )
        pago = self._reportar(obligaciones=[{
            'credito_id': self.credito.pk,
            'cuota_id': cuota_dos.pk,
            'valor_reportado': '110000.00',
        }])

        with self.assertRaisesRegex(ValidationError, 'ya no es la obligacion exigible'):
            aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)

        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertFalse(HistorialPago.objects.exists())

    def test_pagador_no_puede_incluir_credito_de_otra_empresa(self):
        otra = Empresa.objects.create(nombre='OTRA EMPRESA', convenio_activo=True)
        credito_ajeno, _ = self._crear_credito('CR-BREB-AJENO', empresa=otra)
        with self.assertRaises(PermissionDenied):
            self._reportar(obligaciones=[{'credito_id': credito_ajeno.pk, 'valor_reportado': '10.00'}])
        self.assertFalse(PagoBREB.objects.exists())

    def test_no_confia_en_valor_frontend_superior_a_obligacion(self):
        with self.assertRaises(ValidationError):
            self._reportar(obligaciones=[
                {'credito_id': self.credito.pk, 'valor_reportado': '110000.01'}
            ])
        self.assertFalse(PagoBREB.objects.exists())

    def test_pagador_con_permiso_accidental_no_puede_revisar(self):
        pago = self._reportar()
        self.pagador.is_staff = True
        self.pagador.save(update_fields=['is_staff'])
        self.pagador.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        with self.assertRaises(PermissionDenied):
            aprobar_pago_breb(pago_breb=pago, usuario=self.pagador)
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)

    def test_staff_sin_permiso_no_revisa(self):
        pago = self._reportar()
        staff = User.objects.create_user('staff-sin-permiso', is_staff=True)
        with self.assertRaises(PermissionDenied):
            aprobar_pago_breb(pago_breb=pago, usuario=staff)

    def test_aprobacion_agrupada_aplica_todos_los_detalles(self):
        segundo, _ = self._crear_credito('CR-BREB-0003', cuota='90000.00')
        pago = self._reportar(obligaciones=[
            {'credito_id': self.credito.pk, 'valor_reportado': '110000.00'},
            {'credito_id': segundo.pk, 'valor_reportado': '90000.00'},
        ])

        aprobado, creado = aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)

        self.assertTrue(creado)
        self.assertEqual(aprobado.estado, PagoBREB.Estado.APROBADO)
        self.assertEqual(aprobado.valor_aprobado, Decimal('200000.00'))
        self.assertEqual(HistorialPago.objects.count(), 2)
        self.assertEqual(aprobado.detalles.filter(historial_pago__isnull=False).count(), 2)
        self.assertEqual(DetalleContablePago.objects.count(), 2)

        repetido, creado_repetido = aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)
        self.assertFalse(creado_repetido)
        self.assertEqual(repetido.pk, aprobado.pk)
        self.assertEqual(HistorialPago.objects.count(), 2)

    def test_valores_corregidos_se_auditan_por_detalle_y_total(self):
        pago = self._reportar(obligaciones=[
            {'credito_id': self.credito.pk, 'valor_reportado': '110000.00'}
        ])
        detalle = pago.detalles.get()
        aprobado, _ = aprobar_pago_breb(
            pago_breb=pago,
            usuario=self.revisor,
            valores_aprobados={detalle.pk: Decimal('100000.00')},
        )
        detalle.refresh_from_db()
        self.assertEqual(detalle.valor_reportado, Decimal('110000.00'))
        self.assertEqual(detalle.valor_aprobado, Decimal('100000.00'))
        self.assertEqual(aprobado.valor_aprobado, Decimal('100000.00'))

    def test_fallo_en_segundo_detalle_revierte_toda_la_aplicacion(self):
        segundo, _ = self._crear_credito('CR-BREB-0004', cuota='90000.00')
        pago = self._reportar(obligaciones=[
            {'credito_id': self.credito.pk, 'valor_reportado': '110000.00'},
            {'credito_id': segundo.pk, 'valor_reportado': '90000.00'},
        ])
        funcion_real = __import__(
            'gestion_creditos.credit_services', fromlist=['registrar_pago_credito']
        ).registrar_pago_credito
        llamadas = {'total': 0}

        def aplicar_y_fallar(**kwargs):
            llamadas['total'] += 1
            if llamadas['total'] == 2:
                raise RuntimeError('fallo en detalle dos')
            return funcion_real(**kwargs)

        with patch(
            'gestion_creditos.services.breb_payments.credit_services.registrar_pago_credito',
            side_effect=aplicar_y_fallar,
        ):
            with self.assertRaisesRegex(RuntimeError, 'fallo en detalle dos'):
                aprobar_pago_breb(pago_breb=pago, usuario=self.revisor)

        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertIsNone(pago.valor_aprobado)
        self.assertFalse(HistorialPago.objects.exists())
        self.assertFalse(PagoBREBDetalle.objects.filter(historial_pago__isnull=False).exists())

    def test_rechazo_no_modifica_cartera(self):
        pago = self._reportar()
        rechazado = rechazar_pago_breb(
            pago_breb=pago,
            usuario=self.revisor,
            motivo='El comprobante no coincide con la transferencia.',
        )
        self.assertEqual(rechazado.estado, PagoBREB.Estado.RECHAZADO)
        self.assertFalse(HistorialPago.objects.exists())

    def test_dashboard_pagador_muestra_breb_solo_activo_y_reporta_por_post(self):
        self.client.force_login(self.pagador)
        activo = self.client.get(reverse('pagador:dashboard'))
        self.assertContains(activo, 'BRE-B')
        self.assertContains(activo, 'breb-config-data')
        self.assertContains(activo, '/static/images/logo-breb.png')
        metodo = activo.context['pago_obligaciones_form'].fields['metodo_pago']
        self.assertEqual(list(metodo.choices)[0][0], HistorialPago.MetodoPago.BREB)
        self.assertEqual(metodo.initial, HistorialPago.MetodoPago.BREB)

        response = self.client.post(reverse('pagador:pagar_obligaciones'), {
            'obligaciones': [str(self.credito.pk)],
            f'monto_{self.credito.pk}': '110000.00',
            'metodo_pago': HistorialPago.MetodoPago.BREB,
            'fecha_pago_reportada': '2026-08-29',
            'referencia_reportada': 'WEB-BREB-001',
            'comprobante': self._pdf('web.pdf'),
            'nota': 'Pago agrupado desde dashboard.',
        })
        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.assertFalse(HistorialPago.objects.exists())
        confirmacion = self.client.get(reverse('pagador:dashboard'))
        self.assertContains(
            confirmacion,
            'Pago BRE-B reportado por $110.000,00. Quedó pendiente de verificación.',
        )
        self.assertContains(confirmacion, 'breb-toast')

        self.configuracion.activo = False
        self.configuracion.save(update_fields=['activo'])
        inactivo = self.client.get(reverse('pagador:dashboard'))
        self.assertNotContains(inactivo, '<option value="BREB">', html=False)
        self.assertEqual(inactivo.context['reportes_breb_ui'][0]['id'], PagoBREB.objects.get().pk)
        self.assertEqual(
            inactivo.context['reportes_breb_ui'][0]['estado'],
            PagoBREB.Estado.PENDIENTE_VERIFICACION.label,
        )

    def test_reporte_web_breb_requiere_comprobante(self):
        self.client.force_login(self.pagador)
        response = self.client.post(reverse('pagador:pagar_obligaciones'), {
            'obligaciones': [str(self.credito.pk)],
            f'monto_{self.credito.pk}': '110000.00',
            'metodo_pago': HistorialPago.MetodoPago.BREB,
            'fecha_pago_reportada': '2026-08-29',
            'nota': 'Sin comprobante.',
        })
        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertFalse(PagoBREB.objects.exists())

    def test_colaborador_no_tiene_cta_ni_rutas_breb(self):
        self.client.force_login(self.credito.usuario)
        response = self.client.get(reverse('libranza:mi_credito_detalle', args=[self.credito.pk]))
        self.assertNotContains(response, 'Pagar por BRE-B')
        with self.assertRaises(NoReverseMatch):
            reverse('libranza:pago_breb', args=[self.credito.pk])

    def test_bandeja_es_exclusiva_staff_con_permiso_y_muestra_detalles(self):
        pago = self._reportar()
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(reverse('gestion:pagos_breb')).status_code, 403)
        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pagos_breb'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'creditos/css/base_styles.css')
        self.assertContains(response, '/static/images/logo-breb.png')
        self.assertNotContains(response, '<div class="div-body-main">', html=False)
        self.assertContains(response, pago.detalles.get().credito.numero_credito)
        self.assertContains(response, 'Aprobar y aplicar todo')

        dashboard = self.client.get(reverse('gestion:dashboard'))
        self.assertContains(dashboard, 'Pagos BRE-B')
        self.assertContains(dashboard, '/static/images/logo-breb.png')
        self.assertContains(dashboard, 'breb-action-logo')

    def test_rechazo_admin_usa_toast_y_tarjeta_resuelta_sin_acciones(self):
        pago = self._reportar()
        self.client.force_login(self.revisor)
        response = self.client.post(
            reverse('gestion:pago_breb_decidir', args=[pago.pk]),
            {
                'accion': 'rechazar',
                'motivo_rechazo': 'La referencia no coincide con el comprobante.',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'breb-toast')
        self.assertContains(response, 'El reporte BRE-B fue rechazado sin modificar la cartera.')
        self.assertContains(response, 'Motivo de rechazo')
        self.assertContains(response, 'La referencia no coincide con el comprobante.')
        self.assertContains(response, 'breb-obligation-details')
        self.assertNotContains(response, 'Aprobar y aplicar todo')

    def test_aprobacion_admin_usa_toast_y_oculta_acciones_resueltas(self):
        pago = self._reportar()
        detalle = pago.detalles.get()
        self.client.force_login(self.revisor)
        response = self.client.post(
            reverse('gestion:pago_breb_decidir', args=[pago.pk]),
            {
                'accion': 'aprobar',
                f'valor_detalle_{detalle.pk}': '110000.00',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'breb-toast success')
        self.assertContains(
            response,
            'El pago BRE-B agrupado fue verificado y aplicado completamente.',
        )
        self.assertContains(response, 'breb-obligation-details')
        self.assertNotContains(response, 'Aprobar y aplicar todo')

    def test_bandeja_prioriza_pendientes_y_ordena_resueltos_por_fecha(self):
        credito_aprobado, _ = self._crear_credito('CR-BREB-ORD-01')
        aprobado = self._reportar(
            obligaciones=[{'credito_id': credito_aprobado.pk, 'valor_reportado': '110000.00'}],
            comprobante=self._pdf('aprobado.pdf', b'%PDF-1.4\naprobado'),
            referencia_reportada='ORD-APROBADO',
        )
        PagoBREB.objects.filter(pk=aprobado.pk).update(
            estado=PagoBREB.Estado.APROBADO,
            valor_aprobado=Decimal('110000.00'),
            creado_en=timezone.now() - timedelta(days=2),
        )

        credito_rechazado, _ = self._crear_credito('CR-BREB-ORD-02')
        rechazado = self._reportar(
            obligaciones=[{'credito_id': credito_rechazado.pk, 'valor_reportado': '110000.00'}],
            comprobante=self._pdf('rechazado.pdf', b'%PDF-1.4\nrechazado'),
            referencia_reportada='ORD-RECHAZADO',
        )
        PagoBREB.objects.filter(pk=rechazado.pk).update(
            estado=PagoBREB.Estado.RECHAZADO,
            motivo_rechazo='No coincide.',
            creado_en=timezone.now() - timedelta(days=1),
        )

        pendiente = self._reportar(referencia_reportada='ORD-PENDIENTE')
        PagoBREB.objects.filter(pk=pendiente.pk).update(
            creado_en=timezone.now() - timedelta(days=3)
        )

        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pagos_breb'))
        ids = [item.pk for item in response.context['pagos_breb'].object_list]
        self.assertEqual(ids[:3], [pendiente.pk, rechazado.pk, aprobado.pk])

    def test_bandeja_pagina_diez_y_conserva_filtros(self):
        for indice in range(11):
            credito, _ = self._crear_credito(f'CR-BREB-PG-{indice:02d}')
            self._reportar(
                obligaciones=[{'credito_id': credito.pk, 'valor_reportado': '110000.00'}],
                comprobante=self._pdf(
                    f'pagina-{indice}.pdf', f'%PDF-1.4\npagina-{indice}'.encode()
                ),
                referencia_reportada=f'PAGE-{indice:02d}',
            )

        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pagos_breb'), {
            'estado': PagoBREB.Estado.PENDIENTE_VERIFICACION,
            'empresa': str(self.empresa.pk),
            'referencia': 'PAGE-',
            'page': '1',
        })

        pagina = response.context['pagos_breb']
        self.assertEqual(len(pagina.object_list), 10)
        self.assertEqual(pagina.paginator.count, 11)
        self.assertContains(response, 'Siguiente')
        querystring = response.context['querystring_without_page']
        self.assertIn('estado=PENDIENTE_VERIFICACION', querystring)
        self.assertIn(f'empresa={self.empresa.pk}', querystring)
        self.assertIn('referencia=PAGE-', querystring)
        self.assertNotIn('page=', querystring)

    def test_filtros_adicionales_breb_se_aplican_en_queryset(self):
        pago = self._reportar(referencia_reportada='FILTRO-UNICO-2026')
        hoy = timezone.localdate().isoformat()
        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pagos_breb'), {
            'empresa': str(self.empresa.pk),
            'referencia': 'UNICO-2026',
            'reportado_por': 'pagador@aprobado.test',
            'fecha_desde': hoy,
            'fecha_hasta': hoy,
        })

        self.assertEqual(
            [item.pk for item in response.context['pagos_breb'].object_list],
            [pago.pk],
        )

    @override_settings(WOMPI_PAYMENTS_ENABLED=False)
    def test_wompi_inactivo_oculta_cta_y_bloquea_endpoint(self):
        self.client.force_login(self.pagador)
        detalle = self.client.get(reverse('pagador:credito_detalle', args=[self.credito.pk]))
        self.assertNotContains(detalle, 'Pagar Cuota con WOMPI')
        inicio = self.client.get(reverse('pagador:pagar_wompi', args=[self.credito.pk]))
        self.assertRedirects(inicio, reverse('pagador:dashboard'), fetch_redirect_response=False)

    @override_settings(WOMPI_PAYMENTS_ENABLED=True)
    def test_wompi_activo_conserva_flujo_historico(self):
        self.client.force_login(self.pagador)
        detalle = self.client.get(reverse('pagador:credito_detalle', args=[self.credito.pk]))
        self.assertContains(detalle, 'Pagar Cuota con WOMPI')
        with patch('gestion_creditos.services.wompi_client.WompiClient.get_acceptance_token') as token, patch(
            'gestion_creditos.services.wompi_client.WompiClient.get_pse_financial_institutions'
        ) as bancos:
            token.return_value = {'data': {'presigned_acceptance': {'acceptance_token': 'token'}}}
            bancos.return_value = []
            inicio = self.client.get(reverse('pagador:pagar_wompi', args=[self.credito.pk]))
        self.assertEqual(inicio.status_code, 200)


# Closing a FileResponse emits request_finished; PostgreSQL must be free to
# close and reopen its connection outside TestCase's class-level transaction.
class PagoBREBArchivosPrivadosTest(PagoBREBBaseMixin, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp(prefix='aprobado-breb-archivos-')
        cls.override = override_settings(PRIVATE_DOCUMENTS_ROOT=cls.private_root)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._crear_escenario_base()

    def test_pagador_consulta_solo_comprobante_propio(self):
        pago = self._reportar()
        self.client.force_login(self.pagador)
        response = self.client.get(reverse('pagador:pago_breb_comprobante', args=[pago.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()

        otro = User.objects.create_user('otro-pagador')
        PerfilPagador.objects.create(usuario=otro, empresa=self.empresa)
        self.client.force_login(otro)
        self.assertEqual(
            self.client.get(reverse('pagador:pago_breb_comprobante', args=[pago.pk])).status_code,
            403,
        )

    def test_comprobante_admin_tiene_vista_inline_y_descarga_protegidas(self):
        pago = self._reportar()
        self.client.force_login(self.revisor)

        preview = self.client.get(
            reverse('gestion:pago_breb_comprobante_preview', args=[pago.pk])
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['Content-Type'], 'application/pdf')
        self.assertIn('inline;', preview['Content-Disposition'])
        preview.close()

        download = self.client.get(reverse('gestion:pago_breb_comprobante', args=[pago.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment;', download['Content-Disposition'])
        download.close()

        self.client.force_login(self.pagador)
        self.assertEqual(
            self.client.get(
                reverse('gestion:pago_breb_comprobante_preview', args=[pago.pk])
            ).status_code,
            403,
        )

    def test_previsualizacion_admin_identifica_imagen_sin_url_publica(self):
        credito, _ = self._crear_credito('CR-BREB-IMG-01')
        pago = self._reportar(
            obligaciones=[{'credito_id': credito.pk, 'valor_reportado': '110000.00'}],
            comprobante=self._imagen('comprobante.png'),
            referencia_reportada='IMAGEN-001',
        )
        self.client.force_login(self.revisor)

        bandeja = self.client.get(reverse('gestion:pagos_breb'))
        self.assertContains(bandeja, 'data-preview-kind="image"')
        preview = self.client.get(
            reverse('gestion:pago_breb_comprobante_preview', args=[pago.pk])
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['Content-Type'], 'image/png')
        self.assertIn('inline;', preview['Content-Disposition'])
        self.assertNotIn('/private_documents/', bandeja.content.decode())
        preview.close()


@skipIf(connection.vendor != 'postgresql', 'Requiere PostgreSQL real.')
class PagoBREBConcurrenciaPostgresTest(PagoBREBBaseMixin, TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp(prefix='aprobado-breb-concurrente-')
        cls.override = override_settings(PRIVATE_DOCUMENTS_ROOT=cls.private_root)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='EMPRESA BREB CONCURRENTE', convenio_activo=True)
        self.pagador = User.objects.create_user('pagador-breb-concurrente')
        PerfilPagador.objects.create(usuario=self.pagador, empresa=self.empresa)
        self.revisor = User.objects.create_user('revisor-breb-concurrente', is_staff=True)
        self.revisor.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        self.configuracion = ConfiguracionPagoBREB.objects.create(
            activo=True,
            nombre_receptor='APROBADO SAS',
            entidad_financiera='BANCO PRUEBA',
            tipo_llave=ConfiguracionPagoBREB.TipoLlave.ALFANUMERICA,
            llave_mostrable='APROBADO-CONCURRENTE',
            qr=self._imagen(),
        )
        self.credito, _ = self._crear_credito('CR-BREB-CONC')
        self.pago = self._reportar()

    def test_doble_aprobacion_agrupada_concurrente_no_duplica_movimientos(self):
        barrera = Barrier(2)
        resultados = Queue()
        errores = Queue()

        def aprobar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                pago, creado = aprobar_pago_breb(
                    pago_breb=PagoBREB.objects.get(pk=self.pago.pk),
                    usuario=User.objects.get(pk=self.revisor.pk),
                )
                resultados.put((pago.pk, creado))
            except Exception as exc:
                errores.put(exc)
            finally:
                close_old_connections()

        hilos = [Thread(target=aprobar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(list(errores.queue), [])
        self.assertCountEqual([item[1] for item in resultados.queue], [True, False])
        self.assertEqual(HistorialPago.objects.count(), 1)
        self.assertEqual(PagoBREBDetalle.objects.filter(historial_pago__isnull=False).count(), 1)

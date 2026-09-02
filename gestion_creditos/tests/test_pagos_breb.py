import io
import shutil
import tempfile
from queue import Queue
from threading import Barrier, Thread
from unittest import skipIf
from unittest.mock import patch
from datetime import date
from decimal import Decimal
from pathlib import Path

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import (
    ConfiguracionPagoBREB,
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    DetalleContablePago,
    Empresa,
    HistorialPago,
    PagoBREB,
)
from gestion_creditos.services.breb_payments import (
    aprobar_pago_breb,
    rechazar_pago_breb,
    reportar_pago_breb,
)
from usuarios.models import PerfilPagador


User = get_user_model()


class PagoBREBTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp(prefix='aprobado-breb-private-')
        cls.media_root = tempfile.mkdtemp(prefix='aprobado-breb-media-')
        cls.override = override_settings(
            PRIVATE_DOCUMENTS_ROOT=cls.private_root,
            MEDIA_ROOT=cls.media_root,
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        )
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.usuario = User.objects.create_user(
            username='cliente-breb', email='cliente-breb@aprobado.test', password='test1234'
        )
        self.otro_usuario = User.objects.create_user(
            username='otro-breb', email='otro-breb@aprobado.test', password='test1234'
        )
        self.revisor = User.objects.create_user(
            username='revisor-breb', email='revisor-breb@aprobado.test',
            password='test1234', is_staff=True,
        )
        permiso = Permission.objects.get(codename='review_pagobreb')
        self.revisor.user_permissions.add(permiso)
        self.empresa = Empresa.objects.create(nombre='EMPRESA BREB', convenio_activo=True)
        self.configuracion = ConfiguracionPagoBREB.objects.create(
            activo=True,
            nombre_receptor='APROBADO SAS',
            entidad_financiera='BANCO PRUEBA',
            tipo_llave=ConfiguracionPagoBREB.TipoLlave.ALFANUMERICA,
            llave_mostrable='APROBADO-TEST',
            qr=self._imagen('qr.png'),
            monto_minimo=Decimal('1000.00'),
        )
        self.credito = Credito.objects.create(
            usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-BREB-0001',
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
            valor_cuota=Decimal('110000.00'),
            fecha_proximo_pago=date(2026, 9, 1),
        )
        CreditoLibranza.objects.create(
            credito=self.credito,
            empresa=self.empresa,
            nombres='CLIENTE',
            apellidos='BREB',
            cedula='1000000001',
            direccion='Dirección de prueba',
            telefono='3000000000',
            correo_electronico=self.usuario.email,
            cedula_frontal=self._pdf('frontal.pdf'),
            cedula_trasera=self._pdf('trasera.pdf'),
            certificado_bancario=self._pdf('certificado.pdf'),
        )
        for numero, fecha in ((1, date(2026, 9, 1)), (2, date(2026, 10, 1))):
            CuotaAmortizacion.objects.create(
                credito=self.credito,
                numero_cuota=numero,
                fecha_vencimiento=fecha,
                capital_a_pagar=Decimal('100000.00'),
                interes_a_pagar=Decimal('10000.00'),
                valor_cuota=Decimal('110000.00'),
                saldo_capital_pendiente=Decimal('100000.00') if numero == 1 else Decimal('0.00'),
            )

    @staticmethod
    def _pdf(nombre='comprobante.pdf', contenido=b'%PDF-1.4\nprueba'):
        return SimpleUploadedFile(nombre, contenido, content_type='application/pdf')

    @staticmethod
    def _imagen(nombre='imagen.png'):
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'white').save(buffer, format='PNG')
        return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')

    def _reportar(self, **kwargs):
        datos = {
            'credito': self.credito,
            'usuario': self.usuario,
            'configuracion': self.configuracion,
            'valor_reportado': Decimal('110000.00'),
            'fecha_pago_reportada': date(2026, 8, 29),
            'comprobante': self._pdf(),
            'referencia_reportada': 'REFERENCIA USUARIO',
        }
        datos.update(kwargs)
        return reportar_pago_breb(**datos)

    def test_reportar_crea_pendiente_sin_modificar_cartera(self):
        pago = self._reportar()

        self.credito.refresh_from_db()
        self.assertEqual(pago.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertEqual(self.credito.saldo_pendiente, Decimal('220000.00'))
        self.assertFalse(HistorialPago.objects.filter(credito=self.credito).exists())
        self.assertFalse(DetalleContablePago.objects.filter(credito=self.credito).exists())
        self.assertNotIn('comprobante.pdf', pago.comprobante.name)
        with self.assertRaises(ValueError):
            _ = pago.comprobante.url

    def test_doble_envio_del_mismo_comprobante_reutiliza_reporte(self):
        primero = self._reportar()
        segundo = self._reportar(referencia_reportada='OTRO CLICK DEL MISMO FORMULARIO')

        self.assertEqual(segundo.pk, primero.pk)
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.assertFalse(HistorialPago.objects.exists())

    def test_otro_usuario_no_puede_reportar_pago(self):
        with self.assertRaises(PermissionDenied):
            self._reportar(usuario=self.otro_usuario)
        self.assertFalse(PagoBREB.objects.exists())

    def test_configuracion_inactiva_no_admite_reporte_y_oculta_opcion(self):
        self.configuracion.activo = False
        self.configuracion.save(update_fields=['activo'])
        with self.assertRaises(ValidationError):
            self._reportar()

        self.client.force_login(self.usuario)
        response = self.client.get(reverse('libranza:mi_credito_detalle', args=[self.credito.id]))
        self.assertNotContains(response, 'Pagar por BRE-B')

    def test_sin_configuracion_activa_no_muestra_opcion_ni_falla(self):
        self.configuracion.delete()
        self.client.force_login(self.usuario)

        dashboard = self.client.get(reverse('libranza:mi_credito_detalle', args=[self.credito.id]))
        reporte = self.client.get(reverse('libranza:pago_breb', args=[self.credito.id]))

        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, 'Pagar por BRE-B')
        self.assertEqual(reporte.status_code, 302)

    def test_permiso_revision_se_crea_para_modelo_pagobreb(self):
        permiso = Permission.objects.get(
            content_type__app_label='gestion_creditos',
            codename='review_pagobreb',
        )
        self.assertEqual(permiso.content_type.model, 'pagobreb')

    def test_comando_configuracion_es_idempotente_y_no_expone_llave(self):
        self.configuracion.delete()
        ruta_qr = Path(self.private_root) / 'fuente-qr.png'
        ruta_qr.write_bytes(self._imagen('fuente-qr.png').read())
        argumentos = (
            '--qr', str(ruta_qr),
            '--receptor', 'APROBADO',
            '--entidad', 'DAVIPLATA',
            '--tipo-llave', ConfiguracionPagoBREB.TipoLlave.ALFANUMERICA,
            '--llave', 'LLAVE-PRUEBA-SEGURA',
            '--instrucciones', 'Escanea el QR y conserva el comprobante.',
            '--activar',
        )
        salida = io.StringIO()

        call_command('configurar_pago_breb', *argumentos, stdout=salida)
        call_command('configurar_pago_breb', *argumentos, stdout=salida)

        self.assertEqual(ConfiguracionPagoBREB.objects.count(), 1)
        configuracion = ConfiguracionPagoBREB.objects.get()
        self.assertTrue(configuracion.activo)
        self.assertEqual(len(configuracion.hash_qr), 64)
        self.assertNotIn('LLAVE-PRUEBA-SEGURA', salida.getvalue())

    def test_solo_puede_existir_una_configuracion_activa(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConfiguracionPagoBREB.objects.create(
                    activo=True,
                    nombre_receptor='OTRO RECEPTOR',
                    entidad_financiera='OTRA ENTIDAD',
                    tipo_llave=ConfiguracionPagoBREB.TipoLlave.CORREO,
                    llave_mostrable='otra@aprobado.test',
                    qr=self._imagen('otro-qr.png'),
                )
        self.assertEqual(ConfiguracionPagoBREB.objects.filter(activo=True).count(), 1)

    def test_archivo_con_extension_pdf_y_contenido_falso_es_rechazado(self):
        with self.assertRaises(ValidationError):
            self._reportar(comprobante=self._pdf(contenido=b'<html>no es pdf</html>'))

    def test_archivo_mayor_a_ocho_mb_es_rechazado(self):
        contenido = b'%PDF-' + (b'0' * (8 * 1024 * 1024))
        with self.assertRaises(ValidationError):
            self._reportar(comprobante=self._pdf(contenido=contenido))

    def test_aprobar_reutiliza_motor_contable_y_es_idempotente(self):
        reporte = self._reportar()
        aprobado, creado = aprobar_pago_breb(
            pago_breb=reporte,
            usuario=self.revisor,
            valor_aprobado=Decimal('110000.00'),
        )
        self.assertTrue(creado)
        self.assertEqual(aprobado.estado, PagoBREB.Estado.APROBADO)
        self.assertIsNotNone(aprobado.historial_pago_id)
        self.assertEqual(aprobado.historial_pago.metodo_pago, HistorialPago.MetodoPago.BREB)
        self.assertEqual(aprobado.historial_pago.origen_registro, HistorialPago.OrigenRegistro.REPORTE_BREB)
        self.assertEqual(HistorialPago.objects.filter(credito=self.credito).count(), 1)
        self.assertTrue(DetalleContablePago.objects.filter(pago=aprobado.historial_pago).exists())

        repetido, creado_repetido = aprobar_pago_breb(
            pago_breb=reporte,
            usuario=self.revisor,
            valor_aprobado=Decimal('110000.00'),
        )
        self.assertFalse(creado_repetido)
        self.assertEqual(repetido.historial_pago_id, aprobado.historial_pago_id)
        self.assertEqual(HistorialPago.objects.filter(credito=self.credito).count(), 1)

    def test_fallo_del_motor_revierte_pago_saldo_y_cuotas(self):
        reporte = self._reportar()
        saldo_inicial = self.credito.saldo_pendiente
        cuota = self.credito.tabla_amortizacion.get(numero_cuota=1)

        def aplicar_parcial_y_fallar(**kwargs):
            HistorialPago.objects.create(
                credito=kwargs['credito'],
                monto=kwargs['monto'],
                referencia_pago=kwargs['referencia_pago'],
                estado=HistorialPago.EstadoPago.EXITOSO,
            )
            Credito.objects.filter(pk=self.credito.pk).update(saldo_pendiente=Decimal('1.00'))
            CuotaAmortizacion.objects.filter(pk=cuota.pk).update(pagada=True)
            raise RuntimeError('Fallo financiero simulado')

        with patch(
            'gestion_creditos.services.breb_payments.credit_services.registrar_pago_credito',
            side_effect=aplicar_parcial_y_fallar,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Fallo financiero simulado'):
                aprobar_pago_breb(
                    pago_breb=reporte,
                    usuario=self.revisor,
                    valor_aprobado=Decimal('110000.00'),
                )

        reporte.refresh_from_db()
        self.credito.refresh_from_db()
        cuota.refresh_from_db()
        self.assertEqual(reporte.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertIsNone(reporte.historial_pago_id)
        self.assertIsNone(reporte.valor_aprobado)
        self.assertEqual(self.credito.saldo_pendiente, saldo_inicial)
        self.assertFalse(cuota.pagada)
        self.assertFalse(HistorialPago.objects.exists())

    def test_constraint_impide_aprobado_sin_pago_financiero(self):
        reporte = self._reportar()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagoBREB.objects.filter(pk=reporte.pk).update(
                    estado=PagoBREB.Estado.APROBADO,
                    valor_aprobado=Decimal('110000.00'),
                )
        reporte.refresh_from_db()
        self.assertEqual(reporte.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)

    def test_aprobacion_con_valor_corregido_conserva_ambos_valores(self):
        reporte = self._reportar(valor_reportado=Decimal('109000.00'))
        aprobado, _ = aprobar_pago_breb(
            pago_breb=reporte,
            usuario=self.revisor,
            valor_aprobado=Decimal('110000.00'),
        )
        self.assertEqual(aprobado.valor_reportado, Decimal('109000.00'))
        self.assertEqual(aprobado.valor_aprobado, Decimal('110000.00'))
        self.assertEqual(aprobado.historial_pago.monto, Decimal('110000.00'))

    def test_usuario_sin_permiso_no_puede_aprobar(self):
        reporte = self._reportar()
        with self.assertRaises(PermissionDenied):
            aprobar_pago_breb(
                pago_breb=reporte,
                usuario=self.otro_usuario,
                valor_aprobado=Decimal('110000.00'),
            )
        reporte.refresh_from_db()
        self.assertEqual(reporte.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertFalse(HistorialPago.objects.exists())

    def test_usuario_no_puede_aprobar_su_propio_reporte(self):
        self.usuario.is_staff = True
        self.usuario.save(update_fields=['is_staff'])
        self.usuario.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        reporte = self._reportar()

        with self.assertRaises(PermissionDenied):
            aprobar_pago_breb(
                pago_breb=reporte,
                usuario=self.usuario,
                valor_aprobado=Decimal('110000.00'),
            )
        reporte.refresh_from_db()
        self.assertEqual(reporte.estado, PagoBREB.Estado.PENDIENTE_VERIFICACION)
        self.assertFalse(HistorialPago.objects.exists())

    def test_pagador_autorizado_solo_revisa_pagos_de_su_empresa(self):
        pagador = User.objects.create_user(
            username='pagador-breb', email='pagador-breb@aprobado.test', password='test1234'
        )
        pagador.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        reporte = self._reportar()

        aprobado, creado = aprobar_pago_breb(
            pago_breb=reporte,
            usuario=pagador,
            valor_aprobado=Decimal('110000.00'),
        )
        self.assertTrue(creado)
        self.assertEqual(aprobado.revisado_por_id, pagador.id)

        otra_empresa = Empresa.objects.create(nombre='OTRA EMPRESA BREB', convenio_activo=True)
        otro_credito = Credito.objects.create(
            usuario=self.otro_usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-BREB-0002',
            monto_solicitado=Decimal('100000.00'),
            monto_aprobado=Decimal('100000.00'),
            plazo_solicitado=1,
            plazo=1,
            saldo_pendiente=Decimal('100000.00'),
            capital_pendiente=Decimal('100000.00'),
            valor_cuota=Decimal('100000.00'),
        )
        CreditoLibranza.objects.create(
            credito=otro_credito,
            empresa=otra_empresa,
            nombres='OTRO',
            apellidos='CLIENTE',
            cedula='1000000002',
            direccion='Direccion de prueba',
            telefono='3000000001',
            correo_electronico=self.otro_usuario.email,
            cedula_frontal=self._pdf('otra-frontal.pdf'),
            cedula_trasera=self._pdf('otra-trasera.pdf'),
            certificado_bancario=self._pdf('otro-certificado.pdf'),
        )
        otro_reporte = reportar_pago_breb(
            credito=otro_credito,
            usuario=self.otro_usuario,
            configuracion=self.configuracion,
            valor_reportado=Decimal('100000.00'),
            fecha_pago_reportada=date(2026, 8, 29),
            comprobante=self._pdf('otro-comprobante.pdf'),
        )
        with self.assertRaises(PermissionDenied):
            aprobar_pago_breb(
                pago_breb=otro_reporte,
                usuario=pagador,
                valor_aprobado=Decimal('100000.00'),
            )

    def test_rechazo_requiere_motivo_y_no_modifica_cartera(self):
        reporte = self._reportar()
        with self.assertRaises(ValidationError):
            rechazar_pago_breb(pago_breb=reporte, usuario=self.revisor, motivo='')

        rechazado = rechazar_pago_breb(
            pago_breb=reporte,
            usuario=self.revisor,
            motivo='El valor no coincide con el movimiento recibido.',
        )
        self.credito.refresh_from_db()
        self.assertEqual(rechazado.estado, PagoBREB.Estado.RECHAZADO)
        self.assertTrue(rechazado.comprobante.name)
        self.assertEqual(self.credito.saldo_pendiente, Decimal('220000.00'))
        self.assertFalse(HistorialPago.objects.exists())

    def test_descarga_comprobante_respeta_ownership_y_permiso(self):
        reporte = self._reportar()
        url_usuario = reverse('libranza:pago_breb_comprobante', args=[reporte.id])

        self.client.force_login(self.otro_usuario)
        self.assertEqual(self.client.get(url_usuario).status_code, 403)

        self.client.force_login(self.usuario)
        response = self.client.get(url_usuario)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertNotIn('comprobante.pdf', response.headers['Content-Disposition'])

        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pago_breb_comprobante', args=[reporte.id]))
        self.assertEqual(response.status_code, 200)

    def test_bandeja_requiere_permiso_y_muestra_pago(self):
        reporte = self._reportar()
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse('gestion:pagos_breb')).status_code, 403)

        self.client.force_login(self.revisor)
        response = self.client.get(reverse('gestion:pagos_breb'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reporte.credito.numero_credito)
        self.assertContains(response, 'Ver comprobante')

    def test_reporte_por_vista_muestra_pendiente_y_no_aplica_pago(self):
        self.client.force_login(self.usuario)
        response = self.client.post(
            reverse('libranza:pago_breb', args=[self.credito.id]),
            {
                'valor_reportado': '110000.00',
                'fecha_pago_reportada': '2026-08-29',
                'referencia_reportada': 'WEB-001',
                'comprobante': self._pdf('soporte-web.pdf'),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'pendiente de verificación')
        self.assertEqual(PagoBREB.objects.count(), 1)
        self.assertFalse(HistorialPago.objects.exists())


@skipIf(connection.vendor != 'postgresql', 'Requiere PostgreSQL real.')
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PagoBREBConcurrenciaPostgresTest(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.mkdtemp(prefix='aprobado-breb-concurrente-')
        cls.private_override = override_settings(PRIVATE_DOCUMENTS_ROOT=cls.private_root)
        cls.private_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.private_override.disable()
        shutil.rmtree(cls.private_root, ignore_errors=True)
        super().tearDownClass()

    @staticmethod
    def _imagen(nombre='qr-concurrente.png'):
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'white').save(buffer, format='PNG')
        return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')

    @staticmethod
    def _pdf(nombre='concurrente.pdf'):
        return SimpleUploadedFile(nombre, b'%PDF-1.4\ncomprobante', content_type='application/pdf')

    def setUp(self):
        self.usuario = User.objects.create_user('cliente-breb-concurrente', password='test1234')
        self.revisor = User.objects.create_user(
            'revisor-breb-concurrente', password='test1234', is_staff=True,
        )
        self.revisor.user_permissions.add(Permission.objects.get(codename='review_pagobreb'))
        empresa = Empresa.objects.create(nombre='EMPRESA BREB CONCURRENTE', convenio_activo=True)
        configuracion = ConfiguracionPagoBREB.objects.create(
            activo=True,
            nombre_receptor='APROBADO SAS',
            entidad_financiera='BANCO PRUEBA',
            tipo_llave=ConfiguracionPagoBREB.TipoLlave.ALFANUMERICA,
            llave_mostrable='APROBADO-CONCURRENTE',
            qr=self._imagen(),
        )
        credito = Credito.objects.create(
            usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-BREB-CONC',
            monto_solicitado=Decimal('220000.00'),
            monto_aprobado=Decimal('200000.00'),
            plazo_solicitado=2,
            plazo=2,
            tasa_interes=Decimal('2.00'),
            total_a_pagar=Decimal('220000.00'),
            saldo_pendiente=Decimal('220000.00'),
            capital_pendiente=Decimal('200000.00'),
            valor_cuota=Decimal('110000.00'),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa,
            nombres='CLIENTE',
            apellidos='CONCURRENTE',
            cedula='1000000010',
            direccion='Direccion de prueba',
            telefono='3000000010',
            correo_electronico='cliente-concurrente@aprobado.test',
            cedula_frontal='credito_libranza/cedulas/frontal.jpg',
            cedula_trasera='credito_libranza/cedulas/trasera.jpg',
            certificado_bancario='credito_libranza/certificados/certificado.pdf',
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 9, 1),
            capital_a_pagar=Decimal('100000.00'),
            interes_a_pagar=Decimal('10000.00'),
            valor_cuota=Decimal('110000.00'),
            saldo_capital_pendiente=Decimal('100000.00'),
        )
        self.pago = PagoBREB.objects.create(
            credito=credito,
            usuario=self.usuario,
            empresa=empresa,
            configuracion=configuracion,
            valor_reportado=Decimal('110000.00'),
            fecha_pago_reportada=date(2026, 8, 29),
            comprobante=self._pdf(),
            hash_comprobante='a' * 64,
        )

    def test_doble_aprobacion_concurrente_crea_un_pago_financiero(self):
        barrera = Barrier(2)
        resultados = Queue()
        errores = Queue()

        def aprobar():
            close_old_connections()
            try:
                pago = PagoBREB.objects.get(pk=self.pago.pk)
                revisor = User.objects.get(pk=self.revisor.pk)
                barrera.wait(timeout=10)
                aprobado, creado = aprobar_pago_breb(
                    pago_breb=pago,
                    usuario=revisor,
                    valor_aprobado=Decimal('110000.00'),
                )
                resultados.put((aprobado.historial_pago_id, creado))
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
        valores = list(resultados.queue)
        self.assertEqual(len(valores), 2)
        self.assertEqual(valores[0][0], valores[1][0])
        self.assertCountEqual([valor[1] for valor in valores], [True, False])
        self.assertEqual(HistorialPago.objects.filter(credito=self.pago.credito).count(), 1)
        self.assertEqual(DetalleContablePago.objects.filter(credito=self.pago.credito).count(), 1)

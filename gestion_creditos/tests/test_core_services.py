# -*- coding: utf-8 -*-
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.utils import timezone
from decimal import Decimal
import json
from gestion_creditos.models import (
    Credito,
    CreditoEmprendimiento,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    Pagare,
    ZapSignWebhookLog,
)
from gestion_creditos.services import filtrar_creditos, get_billetera_context, procesar_pagos_masivos_csv
from gestion_creditos.services.pagare_service import (
    _get_pagare_template_name,
    _preparar_contexto_pagare,
)
import io
from datetime import date


def pdf_upload(name):
    return SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')


class FiltrarCreditosServiceTest(TestCase):
    """Pruebas para la función de servicio `filtrar_creditos`."""

    @classmethod
    def setUpTestData(cls):
        """Crea los datos iniciales para todas las pruebas de esta clase."""
        cls.user = User.objects.create_user(username='testuser', password='123')
        cls.empresa = Empresa.objects.create(nombre='Empresa Test')

        # Crédito de Libranza para filtrar
        credito_libranza_1 = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('1000.00'),
            plazo_solicitado=12,
        )
        CreditoLibranza.objects.create(
            credito=credito_libranza_1,
            nombres='Juan',
            apellidos='Perez',
            cedula='12345',
            direccion='Calle 1',
            telefono='3000000000',
            correo_electronico='juan@example.com',
            empresa=cls.empresa,
            ingresos_mensuales=Decimal('2500000.00'),
            cedula_frontal=pdf_upload('cedula_frontal.pdf'),
            cedula_trasera=pdf_upload('cedula_trasera.pdf'),
            certificado_bancario=pdf_upload('certificado_bancario.pdf'),
        )

        # Crédito de Emprendimiento para filtrar
        credito_emprendimiento_1 = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            estado=Credito.EstadoCredito.EN_REVISION,
            monto_solicitado=Decimal('2000.00'),
            plazo_solicitado=24,
        )
        CreditoEmprendimiento.objects.create(
            credito=credito_emprendimiento_1,
            nombre='Negocio de Ana',
            numero_cedula='67890',
            fecha_nac=date(1990, 1, 1),
            celular_wh='3001234567',
            direccion='Calle Falsa 123',
            estado_civil='Soltero/a',
            numero_personas_cargo=0,
            nombre_negocio='Mi Negocio',
            ubicacion_negocio='Centro',
            tiempo_operando='1 año',
            dias_trabajados_sem=5,
            prod_serv_ofrec='Venta de productos',
            ingresos_prom_mes='1000000',
            cli_aten_day=10,
            inventario='si',
            nomb_ref_per1='Ref1',
            cel_ref_per1='3001234568',
            rel_ref_per1='Amigo',
            nomb_ref_cl1='RefC1',
            cel_ref_cl1='3001234569',
            rel_ref_cl1='Cliente',
            ref_conoc_lid_com='no',
            foto_negocio='fotos_negocios/test.pdf',
            desc_fotos_neg='...',
            tipo_cta_mno='Nequi',
            ahorro_tand_alc='si',
            depend_h='no',
            desc_cred_nec='Para capital de trabajo',
            redes_soc='si',
            fotos_prod='si'
        )
        
        cls.factory = RequestFactory()

    def test_filtrar_por_linea_libranza(self):
        """Verifica que el filtro por línea 'LIBRANZA' funcione correctamente."""
        request = self.factory.get('/', {'linea': 'LIBRANZA'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().linea, Credito.LineaCredito.LIBRANZA)

    def test_filtrar_por_estado_activo(self):
        """Verifica que el filtro por estado 'ACTIVO' funcione correctamente."""
        request = self.factory.get('/', {'estado': 'ACTIVO'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().estado, Credito.EstadoCredito.ACTIVO)

    def test_filtrar_por_busqueda_nombre(self):
        """Verifica que la búsqueda por nombre de solicitante funcione."""
        request = self.factory.get('/', {'search': 'Juan'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().detalle.nombres, 'Juan')

    def test_filtrar_por_busqueda_cedula(self):
        """Verifica que la búsqueda por cédula funcione."""
        request = self.factory.get('/', {'search': '12345'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().detalle.cedula, '12345')

    def test_sin_filtros(self):
        """Verifica que si no se aplican filtros, se devuelvan todos los créditos."""
        request = self.factory.get('/')
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 2)

class BilleteraContextServiceTest(TestCase):
    """Pruebas para la función de servicio `get_billetera_context`."""

    def setUp(self):
        """Configura un usuario para las pruebas de la billetera."""
        self.user = User.objects.create_user(username='billetera_user', password='123')

    def test_contexto_billetera_creacion_cuenta(self):
        """Verifica que se cree una cuenta si el usuario no tiene una y el contexto sea correcto."""
        context = get_billetera_context(self.user)
        self.assertIsNotNone(context.get('cuenta'))
        self.assertEqual(context.get('saldo_disponible'), Decimal('0.00'))
        self.assertEqual(context.get('total_depositado'), Decimal('0.00'))
        self.assertEqual(context.get('progreso_porcentaje'), 0)

class PagosMasivosCSVServiceTest(TestCase):
    """Pruebas para la función de servicio `procesar_pagos_masivos_csv`."""

    @classmethod
    def setUpTestData(cls):
        """Crea datos para las pruebas de procesamiento de CSV."""
        cls.user = User.objects.create_user(username='pagador_user', password='123')
        cls.empresa = Empresa.objects.create(nombre='Empresa Pagadora')

        # Crédito activo para procesar pago
        credito_activo = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('5000.00'),
            plazo_solicitado=12,
            monto_aprobado=Decimal('5000.00'),
            plazo=12,
            tasa_interes=Decimal('2.50'),
            comision=Decimal('0.00'),
            iva_comision=Decimal('0.00'),
            total_a_pagar=Decimal('5000.00'),
            saldo_pendiente=Decimal('5000.00'),
            capital_pendiente=Decimal('5000.00'),
            valor_cuota=Decimal('500.00'),
            fecha_proximo_pago=timezone.now().date(),
        )
        CreditoLibranza.objects.create(
            credito=credito_activo,
            nombres='Maria',
            apellidos='Lopez',
            cedula='112233',
            direccion='Calle 2',
            telefono='3001112233',
            correo_electronico='maria@example.com',
            empresa=cls.empresa,
            ingresos_mensuales=Decimal('3200000.00'),
            cedula_frontal=pdf_upload('cedula_frontal_maria.pdf'),
            cedula_trasera=pdf_upload('cedula_trasera_maria.pdf'),
            certificado_bancario=pdf_upload('certificado_bancario_maria.pdf'),
        )
        CuotaAmortizacion.objects.create(
            credito=credito_activo,
            numero_cuota=1,
            fecha_vencimiento=timezone.now().date(),
            capital_a_pagar=Decimal('500.00'),
            interes_a_pagar=Decimal('0.00'),
            valor_cuota=Decimal('500.00'),
            saldo_capital_pendiente=Decimal('4500.00'),
        )

    def test_procesar_csv_exitoso(self):
        """Verifica el procesamiento exitoso de un CSV de pagos masivos."""
        csv_content = 'cedula,monto_a_pagar\n112233,500\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        
        pagos_exitosos, errores = procesar_pagos_masivos_csv(csv_file, self.empresa)
        
        self.assertEqual(pagos_exitosos, 1)
        self.assertEqual(len(errores), 0)
        
        credito_actualizado = Credito.objects.get(detalle_libranza__cedula='112233')
        self.assertTrue(credito_actualizado.saldo_pendiente < Decimal('5000.00'))

    def test_procesar_csv_con_errores(self):
        """Verifica que se manejen correctamente las filas con errores en el CSV."""
        # Cédula no existente, monto inválido
        csv_content = 'cedula,monto_a_pagar\n999999,100\n112233,monto_invalido\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))

        pagos_exitosos, errores = procesar_pagos_masivos_csv(csv_file, self.empresa)

        self.assertEqual(pagos_exitosos, 0)
        self.assertEqual(len(errores), 2)
        self.assertIn("No se encontro un credito activo para la cedula 999999", errores[0])
        self.assertIn("monto_invalido", errores[1])


@override_settings(ZAPSIGN_WEBHOOK_SECRET='test-secret', ZAPSIGN_WEBHOOK_HEADER='X-ZapSign-Secret')
class ZapSignWebhookViewTest(TestCase):
    """Pruebas para el webhook robusto de ZapSign."""

    def setUp(self):
        self.user = User.objects.create_user(username='zapsign_user', password='123')
        self.credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            estado=Credito.EstadoCredito.PENDIENTE_FIRMA,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=3
        )
        self.pdf_file = SimpleUploadedFile(
            'pagare.pdf',
            b'%PDF-1.4 test',
            content_type='application/pdf'
        )
        self.pagare = Pagare.objects.create(
            credito=self.credito,
            archivo_pdf=self.pdf_file,
            zapsign_doc_token='token-123',
            estado=Pagare.EstadoPagare.SENT
        )
        self.url = reverse('zapsign_webhook')
        self.secret = settings.ZAPSIGN_WEBHOOK_SECRET

    def _post(self, payload, secret=None):
        headers = {}
        if secret is not None:
            headers['HTTP_X_ZAPSIGN_SECRET'] = secret
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
            **headers
        )

    def test_webhook_rechaza_secret_invalido(self):
        payload = {
            'event': 'doc_signed',
            'token': 'token-123',
            'status': 'signed',
            'signers': [{'ip': '1.2.3.4'}]
        }
        response = self._post(payload, secret='bad-secret')
        self.assertEqual(response.status_code, 403)

        log = ZapSignWebhookLog.objects.latest('received_at')
        self.assertFalse(log.signature_valid)
        self.assertFalse(log.processed)
        self.assertIn('Secret', log.error_message)

    def test_webhook_doc_signed_actualiza_estado(self):
        payload = {
            'event': 'doc_signed',
            'token': 'token-123',
            'status': 'signed',
            'signers': [{'ip': '1.2.3.4'}]
        }
        response = self._post(payload, secret=self.secret)
        self.assertEqual(response.status_code, 200)

        self.pagare.refresh_from_db()
        self.credito.refresh_from_db()

        self.assertEqual(self.pagare.estado, Pagare.EstadoPagare.SIGNED)
        self.assertIsNotNone(self.pagare.fecha_firma)
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA)

        log = ZapSignWebhookLog.objects.latest('received_at')
        self.assertTrue(log.signature_valid)
        self.assertTrue(log.processed)

    def test_webhook_doc_signed_idempotente(self):
        payload = {
            'event': 'doc_signed',
            'token': 'token-123',
            'status': 'signed',
            'signers': [{'ip': '1.2.3.4'}]
        }
        self._post(payload, secret=self.secret)
        response = self._post(payload, secret=self.secret)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('status'), 'already_processed')
        self.assertEqual(ZapSignWebhookLog.objects.count(), 2)

    def test_webhook_doc_refused(self):
        credito_refused = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            estado=Credito.EstadoCredito.PENDIENTE_FIRMA,
            monto_solicitado=Decimal('500000.00'),
            plazo_solicitado=2
        )
        pagare_refused = Pagare.objects.create(
            credito=credito_refused,
            archivo_pdf=self.pdf_file,
            zapsign_doc_token='token-refused',
            estado=Pagare.EstadoPagare.SENT
        )
        payload = {
            'event': 'doc_refused',
            'token': 'token-refused',
            'status': 'refused'
        }
        response = self._post(payload, secret=self.secret)
        self.assertEqual(response.status_code, 200)

        pagare_refused.refresh_from_db()
        credito_refused.refresh_from_db()

        self.assertEqual(pagare_refused.estado, Pagare.EstadoPagare.REFUSED)
        self.assertIsNotNone(pagare_refused.fecha_rechazo)
        self.assertEqual(credito_refused.estado, Credito.EstadoCredito.PENDIENTE_FIRMA)

    def test_webhook_doc_signed_con_status_recusado_se_trata_como_rechazo(self):
        payload = {
            'event': 'doc_signed',
            'token': 'token-123',
            'status': 'recusado',
            'rejected_reason': 'Prueba de rechazo',
            'signers': [{'status': 'link-opened'}]
        }
        response = self._post(payload, secret=self.secret)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('status'), 'refused_recorded')

        self.pagare.refresh_from_db()
        self.credito.refresh_from_db()

        self.assertEqual(self.pagare.estado, Pagare.EstadoPagare.REFUSED)
        self.assertIsNotNone(self.pagare.fecha_rechazo)
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.PENDIENTE_FIRMA)

    def test_webhook_documento_no_encontrado_no_falla(self):
        payload = {
            'event': 'doc_signed',
            'token': 'token-inexistente',
            'status': 'signed'
        }
        response = self._post(payload, secret=self.secret)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('status'), 'document_not_found_ignored')


class PagareTemplateVersionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='deudor_pagare',
            password='123',
            first_name='Ana',
            last_name='Torres',
            email='ana@example.com',
        )
        self.empresa = Empresa.objects.create(nombre='Empresa Sin Ubicacion')
        self.credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.APROBADO,
            monto_solicitado=Decimal('1200000.00'),
            monto_aprobado=Decimal('1200000.00'),
            plazo_solicitado=6,
            plazo=6,
            tasa_interes=Decimal('2.10'),
            comision=Decimal('0.00'),
            iva_comision=Decimal('0.00'),
            valor_cuota=Decimal('220000.00'),
            fecha_proximo_pago=timezone.localdate(),
        )
        self.detalle = CreditoLibranza.objects.create(
            credito=self.credito,
            nombres='Ana',
            apellidos='Torres',
            cedula='123456789',
            direccion='Calle 10 # 20-30',
            telefono='3001234567',
            correo_electronico='ana@example.com',
            empresa=self.empresa,
            ingresos_mensuales=Decimal('2500000.00'),
            cedula_frontal=pdf_upload('cedula_frontal_pagare.pdf'),
            cedula_trasera=pdf_upload('cedula_trasera_pagare.pdf'),
            certificado_bancario=pdf_upload('certificado_bancario_pagare.pdf'),
        )

    def _contexto(self, version='1.0'):
        return _preparar_contexto_pagare(
            self.credito,
            self.detalle,
            'PAG-TEST-001',
            version_plantilla=version,
        )

    def test_pagare_actual_no_contiene_pluralizaciones_ni_fecha_hardcodeada(self):
        html = render_to_string('pagares/pagare_v1.0.html', self._contexto('1.0'))

        self.assertNotIn('he(mos)', html)
        self.assertNotIn('obligo(amos)', html)
        self.assertNotIn('Yo (Nosotros)', html)
        self.assertNotIn('1 de junio de 2026', html)

    def test_ciudad_firma_no_usa_villavicencio_por_defecto(self):
        contexto = self._contexto('1.0')
        html = render_to_string('pagares/pagare_v1.0.html', contexto)

        self.assertEqual(contexto['ciudad_firma'], '')
        self.assertIn('________________', html)
        self.assertNotIn('Villavicencio', html)

    def test_fecha_firma_sale_del_backend(self):
        contexto = self._contexto('1.0')
        fecha_actual = timezone.localdate()

        self.assertEqual(contexto['dia_firma'], str(fecha_actual.day))
        self.assertEqual(contexto['anio_firma'], str(fecha_actual.year))
        self.assertIn(str(fecha_actual.year), contexto['fecha_firma_texto'])

    def test_selector_de_template_permite_version_dos_sin_romper_default(self):
        self.assertEqual(_get_pagare_template_name('2.0'), 'pagares/pagare_v2.0.html')
        self.assertEqual(_get_pagare_template_name('version-invalida'), 'pagares/pagare_v1.0.html')

    def test_pagare_version_dos_renderiza_con_datos_minimos(self):
        html = render_to_string('pagares/pagare_v2.0.html', self._contexto('2.0'))

        self.assertIn('PAGARÉ EN BLANCO', html)
        self.assertIn('CARTA DE INSTRUCCIONES', html)
        self.assertIn('ANA TORRES', html)
        self.assertIn('123456789', html)
        self.assertIn('APROBADO SOLUCIONES DIGITALES SAS', html)
        self.assertIn('Descuento por libranza', html)


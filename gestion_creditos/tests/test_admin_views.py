from datetime import timedelta
from decimal import Decimal
import io

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from gestion_creditos.models import (
    Credito,
    CreditoAdelantoNomina,
    CuotaAmortizacion,
    Empresa,
    HistorialPago,
    VinculoLaboralEmpresa,
)


User = get_user_model()


class AdminViewsSmokeTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff-admin',
            email='staff-admin@aprobado.test',
            password='123456',
            is_staff=True,
        )
        self.client.login(username='staff-admin', password='123456')

    def _crear_creditos_activos_admin(self, cantidad, prefijo='CR-ADMIN-PAG'):
        usuario = User.objects.create_user(
            username=f'{prefijo.lower()}-user',
            email=f'{prefijo.lower()}@aprobado.test',
            password='123456',
        )
        for index in range(cantidad):
            Credito.objects.create(
                usuario=usuario,
                numero_credito=f'{prefijo}-{index:03d}',
                linea=Credito.LineaCredito.LIBRANZA,
                estado=Credito.EstadoCredito.ACTIVO,
                monto_solicitado=Decimal('1000000.00'),
                monto_aprobado=Decimal('1000000.00'),
                plazo_solicitado=12,
                plazo=12,
                saldo_pendiente=Decimal('500000.00'),
                capital_pendiente=Decimal('500000.00'),
                total_a_pagar=Decimal('500000.00'),
                valor_cuota=Decimal('100000.00'),
                fecha_solicitud=timezone.now() - timedelta(minutes=index),
            )

    def test_paginas_admin_principales_responden(self):
        for url_name in [
            'gestion:dashboard',
            'gestion:solicitudes',
            'gestion:adelantos_nomina',
            'gestion:creditos_activos',
            'gestion:cartera_mora',
        ]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)

    def test_detalle_credito_adelanto_renderiza_capacidad_descuento(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Admin Smoke',
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
        )
        usuario = User.objects.create_user(
            username='empleado-smoke',
            email='empleado-smoke@aprobado.test',
            password='123456',
            first_name='Empleado',
            last_name='Smoke',
        )
        vinculo = VinculoLaboralEmpresa.objects.create(
            usuario=usuario,
            empresa=empresa,
            documento_empleado='123456789',
            nombre_empleado='EMPLEADO SMOKE',
            estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            fecha_alta_aprobado=timezone.localdate() - timedelta(days=90),
            salario_base_mensual=Decimal('2400000.00'),
            auxilio_transporte_mensual=Decimal('162000.00'),
            descuentos_fijos_mensuales=Decimal('350000.00'),
        )
        credito = Credito.objects.create(
            usuario=usuario,
            numero_credito='CR-ADMIN-SMOKE-0001',
            linea=Credito.LineaCredito.ADELANTO_NOMINA,
            estado=Credito.EstadoCredito.SOLICITUD,
            monto_solicitado=Decimal('500000.00'),
            plazo_solicitado=1,
        )
        CreditoAdelantoNomina.objects.create(
            credito=credito,
            vinculo_laboral=vinculo,
            monto_solicitado=Decimal('500000.00'),
            monto_maximo_calculado=Decimal('600000.00'),
            dias_adelanto=5,
            salario_base_usado=Decimal('2400000.00'),
        )

        response = self.client.get(reverse('gestion:credito_detalle', args=[credito.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Capacidad de descuento')

    def test_dashboard_export_descarga_excel_funcional(self):
        response = self.client.get(reverse('gestion:dashboard_export'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        workbook = load_workbook(io.BytesIO(response.content))
        self.assertIn('Resumen ejecutivo', workbook.sheetnames)
        self.assertIn('Recaudo contable', workbook.sheetnames)
        self.assertIn('Detalle contable', workbook.sheetnames)
        self.assertIn('Detalle operativo', workbook.sheetnames)

    def test_dashboard_renderiza_indicadores_contables(self):
        response = self.client.get(reverse('gestion:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Total recaudado')
        self.assertContains(response, 'Capital recuperado')

    def test_admin_creditos_activos_responde_pagina_tres(self):
        self._crear_creditos_activos_admin(45)

        response = self.client.get(reverse('gestion:creditos_activos'), {'page': 3})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['creditos'].number, 3)
        self.assertEqual(response.context['creditos'].paginator.num_pages, 3)

    def test_admin_creditos_activos_no_duplica_page_y_preserva_filtros(self):
        self._crear_creditos_activos_admin(45, prefijo='CR-ADMIN-FILTRO')

        response = self.client.get(
            reverse('gestion:creditos_activos'),
            {'page': 2, 'search': 'CR-ADMIN-FILTRO', 'linea': Credito.LineaCredito.LIBRANZA},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'page=3&search=CR-ADMIN-FILTRO&amp;linea=LIBRANZA', html=False)
        self.assertNotContains(response, 'page=3&amp;page=2', html=False)

    def test_admin_creditos_activos_url_malformada_usa_primer_page(self):
        self._crear_creditos_activos_admin(45, prefijo='CR-ADMIN-DUP')

        response = self.client.get(
            f"{reverse('gestion:creditos_activos')}?page=3&page=2&search=CR-ADMIN-DUP"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['creditos'].number, 3)
        self.assertNotContains(response, 'page=2&amp;page=3', html=False)

    @override_settings(MANUAL_PAYMENT_AUTH_KEY='clave-prueba-admin')
    def test_pago_manual_admin_total_cierra_credito_activo(self):
        credito = Credito.objects.create(
            usuario=self.staff,
            numero_credito='CR-ADMIN-PAGO-TOTAL',
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('3000000.00'),
            monto_aprobado=Decimal('3000000.00'),
            plazo_solicitado=1,
            plazo=1,
            saldo_pendiente=Decimal('3357000.00'),
            capital_pendiente=Decimal('3000000.00'),
            comision=Decimal('300000.00'),
            iva_comision=Decimal('57000.00'),
            tasa_interes=Decimal('1.90'),
            valor_cuota=Decimal('3420783.00'),
            total_a_pagar=Decimal('3420783.00'),
            fecha_proximo_pago=timezone.localdate(),
        )
        cuota = CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('3357000.00'),
            interes_a_pagar=Decimal('63783.00'),
            valor_cuota=Decimal('3420783.00'),
            saldo_capital_pendiente=Decimal('0.00'),
        )

        response = self.client.post(
            reverse('gestion:credito_agregar_pago', args=[credito.id]),
            {
                'monto': '3420783.00',
                'referencia_pago': 'ADMIN-PAGO-TOTAL-001',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'auth_key': 'clave-prueba-admin',
            },
        )

        self.assertEqual(response.status_code, 302)
        credito.refresh_from_db()
        cuota.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PAGADO)
        self.assertTrue(cuota.pagada)
        self.assertEqual(HistorialPago.objects.filter(credito=credito).count(), 1)
        mensajes = [str(mensaje) for mensaje in get_messages(response.wsgi_request)]
        self.assertFalse(any('error inesperado' in mensaje.lower() for mensaje in mensajes))

    @override_settings(MANUAL_PAYMENT_AUTH_KEY='clave-prueba-admin')
    def test_pago_manual_admin_bloquea_credito_ya_pagado_con_mensaje_controlado(self):
        credito = Credito.objects.create(
            usuario=self.staff,
            numero_credito='CR-ADMIN-YA-PAGADO',
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.PAGADO,
            monto_solicitado=Decimal('100000.00'),
            monto_aprobado=Decimal('100000.00'),
            plazo_solicitado=1,
            plazo=1,
            saldo_pendiente=Decimal('0.00'),
            capital_pendiente=Decimal('0.00'),
        )

        response = self.client.post(
            reverse('gestion:credito_agregar_pago', args=[credito.id]),
            {
                'monto': '100000.00',
                'auth_key': 'clave-prueba-admin',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(HistorialPago.objects.filter(credito=credito).exists())
        mensajes = [str(mensaje) for mensaje in get_messages(response.wsgi_request)]
        self.assertIn(
            'El crédito ya se encuentra pagado y no permite registrar pagos manuales adicionales.',
            mensajes,
        )

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, Empresa, HistorialPago
from usuarios.models import PerfilPagador


User = get_user_model()


class PagadorDashboardTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagador UX',
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
            convenio_activo=True,
        )
        self.user = User.objects.create_user(
            username='pagador-ux',
            email='pagador-ux@aprobado.test',
            password='123456',
            first_name='Pagador',
            last_name='UX',
        )
        PerfilPagador.objects.create(usuario=self.user, empresa=self.empresa, es_pagador=True)
        self.client.login(username='pagador-ux', password='123456')

    def _crear_credito_libranza(self, numero, estado=Credito.EstadoCredito.ACTIVO, cuota='100.00'):
        empleado = User.objects.create_user(
            username=f'user-{numero.lower()}',
            email=f'{numero.lower()}@aprobado.test',
            password='123456',
        )
        credito = Credito.objects.create(
            usuario=empleado,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            numero_credito=numero,
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal(cuota),
            saldo_pendiente=Decimal('200.00') if estado != Credito.EstadoCredito.PAGADO else Decimal('0.00'),
            capital_pendiente=Decimal('200.00') if estado != Credito.EstadoCredito.PAGADO else Decimal('0.00'),
            total_a_pagar=Decimal('200.00'),
            comision=Decimal('0.00'),
            iva_comision=Decimal('0.00'),
            fecha_proximo_pago=date(2026, 4, 30),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=self.empresa,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico=f'{numero.lower()}@empresa.test',
            cedula=f'{numero[-3:]}123',
            nombres='Empleado',
            apellidos=numero[-2:],
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 4, 30),
            capital_a_pagar=Decimal('80.00'),
            interes_a_pagar=Decimal('20.00'),
            valor_cuota=Decimal(cuota),
            saldo_capital_pendiente=Decimal('100.00'),
            pagada=estado == Credito.EstadoCredito.PAGADO,
            monto_pagado=Decimal(cuota) if estado == Credito.EstadoCredito.PAGADO else Decimal('0.00'),
        )
        return credito

    def test_dashboard_principal_muestra_pago_directo_en_la_tabla(self):
        self._crear_credito_libranza('CR-PAG-001', estado=Credito.EstadoCredito.ACTIVO)
        self._crear_credito_libranza('CR-PAG-002', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="per_page"', html=False)
        self.assertContains(response, 'Aplicar pagos seleccionados')
        self.assertContains(response, 'Respaldo operativo por Excel')
        self.assertNotContains(response, 'Completar datos de usuarios existentes')

    def test_dashboard_preserva_paginacion_y_filtros(self):
        for index in range(12):
            self._crear_credito_libranza(f'CR-PAG-{index:03d}', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.get(reverse('pagador:dashboard'), {'per_page': 10, 'page': 2, 'search': 'Empleado'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Página 2 de 2')
        self.assertContains(response, 'per_page=10')
        self.assertContains(response, 'search=Empleado')

    def test_pago_directo_redirige_al_siguiente_contexto(self):
        credito = self._crear_credito_libranza('CR-PAG-900', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
                'nota': 'Pago directo desde tabla',
                'next': '/pagador/adelantos/?page=2&per_page=10',
                'origen': 'adelantos',
            },
        )

        self.assertRedirects(response, '/pagador/adelantos/?page=2&per_page=10', fetch_redirect_response=False)

    def test_detalle_credito_carga_sin_nameerror(self):
        credito = self._crear_credito_libranza('CR-PAG-DET', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.get(reverse('pagador:credito_detalle', args=[credito.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Detalle del Crédito')
        self.assertContains(response, 'Registrar pago offline')

    def test_dashboard_ignora_residuales_minimos_y_muestra_siguiente_cuota_real(self):
        credito = self._crear_credito_libranza('CR-PAG-ROUND', estado=Credito.EstadoCredito.ACTIVO, cuota='100.00')
        cuota_1 = credito.tabla_amortizacion.get(numero_cuota=1)
        cuota_1.monto_pagado = Decimal('99.25')
        cuota_1.save(update_fields=['monto_pagado'])
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 5, 30),
            capital_a_pagar=Decimal('80.00'),
            interes_a_pagar=Decimal('20.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
        )
        credito.saldo_pendiente = Decimal('100.75')
        credito.save(update_fields=['saldo_pendiente'])

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cuota 2')
        self.assertContains(response, 'value="100.00"', html=False)

    @patch('gestion_creditos.views.pagador.credit_services.preparar_documento_para_firma')
    @patch('gestion_creditos.views.pagador.credit_services.gestionar_cambio_estado_credito')
    def test_decision_pagador_aprueba_solicitud_sin_error_de_bloqueo(self, cambio_estado_mock, preparar_mock):
        credito = self._crear_credito_libranza('CR-PAG-DEC', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.post(
            reverse('pagador:decidir_solicitud', args=[credito.id]),
            {'action': 'approve', 'motivo': 'Aprobado en prueba'},
        )

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        cambio_estado_mock.assert_called_once()
        preparar_mock.assert_called_once()

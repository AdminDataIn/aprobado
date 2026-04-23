from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    DetalleContablePago,
    Empresa,
    HistorialEstado,
    HistorialPago,
)
from gestion_creditos.services.dashboard_metrics import get_admin_dashboard_context


User = get_user_model()


class DashboardMetricsTests(TestCase):
    def test_dashboard_usa_detalle_contable_para_desglose_real(self):
        staff = User.objects.create_user(
            username='metric-staff',
            email='metric-staff@aprobado.test',
            password='123456',
            is_staff=True,
        )
        empresa = Empresa.objects.create(
            nombre='Empresa Contable',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        usuario = User.objects.create_user(
            username='cliente-contable',
            email='cliente-contable@aprobado.test',
            password='123456',
        )
        credito = Credito.objects.create(
            usuario=usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-MET-0001',
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=4,
            plazo=4,
            valor_cuota=Decimal('299.75'),
            saldo_pendiente=Decimal('559.50'),
            capital_pendiente=Decimal('500.00'),
            total_a_pagar=Decimal('1199.00'),
            comision=Decimal('100.00'),
            iva_comision=Decimal('19.00'),
            fecha_desembolso=timezone.now(),
            fecha_proximo_pago=timezone.localdate(),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa,
            direccion='Calle 10',
            telefono='3001234567',
            correo_electronico='cliente-contable@empresa.test',
            cedula='123456789',
            nombres='Cliente',
            apellidos='Contable',
        )
        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=Credito.EstadoCredito.FIRMADO,
            estado_nuevo=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            motivo='Paso a transferencia',
        )
        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            estado_nuevo=Credito.EstadoCredito.ACTIVO,
            motivo='Desembolso confirmado',
            comprobante_pago='comprobantes/demo.pdf',
        )
        cuota = CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 4, 1),
            capital_a_pagar=Decimal('280.00'),
            interes_a_pagar=Decimal('20.00'),
            valor_cuota=Decimal('300.00'),
            saldo_capital_pendiente=Decimal('839.00'),
            pagada=True,
            monto_pagado=Decimal('300.00'),
            fecha_pago=timezone.now(),
        )
        pago = HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('300.00'),
            referencia_pago='REF-MET-001',
            estado=HistorialPago.EstadoPago.EXITOSO,
            metodo_pago=HistorialPago.MetodoPago.WOMPI,
            origen_registro=HistorialPago.OrigenRegistro.PASARELA_WOMPI,
        )
        DetalleContablePago.objects.create(
            pago=pago,
            credito=credito,
            cuota=cuota,
            secuencia_aplicacion=1,
            fecha_aplicacion=pago.fecha_aplicacion,
            monto_total_aplicado=Decimal('300.00'),
            capital_aplicado=Decimal('280.00'),
            interes_aplicado=Decimal('20.00'),
            capital_principal_aplicado=Decimal('235.29'),
            comision_aplicada=Decimal('23.53'),
            iva_aplicado=Decimal('21.18'),
            metodologia_calculo=DetalleContablePago.MetodologiaCalculo.CUOTA_INTERES_PRIMERO,
        )

        context = get_admin_dashboard_context(staff)

        self.assertEqual(context['total_recaudado'], Decimal('300.00'))
        self.assertEqual(context['capital_recuperado'], Decimal('235.29'))
        self.assertEqual(context['interes_recuperado'], Decimal('20.00'))
        self.assertEqual(context['comision_recuperada'], Decimal('23.53'))
        self.assertEqual(context['iva_recuperado'], Decimal('21.18'))
        self.assertTrue(context['rentabilidad_breakdown_supported'])
        self.assertEqual(context['creditos_con_trazabilidad_contable'], 1)
        self.assertEqual(context['pagos_con_trazabilidad_contable'], 1)

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
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


User = get_user_model()


class AccountingBackfillCommandTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre='Empresa Backfill',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.usuario = User.objects.create_user(
            username='cliente-backfill',
            email='cliente-backfill@aprobado.test',
            password='123456',
        )

    def _crear_credito(self, numero_credito, elegible):
        credito = Credito.objects.create(
            usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito=numero_credito,
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal('600.00'),
            saldo_pendiente=Decimal('0.00'),
            capital_pendiente=Decimal('0.00'),
            total_a_pagar=Decimal('1200.00'),
            comision=Decimal('100.00'),
            iva_comision=Decimal('19.00'),
            fecha_desembolso=timezone.now(),
            fecha_proximo_pago=timezone.localdate(),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=self.empresa,
            direccion='Calle 10',
            telefono='3001234567',
            correo_electronico='cliente@empresa.test',
            cedula=f'{numero_credito[-3:]}12345',
            nombres='Cliente',
            apellidos='Backfill',
        )
        if elegible:
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

        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 5, 1),
            capital_a_pagar=Decimal('500.00'),
            interes_a_pagar=Decimal('100.00'),
            valor_cuota=Decimal('600.00'),
            saldo_capital_pendiente=Decimal('600.00'),
            pagada=True,
            monto_pagado=Decimal('600.00'),
            fecha_pago=timezone.now(),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 6, 1),
            capital_a_pagar=Decimal('600.00'),
            interes_a_pagar=Decimal('0.00'),
            valor_cuota=Decimal('600.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=True,
            monto_pagado=Decimal('600.00'),
            fecha_pago=timezone.now(),
        )
        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('600.00'),
            referencia_pago=f'REF-{numero_credito}-1',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )
        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('600.00'),
            referencia_pago=f'REF-{numero_credito}-2',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )
        return credito

    def test_backfill_solo_procesa_creditos_desembolsados_por_plataforma(self):
        elegible = self._crear_credito('CR-BF-0001', elegible=True)
        self._crear_credito('CR-BF-0002', elegible=False)

        stdout = StringIO()
        call_command('backfill_detalle_contable_pagos', stdout=stdout)

        self.assertEqual(DetalleContablePago.objects.filter(credito=elegible).count(), 2)
        self.assertEqual(DetalleContablePago.objects.exclude(credito=elegible).count(), 0)
        self.assertIn('Backfill contable completado', stdout.getvalue())

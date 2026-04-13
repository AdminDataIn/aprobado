from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos.models import Credito, CuotaAmortizacion, HistorialEstado
from gestion_creditos.services.credit_lifecycle import saldar_credito_formalmente


User = get_user_model()


class CreditLifecycleTest(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(
            username='admin-close',
            email='admin-close@aprobado.test',
            password='123456',
        )
        self.cliente = User.objects.create_user(
            username='cliente-close',
            email='cliente-close@aprobado.test',
            password='123456',
            first_name='CLIENTE',
        )

    def test_saldar_credito_formalmente_lo_deja_sin_residuos(self):
        credito = Credito.objects.create(
            usuario=self.cliente,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-CLOSE-0001',
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal('120.00'),
            total_a_pagar=Decimal('240.00'),
            saldo_pendiente=Decimal('120.00'),
            capital_pendiente=Decimal('90.00'),
            fecha_proximo_pago=date(2026, 5, 30),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 4, 30),
            capital_a_pagar=Decimal('60.00'),
            interes_a_pagar=Decimal('60.00'),
            valor_cuota=Decimal('120.00'),
            saldo_capital_pendiente=Decimal('60.00'),
            pagada=True,
            monto_pagado=Decimal('120.00'),
            fecha_pago=timezone.now(),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 5, 30),
            capital_a_pagar=Decimal('60.00'),
            interes_a_pagar=Decimal('60.00'),
            valor_cuota=Decimal('120.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
        )

        saldar_credito_formalmente(
            credito,
            actor=self.actor,
            motivo='CIERRE CONTROLADO QA',
            fecha_operacion=timezone.now(),
        )

        credito.refresh_from_db()
        cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
        self.assertEqual(credito.estado, Credito.EstadoCredito.PAGADO)
        self.assertEqual(credito.saldo_pendiente, Decimal('0.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('0.00'))
        self.assertIsNone(credito.fecha_proximo_pago)
        self.assertTrue(all(cuota.pagada for cuota in cuotas))
        self.assertEqual(cuotas[-1].monto_pagado, Decimal('120.00'))
        self.assertTrue(
            HistorialEstado.objects.filter(
                credito=credito,
                estado_nuevo=Credito.EstadoCredito.PAGADO,
                usuario_modificacion=self.actor,
            ).exists()
        )

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CuotaAmortizacion, HistorialPago


class ResumenPagosCreditoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='resumen-creditos',
            email='resumen@example.com',
            password='secret123',
        )

    def _crear_credito(self, **overrides):
        data = {
            'usuario': self.user,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'monto_solicitado': Decimal('1200.00'),
            'plazo_solicitado': 3,
            'monto_aprobado': Decimal('1200.00'),
            'plazo': 3,
            'valor_cuota': Decimal('100.00'),
            'saldo_pendiente': Decimal('100.00'),
            'fecha_proximo_pago': timezone.localdate(),
        }
        data.update(overrides)
        return Credito.objects.create(**data)

    def test_usa_tabla_amortizacion_como_fuente_de_verdad(self):
        credito = self._crear_credito()

        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('70.00'),
            interes_a_pagar=Decimal('30.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('200.00'),
            pagada=True,
            monto_pagado=Decimal('100.00'),
            fecha_pago=timezone.now(),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('70.00'),
            interes_a_pagar=Decimal('30.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('100.00'),
            pagada=True,
            monto_pagado=None,
            fecha_pago=timezone.now(),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=3,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('70.00'),
            interes_a_pagar=Decimal('30.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
        )

        resumen = credit_services.obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen['fuente'], 'tabla_amortizacion')
        self.assertEqual(resumen['cuotas_pagadas'], 2)
        self.assertEqual(resumen['cuotas_restantes'], 1)
        self.assertEqual(resumen['total_pagado'], Decimal('200.00'))
        self.assertEqual(resumen['saldo_pendiente'], Decimal('100.00'))
        self.assertEqual(resumen['capital_pendiente'], Decimal('70.00'))

    def test_hace_fallback_a_historial_pagos_sin_amortizacion(self):
        credito = self._crear_credito(plazo=2, saldo_pendiente=Decimal('150.00'))

        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('50.00'),
            referencia_pago='TEST-PAGO-1',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )

        resumen = credit_services.obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen['fuente'], 'historial_pagos')
        self.assertEqual(resumen['cuotas_pagadas'], 1)
        self.assertEqual(resumen['cuotas_restantes'], 1)
        self.assertEqual(resumen['total_pagado'], Decimal('50.00'))
        self.assertEqual(resumen['saldo_pendiente'], Decimal('150.00'))

    def test_recalcula_y_persiste_saldos_desde_amortizacion(self):
        credito = self._crear_credito(
            saldo_pendiente=Decimal('999.99'),
            capital_pendiente=Decimal('888.88'),
        )

        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('60.00'),
            interes_a_pagar=Decimal('40.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('120.00'),
            pagada=True,
            monto_pagado=Decimal('100.00'),
            fecha_pago=timezone.now(),
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('60.00'),
            interes_a_pagar=Decimal('40.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('60.00'),
            pagada=False,
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=3,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=Decimal('60.00'),
            interes_a_pagar=Decimal('40.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
        )

        resumen = credit_services.recalcular_credito_desde_tabla_amortizacion(credito, persist=True)
        credito.refresh_from_db()

        self.assertEqual(resumen['saldo_pendiente'], Decimal('200.00'))
        self.assertEqual(resumen['capital_pendiente'], Decimal('120.00'))
        self.assertEqual(credito.saldo_pendiente, Decimal('200.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('120.00'))
        self.assertEqual(credito.estado, Credito.EstadoCredito.ACTIVO)

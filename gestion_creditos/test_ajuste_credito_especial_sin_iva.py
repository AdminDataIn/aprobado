from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CuotaAmortizacion


class AjusteCreditoEspecialSinIvaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='duvercold-test',
            email='duvercold@example.com',
            password='secret123',
        )

        self.credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL,
            monto_solicitado=Decimal('2625000.00'),
            plazo_solicitado=8,
            monto_aprobado=Decimal('2625000.00'),
            plazo=8,
            plazo_forzado=8,
            tasa_interes=Decimal('2.00'),
            tasa_forzada=Decimal('2.00'),
            comision=Decimal('200000.00'),
            iva_comision=Decimal('38000.00'),
            valor_cuota=Decimal('578528.53'),
            total_a_pagar=Decimal('4628228.24'),
            saldo_pendiente=Decimal('4628228.24'),
            capital_pendiente=Decimal('2625000.00'),
            fecha_proximo_pago=date(2026, 3, 30),
            fecha_primera_cuota_forzada=date(2026, 3, 30),
            observacion_regla_especial='Credito especial prueba.',
        )

        for numero in range(1, 9):
            CuotaAmortizacion.objects.create(
                credito=self.credito,
                numero_cuota=numero,
                fecha_vencimiento=date(2026, 3, 30),
                capital_a_pagar=Decimal('0.00'),
                interes_a_pagar=Decimal('0.00'),
                valor_cuota=Decimal('578528.53'),
                saldo_capital_pendiente=Decimal('0.00'),
            )

    def test_preview_recalcula_solo_el_credito_especial(self):
        resultado = credit_services.recalcular_credito_especial_sin_iva_comision(
            self.credito,
            persist=False,
        )

        self.assertEqual(resultado['iva_actual'], Decimal('38000.00'))
        self.assertEqual(resultado['iva_nuevo'], Decimal('0.00'))
        self.assertEqual(resultado['plazo_aplicado'], 8)
        self.assertEqual(resultado['tasa_aplicada'], Decimal('2.00'))
        self.assertEqual(resultado['fecha_primera_cuota'], date(2026, 3, 30))
        self.assertEqual(resultado['valor_cuota_nuevo'], Decimal('385640.18'))
        self.assertEqual(resultado['total_a_pagar_nuevo'], Decimal('3085121.44'))
        self.assertEqual(resultado['saldo_pendiente_nuevo'], Decimal('3085121.44'))
        self.assertEqual(len(resultado['cuotas_generadas']), 8)
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.iva_comision, Decimal('38000.00'))

    def test_persist_regenera_amortizacion_y_deja_iva_en_cero(self):
        credit_services.recalcular_credito_especial_sin_iva_comision(
            self.credito,
            persist=True,
        )
        self.credito.refresh_from_db()

        self.assertEqual(self.credito.iva_comision, Decimal('0.00'))
        self.assertEqual(self.credito.valor_cuota, Decimal('385640.18'))
        self.assertEqual(self.credito.total_a_pagar, Decimal('3085121.44'))
        self.assertEqual(self.credito.saldo_pendiente, Decimal('3085121.44'))
        self.assertEqual(self.credito.capital_pendiente, Decimal('2625000.00'))
        self.assertEqual(self.credito.fecha_proximo_pago, date(2026, 3, 30))
        self.assertEqual(self.credito.tabla_amortizacion.count(), 8)
        self.assertTrue(
            self.credito.observacion_regla_especial.startswith('Credito especial prueba.')
        )

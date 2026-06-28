from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CuotaAmortizacion, HistorialPago
from gestion_creditos.services.credit_recalculation import (
    obtener_resumen_pagos_credito,
    recalcular_credito_desde_tabla_amortizacion,
)


User = get_user_model()


class CreditRecalculationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='recalc-user', password='123456')

    def test_tabla_vacia_sin_pagos_usa_fallback_historial(self):
        credito = self.create_credito(saldo_pendiente=Decimal('500.00'), capital_pendiente=Decimal('400.00'))

        resumen = obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen['fuente'], 'historial_pagos')
        self.assertEqual(resumen['cuotas_pagadas'], 0)
        self.assertEqual(resumen['cuotas_restantes'], 3)
        self.assertEqual(resumen['total_pagado'], Decimal('0.00'))
        self.assertEqual(resumen['saldo_pendiente'], Decimal('500.00'))
        self.assertEqual(resumen['capital_pendiente'], Decimal('400.00'))

    def test_tabla_vacia_con_pagos_cuenta_historial_exitoso(self):
        credito = self.create_credito(saldo_pendiente=Decimal('300.00'), capital_pendiente=Decimal('250.00'))
        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('100.00'),
            referencia_pago='RECALC-1',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )

        resumen = obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen['fuente'], 'historial_pagos')
        self.assertEqual(resumen['cuotas_pagadas'], 1)
        self.assertEqual(resumen['cuotas_restantes'], 2)
        self.assertEqual(resumen['total_pagado'], Decimal('100.00'))

    def test_cuotas_pagadas_y_pendientes_usan_tabla_como_fuente(self):
        credito = self.create_credito()
        self.create_cuota(credito, 1, pagada=True, monto_pagado=Decimal('100.00'), capital=Decimal('70.00'))
        self.create_cuota(credito, 2, pagada=False, capital=Decimal('80.00'))
        self.create_cuota(credito, 3, pagada=False, capital=Decimal('90.00'))

        resumen = obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen['fuente'], 'tabla_amortizacion')
        self.assertEqual(resumen['cuotas_pagadas'], 1)
        self.assertEqual(resumen['cuotas_restantes'], 2)
        self.assertEqual(resumen['total_pagado'], Decimal('100.00'))
        self.assertEqual(resumen['saldo_pendiente'], Decimal('200.00'))
        self.assertEqual(resumen['capital_pendiente'], Decimal('170.00'))

    def test_recalculo_persistente_actualiza_credito_en_mora_a_activo_si_quedan_cuotas(self):
        credito = self.create_credito(estado=Credito.EstadoCredito.EN_MORA)
        self.create_cuota(credito, 1, pagada=True, monto_pagado=Decimal('100.00'), capital=Decimal('70.00'))
        self.create_cuota(credito, 2, pagada=False, capital=Decimal('80.00'))

        resumen = recalcular_credito_desde_tabla_amortizacion(credito, persist=True)

        credito.refresh_from_db()
        self.assertEqual(resumen['cuotas_restantes'], 1)
        self.assertEqual(credito.estado, Credito.EstadoCredito.ACTIVO)
        self.assertEqual(credito.saldo_pendiente, Decimal('100.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('80.00'))

    def test_wrapper_legacy_equivale_al_servicio_nuevo(self):
        credito = self.create_credito()
        self.create_cuota(credito, 1, pagada=True, monto_pagado=Decimal('100.00'), capital=Decimal('70.00'))
        self.create_cuota(credito, 2, pagada=False, capital=Decimal('80.00'))

        resumen_servicio = obtener_resumen_pagos_credito(credito)
        resumen_legacy = credit_services.obtener_resumen_pagos_credito(credito)

        self.assertEqual(resumen_legacy, resumen_servicio)

    def test_wrapper_legacy_de_recalculo_equivale_al_servicio_nuevo(self):
        credito_servicio = self.create_credito(numero='CR-RECALC-SERVICE')
        credito_legacy = self.create_credito(numero='CR-RECALC-LEGACY')
        for credito in [credito_servicio, credito_legacy]:
            self.create_cuota(credito, 1, pagada=True, monto_pagado=Decimal('100.00'), capital=Decimal('70.00'))
            self.create_cuota(credito, 2, pagada=False, capital=Decimal('80.00'))

        resumen_servicio = recalcular_credito_desde_tabla_amortizacion(credito_servicio, persist=True)
        resumen_legacy = credit_services.recalcular_credito_desde_tabla_amortizacion(credito_legacy, persist=True)

        self.assertEqual(resumen_legacy, resumen_servicio)

    def create_credito(self, **overrides):
        data = {
            'usuario': self.user,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'numero_credito': overrides.pop('numero', ''),
            'monto_solicitado': Decimal('300.00'),
            'monto_aprobado': Decimal('300.00'),
            'plazo_solicitado': 3,
            'plazo': 3,
            'valor_cuota': Decimal('100.00'),
            'saldo_pendiente': Decimal('300.00'),
            'capital_pendiente': Decimal('240.00'),
            'fecha_proximo_pago': timezone.localdate(),
        }
        data.update(overrides)
        return Credito.objects.create(**data)

    def create_cuota(self, credito, numero, *, pagada, capital, monto_pagado=None):
        return CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=numero,
            fecha_vencimiento=timezone.localdate(),
            capital_a_pagar=capital,
            interes_a_pagar=Decimal('100.00') - capital,
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=max(Decimal('0.00'), Decimal('300.00') - (capital * numero)),
            pagada=pagada,
            monto_pagado=monto_pagado,
            fecha_pago=timezone.now() if pagada else None,
        )

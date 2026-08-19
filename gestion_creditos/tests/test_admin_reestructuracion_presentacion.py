from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.models import (
    Credito,
    CuotaAmortizacion,
    HistorialPago,
    ReestructuracionCredito,
)


User = get_user_model()


class AdminReestructuracionPresentacionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff-reestructuracion',
            password='clave-segura',
            is_staff=True,
        )
        self.cliente = User.objects.create_user(
            username='cliente-reestructuracion',
            password='clave-segura',
        )
        self.client.force_login(self.staff)

    def _crear_credito(self, numero, capital_pendiente, saldo_pendiente, proximo_pago):
        return Credito.objects.create(
            usuario=self.cliente,
            numero_credito=numero,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('30000000.00'),
            monto_aprobado=Decimal('30000000.00'),
            plazo_solicitado=48,
            plazo=48,
            tasa_interes=Decimal('1.90'),
            valor_cuota=Decimal('1097379.98'),
            total_a_pagar=Decimal('34355400.00'),
            capital_pendiente=capital_pendiente,
            saldo_pendiente=saldo_pendiente,
            fecha_proximo_pago=proximo_pago,
        )

    def _crear_cuota(
        self,
        credito,
        numero,
        vencimiento,
        capital,
        interes,
        valor,
        saldo,
        pagada=False,
    ):
        return CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=numero,
            fecha_vencimiento=vencimiento,
            capital_a_pagar=capital,
            interes_a_pagar=interes,
            valor_cuota=valor,
            saldo_capital_pendiente=saldo,
            pagada=pagada,
            fecha_pago=timezone.now() if pagada else None,
            monto_pagado=valor if pagada else None,
        )

    def test_credito_sin_reestructuracion_separa_historico_y_plan(self):
        credito = self._crear_credito(
            'CR-PRESENTACION-SIMPLE',
            Decimal('600000.00'),
            Decimal('660000.00'),
            date(2026, 9, 1),
        )
        self._crear_cuota(
            credito, 1, date(2026, 8, 1), Decimal('400000.00'),
            Decimal('50000.00'), Decimal('450000.00'), Decimal('600000.00'), True,
        )
        self._crear_cuota(
            credito, 2, date(2026, 9, 1), Decimal('600000.00'),
            Decimal('60000.00'), Decimal('660000.00'), Decimal('0.00'), False,
        )

        response = self.client.get(reverse('gestion:credito_detalle', args=[credito.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['historico_pagado']['cantidad_cuotas'], 1)
        self.assertEqual(response.context['historico_pagado']['capital_amortizado'], Decimal('400000.00'))
        self.assertEqual(response.context['historico_pagado']['intereses_pagados'], Decimal('50000.00'))
        self.assertEqual(response.context['plan_vigente']['cantidad_cuotas'], 1)
        self.assertEqual(response.context['plan_vigente']['intereses_futuros'], Decimal('60000.00'))
        self.assertEqual(response.context['abonos_extraordinarios'], [])
        self.assertContains(response, 'Histórico pagado')
        self.assertContains(response, 'Plan vigente')
        self.assertNotContains(response, 'TOTALES:')

    def test_credito_reestructurado_usa_exclusivamente_valores_persistidos(self):
        credito = self._crear_credito(
            'CR-2026-00042',
            Decimal('4457697.30'),
            Decimal('4685077.99'),
            date(2026, 9, 1),
        )
        self._crear_cuota(
            credito, 1, date(2026, 7, 1), Decimal('500000.00'),
            Decimal('100000.00'), Decimal('600000.00'), Decimal('33957697.30'), True,
        )
        self._crear_cuota(
            credito, 2, date(2026, 8, 1), Decimal('500000.00'),
            Decimal('90000.00'), Decimal('590000.00'), Decimal('33457697.30'), True,
        )

        plan = [
            (3, date(2026, 9, 1), '1012683.73', '84696.25', '1097379.98', '3445013.57'),
            (4, date(2026, 10, 1), '1031924.72', '65455.26', '1097379.98', '2413088.85'),
            (5, date(2026, 11, 1), '1051531.29', '45848.69', '1097379.98', '1361557.56'),
            (6, date(2026, 12, 1), '1071510.39', '25869.59', '1097379.98', '290047.17'),
            (7, date(2027, 1, 1), '290047.17', '5510.90', '295558.07', '0.00'),
        ]
        for numero, vencimiento, capital, interes, valor, saldo in plan:
            self._crear_cuota(
                credito,
                numero,
                vencimiento,
                Decimal(capital),
                Decimal(interes),
                Decimal(valor),
                Decimal(saldo),
            )

        pago = HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('29000000.00'),
            referencia_pago='ABONO-CAPITAL-64',
            estado=HistorialPago.EstadoPago.EXITOSO,
            capital_abonado=Decimal('29000000.00'),
            intereses_pagados=Decimal('0.00'),
        )
        reestructuracion = ReestructuracionCredito.objects.create(
            credito=credito,
            monto_abonado=Decimal('29000000.00'),
            tipo_abono=ReestructuracionCredito.TipoAbono.CAPITAL_REDUCIR_PLAZO,
            plan_anterior={'cuotas': []},
            plan_nuevo={'cuotas': []},
            saldo_pendiente_anterior=Decimal('50479390.00'),
            capital_pendiente_anterior=Decimal('33457697.30'),
            plazo_restante_anterior=46,
            saldo_pendiente_nuevo=Decimal('4685077.99'),
            capital_pendiente_nuevo=Decimal('4457697.30'),
            plazo_restante_nuevo=5,
            ahorro_intereses=Decimal('16794400.87'),
            cuota_mensual_nueva=Decimal('1097379.98'),
            pago_relacionado=pago,
            aprobado_por=self.staff,
        )

        estado_financiero_antes = (
            credito.capital_pendiente,
            credito.saldo_pendiente,
            credito.valor_cuota,
            credito.fecha_proximo_pago,
        )
        cantidades_antes = (
            credito.tabla_amortizacion.count(),
            credito.historial_pagos.count(),
            credito.reestructuraciones.count(),
        )

        response = self.client.get(reverse('gestion:credito_detalle', args=[credito.id]))

        self.assertEqual(response.status_code, 200)
        historico = response.context['historico_pagado']
        vigente = response.context['plan_vigente']
        abonos = response.context['abonos_extraordinarios']
        self.assertEqual(historico['cantidad_cuotas'], 2)
        self.assertEqual(historico['capital_amortizado'], Decimal('1000000.00'))
        self.assertEqual(historico['intereses_pagados'], Decimal('190000.00'))
        self.assertEqual(vigente['cantidad_cuotas'], 5)
        self.assertEqual(vigente['capital_pendiente'], Decimal('4457697.30'))
        self.assertEqual(vigente['intereses_futuros'], Decimal('227380.69'))
        self.assertEqual(vigente['saldo_programado_pendiente'], Decimal('4685077.99'))
        self.assertEqual(vigente['proximo_vencimiento'], date(2026, 9, 1))
        self.assertEqual(len(abonos), 1)
        self.assertEqual(abonos[0].pk, reestructuracion.pk)
        self.assertEqual(abonos[0].monto_abonado, Decimal('29000000.00'))
        self.assertEqual(abonos[0].pago_relacionado_id, pago.id)
        self.assertEqual(response.context['total_abonos_extraordinarios'], Decimal('29000000.00'))
        self.assertContains(response, 'Abono extraordinario a capital', count=1)
        self.assertContains(response, 'Reducción de plazo')
        self.assertNotContains(response, 'TOTALES:')

        credito.refresh_from_db()
        self.assertEqual(
            (
                credito.capital_pendiente,
                credito.saldo_pendiente,
                credito.valor_cuota,
                credito.fecha_proximo_pago,
            ),
            estado_financiero_antes,
        )
        self.assertEqual(
            (
                credito.tabla_amortizacion.count(),
                credito.historial_pagos.count(),
                credito.reestructuraciones.count(),
            ),
            cantidades_antes,
        )

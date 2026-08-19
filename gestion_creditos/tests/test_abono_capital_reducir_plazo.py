from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import (
    Credito,
    CuotaAmortizacion,
    DetalleContablePago,
    HistorialPago,
    ReestructuracionCredito,
)


User = get_user_model()
CENTAVO = Decimal('0.01')


def _q(valor):
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


class AbonoCapitalReducirPlazoTest(TestCase):
    CAPITAL_PENDIENTE = Decimal('33457697.33')
    MONTO_ABONO = Decimal('29000000.00')
    CUOTA_OBJETIVO = Decimal('1097379.98')
    TASA_MENSUAL = Decimal('0.019')

    def setUp(self):
        self.usuario = User.objects.create_user(
            username='analista-abono-plazo',
            email='analista.abono@example.test',
            password='test-only-password',
        )
        self.credito = Credito.objects.create(
            usuario=self.usuario,
            numero_credito='CR-2026-00042-TEST',
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('30000000.00'),
            monto_aprobado=Decimal('30000000.00'),
            plazo_solicitado=48,
            plazo=48,
            tasa_interes=Decimal('1.90'),
            comision=Decimal('3660000.00'),
            iva_comision=Decimal('695400.00'),
            total_a_pagar=Decimal('34355400.00'),
            saldo_pendiente=self.CAPITAL_PENDIENTE,
            capital_pendiente=self.CAPITAL_PENDIENTE,
            valor_cuota=self.CUOTA_OBJETIVO,
            fecha_proximo_pago=date(2026, 9, 1),
        )
        self._crear_plan_original()
        saldo_plan = sum(
            (
                cuota.valor_cuota
                for cuota in self.credito.tabla_amortizacion.filter(pagada=False)
            ),
            Decimal('0.00'),
        )
        Credito.objects.filter(pk=self.credito.pk).update(saldo_pendiente=saldo_plan)
        self.credito.refresh_from_db()

    def _crear_plan_original(self):
        capital_pagado = Decimal('897702.67')
        capital_primera = Decimal('448851.34')
        capital_segunda = capital_pagado - capital_primera
        for numero, capital, saldo in (
            (1, capital_primera, Decimal('33906548.66')),
            (2, capital_segunda, self.CAPITAL_PENDIENTE),
        ):
            cuota = CuotaAmortizacion.objects.create(
                credito=self.credito,
                numero_cuota=numero,
                fecha_vencimiento=date(2026, 6 + numero, 1),
                capital_a_pagar=capital,
                interes_a_pagar=self.CUOTA_OBJETIVO - capital,
                valor_cuota=self.CUOTA_OBJETIVO,
                saldo_capital_pendiente=saldo,
                pagada=True,
                monto_pagado=self.CUOTA_OBJETIVO,
                fecha_pago=timezone.now(),
            )
            pago = HistorialPago.objects.create(
                credito=self.credito,
                monto=self.CUOTA_OBJETIVO,
                referencia_pago=f'PAGO-HISTORICO-{numero}-CR-00042',
                estado=HistorialPago.EstadoPago.EXITOSO,
                capital_abonado=capital,
                intereses_pagados=self.CUOTA_OBJETIVO - capital,
            )
            DetalleContablePago.objects.create(
                pago=pago,
                credito=self.credito,
                cuota=cuota,
                secuencia_aplicacion=1,
                fecha_aplicacion=pago.fecha_aplicacion,
                monto_total_aplicado=self.CUOTA_OBJETIVO,
                capital_aplicado=capital,
                interes_aplicado=self.CUOTA_OBJETIVO - capital,
                capital_principal_aplicado=capital,
                comision_aplicada=Decimal('0.00'),
                iva_aplicado=Decimal('0.00'),
                metodologia_calculo=(
                    DetalleContablePago.MetodologiaCalculo.CUOTA_INTERES_PRIMERO
                ),
            )

        saldo = self.CAPITAL_PENDIENTE
        for numero in range(3, 49):
            interes = _q(saldo * self.TASA_MENSUAL)
            if numero == 48:
                capital = saldo
                valor_cuota = _q(capital + interes)
                saldo_nuevo = Decimal('0.00')
            else:
                valor_cuota = self.CUOTA_OBJETIVO
                capital = _q(valor_cuota - interes)
                saldo_nuevo = _q(saldo - capital)
            CuotaAmortizacion.objects.create(
                credito=self.credito,
                numero_cuota=numero,
                fecha_vencimiento=date(2026, 9, 1)
                + credit_services.relativedelta(months=numero - 3),
                capital_a_pagar=capital,
                interes_a_pagar=interes,
                valor_cuota=valor_cuota,
                saldo_capital_pendiente=saldo_nuevo,
                pagada=False,
            )
            saldo = saldo_nuevo

    def test_abono_reducir_plazo_genera_plan_3_a_7_y_trazabilidad(self):
        pagos_historicos_antes = list(
            HistorialPago.objects.filter(credito=self.credito)
            .order_by('id')
            .values()
        )
        detalles_historicos_antes = list(
            DetalleContablePago.objects.filter(credito=self.credito)
            .order_by('id')
            .values()
        )
        cuotas_pagadas_antes = list(
            self.credito.tabla_amortizacion.filter(pagada=True)
            .order_by('numero_cuota')
            .values(
                'id',
                'numero_cuota',
                'fecha_vencimiento',
                'capital_a_pagar',
                'interes_a_pagar',
                'valor_cuota',
                'saldo_capital_pendiente',
                'pagada',
                'monto_pagado',
                'fecha_pago',
            )
        )

        pago, reestructuracion = credit_services.aplicar_abono_credito(
            credito=self.credito,
            monto_abono=self.MONTO_ABONO,
            tipo_abono=credit_services.TIPO_ABONO_CAPITAL_REDUCIR_PLAZO,
            usuario=self.usuario,
            referencia_pago='ABONO-CR-2026-00042-REDUCIR-PLAZO',
        )

        self.credito.refresh_from_db()
        pago.refresh_from_db()
        reestructuracion.refresh_from_db()
        cuotas = list(self.credito.tabla_amortizacion.order_by('numero_cuota'))
        cuotas_pendientes = [cuota for cuota in cuotas if not cuota.pagada]
        cuotas_pagadas_despues = list(
            self.credito.tabla_amortizacion.filter(pagada=True)
            .order_by('numero_cuota')
            .values(
                'id',
                'numero_cuota',
                'fecha_vencimiento',
                'capital_a_pagar',
                'interes_a_pagar',
                'valor_cuota',
                'saldo_capital_pendiente',
                'pagada',
                'monto_pagado',
                'fecha_pago',
            )
        )

        self.assertEqual(self.credito.capital_pendiente, Decimal('4457697.33'))
        self.assertEqual(self.credito.saldo_pendiente, Decimal('4685078.02'))
        self.assertEqual(self.credito.valor_cuota, self.CUOTA_OBJETIVO)
        self.assertEqual(self.credito.fecha_proximo_pago, date(2026, 9, 1))
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.ACTIVO)
        self.assertEqual(cuotas_pagadas_despues, cuotas_pagadas_antes)
        self.assertEqual(
            list(
                HistorialPago.objects.filter(
                    credito=self.credito,
                    referencia_pago__startswith='PAGO-HISTORICO-',
                ).order_by('id').values()
            ),
            pagos_historicos_antes,
        )
        self.assertEqual(
            list(
                DetalleContablePago.objects.filter(
                    credito=self.credito,
                    pago__referencia_pago__startswith='PAGO-HISTORICO-',
                ).order_by('id').values()
            ),
            detalles_historicos_antes,
        )
        self.assertEqual([cuota.numero_cuota for cuota in cuotas_pendientes], [3, 4, 5, 6, 7])
        self.assertEqual(
            [cuota.fecha_vencimiento for cuota in cuotas_pendientes],
            [
                date(2026, 9, 1),
                date(2026, 10, 1),
                date(2026, 11, 1),
                date(2026, 12, 1),
                date(2027, 1, 1),
            ],
        )
        self.assertTrue(all(not cuota.pagada for cuota in cuotas_pendientes))
        self.assertTrue(all(cuota.valor_cuota == self.CUOTA_OBJETIVO for cuota in cuotas_pendientes[:-1]))
        self.assertLess(cuotas_pendientes[-1].valor_cuota, self.CUOTA_OBJETIVO)
        self.assertEqual(cuotas_pendientes[-1].saldo_capital_pendiente, Decimal('0.00'))
        self.assertFalse(self.credito.tabla_amortizacion.filter(numero_cuota__gte=8).exists())
        self.assertEqual(
            [
                (
                    cuota.numero_cuota,
                    cuota.capital_a_pagar,
                    cuota.interes_a_pagar,
                    cuota.valor_cuota,
                    cuota.saldo_capital_pendiente,
                )
                for cuota in cuotas_pendientes
            ],
            [
                (3, Decimal('1012683.73'), Decimal('84696.25'), Decimal('1097379.98'), Decimal('3445013.60')),
                (4, Decimal('1031924.72'), Decimal('65455.26'), Decimal('1097379.98'), Decimal('2413088.88')),
                (5, Decimal('1051531.29'), Decimal('45848.69'), Decimal('1097379.98'), Decimal('1361557.59')),
                (6, Decimal('1071510.39'), Decimal('25869.59'), Decimal('1097379.98'), Decimal('290047.20')),
                (7, Decimal('290047.20'), Decimal('5510.90'), Decimal('295558.10'), Decimal('0.00')),
            ],
        )

        self.assertEqual(pago.estado, HistorialPago.EstadoPago.EXITOSO)
        self.assertEqual(pago.monto, self.MONTO_ABONO)
        self.assertEqual(pago.capital_abonado, self.MONTO_ABONO)
        self.assertEqual(pago.intereses_pagados, Decimal('0.00'))
        detalle = DetalleContablePago.objects.get(pago=pago)
        self.assertIsNone(detalle.cuota_id)
        self.assertEqual(detalle.capital_aplicado, self.MONTO_ABONO)
        self.assertEqual(detalle.interes_aplicado, Decimal('0.00'))
        self.assertEqual(
            detalle.metodologia_calculo,
            DetalleContablePago.MetodologiaCalculo.ABONO_CAPITAL_DIRECTO,
        )

        self.assertEqual(
            reestructuracion.tipo_abono,
            ReestructuracionCredito.TipoAbono.CAPITAL_REDUCIR_PLAZO,
        )
        self.assertEqual(reestructuracion.monto_abonado, self.MONTO_ABONO)
        self.assertEqual(reestructuracion.capital_pendiente_anterior, self.CAPITAL_PENDIENTE)
        self.assertEqual(reestructuracion.capital_pendiente_nuevo, Decimal('4457697.33'))
        self.assertEqual(
            reestructuracion.saldo_pendiente_anterior,
            sum(
                (Decimal(str(cuota['cuota'])) for cuota in reestructuracion.plan_anterior['cuotas']),
                Decimal('0.00'),
            ),
        )
        self.assertEqual(reestructuracion.saldo_pendiente_nuevo, Decimal('4685078.02'))
        self.assertEqual(reestructuracion.plazo_restante_anterior, 46)
        self.assertEqual(reestructuracion.plazo_restante_nuevo, 5)
        self.assertEqual(reestructuracion.cuota_mensual_nueva, self.CUOTA_OBJETIVO)
        self.assertEqual(reestructuracion.ahorro_intereses, Decimal('16794400.79'))
        self.assertEqual(reestructuracion.pago_relacionado, pago)
        self.assertEqual(reestructuracion.aprobado_por, self.usuario)
        self.assertEqual(
            [cuota['numero'] for cuota in reestructuracion.plan_nuevo['cuotas']],
            [3, 4, 5, 6, 7],
        )

    def test_falla_recalculo_revierte_pago_contabilidad_reestructuracion_y_plan(self):
        pagos_antes = list(
            HistorialPago.objects.filter(credito=self.credito).order_by('id').values()
        )
        detalles_antes = list(
            DetalleContablePago.objects.filter(credito=self.credito).order_by('id').values()
        )
        credito_antes = {
            'saldo_pendiente': self.credito.saldo_pendiente,
            'capital_pendiente': self.credito.capital_pendiente,
            'valor_cuota': self.credito.valor_cuota,
            'fecha_proximo_pago': self.credito.fecha_proximo_pago,
            'estado': self.credito.estado,
        }
        cuotas_antes = list(
            self.credito.tabla_amortizacion.order_by('numero_cuota').values()
        )

        with patch(
            'gestion_creditos.credit_services._recalcular_amortizacion_por_capital',
            side_effect=RuntimeError('falla controlada de persistencia'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'falla controlada de persistencia'):
                credit_services.aplicar_abono_credito(
                    credito=self.credito,
                    monto_abono=self.MONTO_ABONO,
                    tipo_abono=credit_services.TIPO_ABONO_CAPITAL_REDUCIR_PLAZO,
                    usuario=self.usuario,
                    referencia_pago='ABONO-ROLLBACK-REDUCIR-PLAZO',
                )

        self.credito.refresh_from_db()
        self.assertEqual(
            {
                'saldo_pendiente': self.credito.saldo_pendiente,
                'capital_pendiente': self.credito.capital_pendiente,
                'valor_cuota': self.credito.valor_cuota,
                'fecha_proximo_pago': self.credito.fecha_proximo_pago,
                'estado': self.credito.estado,
            },
            credito_antes,
        )
        self.assertEqual(
            list(self.credito.tabla_amortizacion.order_by('numero_cuota').values()),
            cuotas_antes,
        )
        self.assertEqual(
            list(HistorialPago.objects.filter(credito=self.credito).order_by('id').values()),
            pagos_antes,
        )
        self.assertFalse(ReestructuracionCredito.objects.filter(credito=self.credito).exists())
        self.assertEqual(
            list(DetalleContablePago.objects.filter(credito=self.credito).order_by('id').values()),
            detalles_antes,
        )

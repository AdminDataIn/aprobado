from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos.models import Credito
from risk.selectors import obtener_ultimo_credito_para_revision_segundo_credito
from risk.services.second_credit import (
    MOTIVO_DATOS_DE_PAGO_INSUFICIENTES,
    MOTIVO_MINIMO_PAGADO_CUMPLIDO,
    MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO,
    MOTIVO_MORA_ACTIVA_RELEVANTE,
    MOTIVO_SIN_CREDITO_PREVIO,
    PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO,
    SecondCreditService,
    ServicioSegundoCredito,
    calcular_porcentaje_pagado,
    evaluate_second_credit_eligibility,
    evaluar_elegibilidad_segundo_credito,
)


User = get_user_model()


class SecondCreditEligibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='risk-second-credit',
            email='risk.second@example.com',
        )

    def create_credit(self, **overrides):
        data = {
            'usuario': self.user,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'monto_solicitado': Decimal('1000000.00'),
            'monto_aprobado': Decimal('1000000.00'),
            'plazo_solicitado': 6,
            'plazo': 6,
            'capital_pendiente': Decimal('700000.00'),
            'saldo_pendiente': Decimal('700000.00'),
            'total_a_pagar': Decimal('1200000.00'),
        }
        data.update(overrides)
        return Credito.objects.create(**data)

    def test_sin_credito_previo_es_elegible(self):
        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(result['eligible'])
        self.assertEqual(result['reason'], MOTIVO_SIN_CREDITO_PREVIO)
        self.assertIsNone(result['paid_percentage'])
        self.assertEqual(result['required_percentage'], PORCENTAJE_MINIMO_PAGADO_SEGUNDO_CREDITO)
        self.assertIsNone(result['blocking_credit_id'])

    def test_credito_con_menos_de_40_pagado_bloquea(self):
        credit = self.create_credit(capital_pendiente=Decimal('700000.00'))

        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO)
        self.assertEqual(result['paid_percentage'], Decimal('30.00'))
        self.assertEqual(result['required_percentage'], Decimal('40'))
        self.assertEqual(result['blocking_credit_id'], credit.id)

    def test_credito_con_40_o_mas_pagado_es_elegible(self):
        credit = self.create_credit(
            capital_pendiente=Decimal('600000.00'),
            valor_cuota=Decimal('120000.00'),
        )

        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
            ingreso_mensual=Decimal('2000000.00'),
            cuota_proyectada=Decimal('250000.00'),
        )

        self.assertTrue(result['eligible'])
        self.assertEqual(result['reason'], MOTIVO_MINIMO_PAGADO_CUMPLIDO)
        self.assertEqual(result['paid_percentage'], Decimal('40.00'))
        self.assertIsNone(result['blocking_credit_id'])
        self.assertEqual(result['current_installment'], Decimal('120000.00'))
        self.assertEqual(result['projected_installment'], Decimal('250000.00'))
        self.assertEqual(result['committed_percentage'], Decimal('18.50'))
        self.assertEqual(result['residual_capacity'], Decimal('630000.00'))
        self.assertEqual(calcular_porcentaje_pagado(credit), Decimal('40.00'))

    def test_credito_en_mora_relevante_bloquea(self):
        credit = self.create_credit(
            estado=Credito.EstadoCredito.EN_MORA,
            capital_pendiente=Decimal('500000.00'),
            saldo_pendiente=Decimal('500000.00'),
        )

        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], MOTIVO_MORA_ACTIVA_RELEVANTE)
        self.assertEqual(result['blocking_credit_id'], credit.id)

    def test_credito_rechazado_por_capacidad(self):
        credit = self.create_credit(
            capital_pendiente=Decimal('500000.00'),
            valor_cuota=Decimal('400000.00'),
        )

        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
            ingreso_mensual=Decimal('1000000.00'),
            cuota_proyectada=Decimal('200000.00'),
        )

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'capacidad_insuficiente')
        self.assertEqual(result['blocking_credit_id'], credit.id)
        self.assertEqual(result['maximum_capacity'], Decimal('500000.00'))
        self.assertEqual(result['residual_capacity'], Decimal('-100000.00'))

    def test_credito_activo_bloqueante_sin_datos_de_pago(self):
        credit = self.create_credit(monto_aprobado=None, capital_pendiente=None)

        result = evaluar_elegibilidad_segundo_credito(
            cliente_id=self.user.id,
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], MOTIVO_DATOS_DE_PAGO_INSUFICIENTES)
        self.assertIsNone(result['paid_percentage'])
        self.assertEqual(result['blocking_credit_id'], credit.id)

    def test_selector_ignora_credito_pagado_y_usa_credito_activo(self):
        self.create_credit(
            estado=Credito.EstadoCredito.PAGADO,
            capital_pendiente=Decimal('0.00'),
        )
        active_credit = self.create_credit(capital_pendiente=Decimal('800000.00'))

        self.assertEqual(
            obtener_ultimo_credito_para_revision_segundo_credito(
                cliente_id=self.user.id,
                linea_credito=Credito.LineaCredito.LIBRANZA,
            ),
            active_credit,
        )

    def test_service_class_returns_same_payload(self):
        self.create_credit(capital_pendiente=Decimal('600000.00'))

        self.assertEqual(
            ServicioSegundoCredito().evaluar(
                cliente_id=self.user.id,
                linea_credito=Credito.LineaCredito.LIBRANZA,
            ),
            evaluar_elegibilidad_segundo_credito(
                cliente_id=self.user.id,
                linea_credito=Credito.LineaCredito.LIBRANZA,
            ),
        )

    def test_alias_legacy_en_ingles_siguen_funcionando(self):
        self.create_credit(capital_pendiente=Decimal('600000.00'))

        self.assertEqual(
            SecondCreditService().evaluate(
                customer_id=self.user.id,
                product_type=Credito.LineaCredito.LIBRANZA,
            ),
            evaluate_second_credit_eligibility(
                customer_id=self.user.id,
                product_type=Credito.LineaCredito.LIBRANZA,
            ),
        )

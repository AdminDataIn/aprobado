from decimal import Decimal

from django.test import SimpleTestCase

from risk.services.policy_engine import RiskPolicyEngine


class RiskPolicyEngineTests(SimpleTestCase):
    def test_evalua_capacidad(self):
        decision = RiskPolicyEngine().evaluate(
            'capacidad',
            {
                'ingreso_mensual': Decimal('1000000.00'),
                'cuota_actual': Decimal('200000.00'),
                'cuota_proyectada': Decimal('100000.00'),
                'porcentaje_maximo': Decimal('50'),
            },
        )

        self.assertTrue(decision.aprobado)
        self.assertEqual(decision.metadata['committed_percentage'], Decimal('30.00'))
        self.assertEqual(decision.metadata['residual_capacity'], Decimal('200000.00'))

    def test_evalua_minimo_pagado(self):
        decision = RiskPolicyEngine().evaluate(
            'minimo_pagado',
            {
                'porcentaje_pagado': Decimal('30.00'),
                'porcentaje_requerido': Decimal('40.00'),
            },
        )

        self.assertFalse(decision.aprobado)
        self.assertEqual(decision.motivos, ('minimo_pagado_no_cumplido',))

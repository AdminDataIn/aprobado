from importlib import import_module

from django.test import SimpleTestCase


class DomainStructureTests(SimpleTestCase):
    def test_risk_domain_modules_are_importable(self):
        modules = [
            'risk',
            'risk.selectors',
            'risk.services',
            'risk.services.affordability',
            'risk.services.second_credit',
            'risk.services.portfolio_takeover',
            'risk.services.policy_engine',
            'risk.policies',
        ]

        for module in modules:
            with self.subTest(module=module):
                import_module(module)

    def test_libranza_domain_modules_are_importable(self):
        modules = [
            'libranza',
            'libranza.selectors',
            'libranza.services',
            'libranza.services.legal_rules',
            'libranza.services.payment_capacity',
            'libranza.services.payer_validation',
            'libranza.services.payroll_law',
            'libranza.policies',
        ]

        for module in modules:
            with self.subTest(module=module):
                import_module(module)

    def test_servicio_simulacion_credito_expone_api_en_espanol_y_aliases_legacy(self):
        modulo = import_module('gestion_creditos.services.credit_simulation')

        self.assertTrue(hasattr(modulo, 'simular_credito'))
        self.assertTrue(hasattr(modulo, 'obtener_configuracion_producto'))
        self.assertTrue(hasattr(modulo, 'ConfiguracionProductoCredito'))
        self.assertIs(modulo.calculate_credit_simulation.__name__, 'calculate_credit_simulation')
        self.assertEqual(modulo.PRODUCT_PAYROLL_LOAN, modulo.PRODUCTO_LIBRANZA)
        self.assertEqual(modulo.SUPPORTED_PRODUCTS, modulo.PRODUCTOS_SOPORTADOS)

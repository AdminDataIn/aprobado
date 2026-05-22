from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from gestion_creditos.services import libranza_rules as legacy_rules
from libranza.services import legal_rules
from libranza.services.legal_rules import LibranzaLegalInput, LibranzaLegalRulesService


class LibranzaLegalRulesMirrorTests(SimpleTestCase):
    def test_primera_fecha_pago_matches_legacy_import(self):
        cases = [
            date(2026, 3, 14),
            date(2026, 3, 15),
            date(2026, 12, 20),
            datetime(2026, 4, 10, 8, 30),
        ]

        for fecha in cases:
            with self.subTest(fecha=fecha):
                self.assertEqual(
                    legal_rules.calcular_primera_fecha_pago_libranza(fecha),
                    legacy_rules.calcular_primera_fecha_pago_libranza(fecha),
                )

    def test_fecha_forzada_matches_legacy_import(self):
        self.assertEqual(
            legal_rules.calcular_primera_fecha_pago_libranza(fecha_forzada=date(2026, 7, 18)),
            legacy_rules.calcular_primera_fecha_pago_libranza(fecha_forzada=date(2026, 7, 18)),
        )

    def test_sumar_meses_con_dia_ancla_matches_legacy_import(self):
        cases = [
            (date(2026, 1, 31), 1, None),
            (date(2026, 1, 31), 2, 30),
            (date(2026, 2, 28), 1, 31),
        ]

        for fecha_base, meses, dia_ancla in cases:
            with self.subTest(fecha_base=fecha_base, meses=meses, dia_ancla=dia_ancla):
                self.assertEqual(
                    legal_rules.sumar_meses_con_dia_ancla(fecha_base, meses, dia_ancla),
                    legacy_rules.sumar_meses_con_dia_ancla(fecha_base, meses, dia_ancla),
                )

    def test_credito_value_helpers_match_legacy_import(self):
        credito = SimpleNamespace(
            plazo_forzado=None,
            plazo=6,
            plazo_solicitado=12,
            tasa_forzada=None,
            tasa_interes=None,
            fecha_primera_cuota_forzada=date(2026, 8, 1),
        )

        self.assertEqual(
            legal_rules.obtener_plazo_credito_aplicado(credito),
            legacy_rules.obtener_plazo_credito_aplicado(credito),
        )
        self.assertEqual(
            legal_rules.obtener_tasa_credito_aplicada(credito, 1.9),
            legacy_rules.obtener_tasa_credito_aplicada(credito, 1.9),
        )
        self.assertEqual(
            legal_rules.obtener_fecha_primera_cuota_credito(credito, date(2026, 7, 10)),
            legacy_rules.obtener_fecha_primera_cuota_credito(credito, date(2026, 7, 10)),
        )


class LibranzaBaseLegalRulesTests(SimpleTestCase):
    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_reglas_base_aprueban_caso_valido(self):
        decision = LibranzaLegalRulesService().evaluate(
            LibranzaLegalInput(
                monto_solicitado=Decimal('1000000.00'),
                ingreso_base=Decimal('2000000.00'),
                descuentos_actuales=Decimal('100000.00'),
                cuota_actual_libranza=Decimal('100000.00'),
                cuota_proyectada=Decimal('300000.00'),
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, 'reglas_libranza_cumplidas')
        self.assertTrue(decision.capacity['eligible'])

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_reglas_base_rechazan_monto_fuera_de_rango_y_capacidad(self):
        decision = legal_rules.evaluar_reglas_base_libranza(
            {
                'monto_solicitado': Decimal('50000.00'),
                'ingreso_base': Decimal('1000000.00'),
                'descuentos_actuales': Decimal('250000.00'),
                'cuota_actual_libranza': Decimal('150000.00'),
                'cuota_proyectada': Decimal('200000.00'),
            }
        )

        self.assertFalse(decision.allowed)
        self.assertIn('monto_menor_al_minimo', decision.reasons)
        self.assertIn('capacidad_insuficiente', decision.reasons)

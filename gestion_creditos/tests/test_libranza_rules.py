from datetime import date

from django.test import SimpleTestCase, override_settings

from gestion_creditos.services.libranza_rules import (
    calcular_primera_fecha_pago_libranza,
    permitir_multiples_creditos_libranza_en_pruebas,
)


class LibranzaRulesTests(SimpleTestCase):
    def test_fecha_aprobacion_antes_del_15_va_al_primero_del_mes_siguiente(self):
        self.assertEqual(
            calcular_primera_fecha_pago_libranza(date(2026, 3, 14)),
            date(2026, 4, 1),
        )

    def test_fecha_aprobacion_desde_el_15_va_al_primero_del_subsiguiente(self):
        self.assertEqual(
            calcular_primera_fecha_pago_libranza(date(2026, 3, 15)),
            date(2026, 5, 1),
        )

    def test_fecha_en_diciembre_salta_correctamente_de_anio(self):
        self.assertEqual(
            calcular_primera_fecha_pago_libranza(date(2026, 12, 20)),
            date(2027, 2, 1),
        )

    @override_settings(ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING=True)
    def test_flag_de_pruebas_habilita_multiples_creditos(self):
        self.assertTrue(permitir_multiples_creditos_libranza_en_pruebas())

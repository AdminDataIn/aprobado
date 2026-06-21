from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from libranza.escenarios_credito import (
    ESCENARIOS_CREDITO_LABELS,
    ESCENARIOS_CREDITO_LIBRANZA,
    NUEVO_CREDITO,
    RECOGIDA_CARTERA,
    SEGUNDO_CREDITO,
    es_nuevo_credito,
    es_recogida_cartera,
    es_segundo_credito,
    normalizar_escenario_credito,
    validar_escenario_credito,
)


class EscenariosCreditoLibranzaTests(SimpleTestCase):
    def test_contiene_escenarios_compartidos(self):
        self.assertEqual(
            set(ESCENARIOS_CREDITO_LIBRANZA),
            {NUEVO_CREDITO, SEGUNDO_CREDITO, RECOGIDA_CARTERA},
        )
        self.assertEqual(ESCENARIOS_CREDITO_LABELS[NUEVO_CREDITO], 'Nuevo credito')
        self.assertEqual(ESCENARIOS_CREDITO_LABELS[SEGUNDO_CREDITO], 'Segundo credito')
        self.assertEqual(ESCENARIOS_CREDITO_LABELS[RECOGIDA_CARTERA], 'Recogida de cartera')

    def test_normalizador_acepta_valores_validos(self):
        self.assertEqual(normalizar_escenario_credito('nuevo_credito'), NUEVO_CREDITO)
        self.assertEqual(normalizar_escenario_credito(' SEGUNDO_CREDITO '), SEGUNDO_CREDITO)
        self.assertEqual(normalizar_escenario_credito(RECOGIDA_CARTERA), RECOGIDA_CARTERA)
        self.assertTrue(validar_escenario_credito(NUEVO_CREDITO))

    def test_normalizador_rechaza_valores_invalidos(self):
        with self.assertRaises(ValidationError):
            normalizar_escenario_credito('credito_libre')

    def test_helpers_identifican_escenario(self):
        self.assertTrue(es_nuevo_credito(NUEVO_CREDITO))
        self.assertTrue(es_segundo_credito(SEGUNDO_CREDITO))
        self.assertTrue(es_recogida_cartera(RECOGIDA_CARTERA))
        self.assertFalse(es_nuevo_credito('credito_libre'))
        self.assertFalse(es_segundo_credito('credito_libre'))
        self.assertFalse(es_recogida_cartera('credito_libre'))

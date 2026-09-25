from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from contractors.forms import SolicitudPrestadorForm


CAMPOS_MONETARIOS = (
    'valor_total_contrato', 'valor_pagado_contrato',
    'valor_pendiente_cobrar', 'valor_mensual_contractual',
)


class MontosContractualesTest(SimpleTestCase):
    def test_formatos_equivalentes_y_centavos_en_los_cuatro_campos(self):
        formulario = SolicitudPrestadorForm()
        for nombre in CAMPOS_MONETARIOS:
            for entrada, esperado in (
                ('120000000', '120000000'),
                ('120.000.000', '120000000'),
                ('$ 120.000.000', '120000000'),
                ('$ 120.000.000,37', '120000000.37'),
                ('120000000.37', '120000000.37'),
                ('120,000,000.37', '120000000.37'),
                ('999999999999.99', '999999999999.99'),
            ):
                with self.subTest(campo=nombre, entrada=entrada):
                    self.assertEqual(formulario.fields[nombre].clean(entrada), Decimal(esperado))

    def test_rechaza_entradas_invalidas_sin_convertirlas_en_cero(self):
        formulario = SolicitudPrestadorForm()
        for nombre in CAMPOS_MONETARIOS:
            for entrada in ('1.20000000', 'abc', '-100', '1e8', '12..000', '1,23,45'):
                with self.subTest(campo=nombre, entrada=entrada):
                    with self.assertRaises(ValidationError):
                        formulario.fields[nombre].clean(entrada)
                    self.assertEqual(formulario.fields[nombre].prepare_value(entrada), entrada)

    def test_presentacion_conserva_miles_y_centavos_tras_post_invalido(self):
        campo = SolicitudPrestadorForm().fields['valor_total_contrato']
        for entrada in ('120.000', '$ 120.000', '120000', Decimal('120000')):
            self.assertEqual(campo.prepare_value(entrada), '120.000')
        self.assertEqual(campo.prepare_value('120000.37'), '120.000,37')

    def test_widgets_texto_con_teclado_decimal(self):
        formulario = SolicitudPrestadorForm()
        for nombre in CAMPOS_MONETARIOS:
            campo = formulario.fields[nombre]
            self.assertEqual(campo.widget.input_type, 'text')
            self.assertEqual(campo.widget.attrs['inputmode'], 'decimal')
            self.assertEqual(campo.decimal_places, 2)

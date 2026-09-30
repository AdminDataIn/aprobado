from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from contractors.forms import SolicitudPrestadorForm
from contractors.validators import (
    celular_colombiano, direccion_personal, documento_numerico,
    nombre_persona, normalizar_nit, validar_campos_personales,
)


class CalidadDatosPrestadorTest(SimpleTestCase):
    def test_documento_numerico_conserva_ceros_y_trim(self):
        for tipo in ('CC', 'CE'):
            self.assertEqual(documento_numerico(' 00123456 ', tipo), '00123456')

    def test_documento_rechaza_letras_separadores_y_longitud(self):
        for value in ('123a567', '1.234.567', '123-4567', '12345', '1' * 13, '１２３４５６'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                documento_numerico(value)

    def test_celular_normaliza_sin_descartar_letras(self):
        self.assertEqual(celular_colombiano(' (300) 123-4567 '), '3001234567')
        for value in ('300abc1234567', '+573001234567', '123', '1' * 11):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                celular_colombiano(value)

    def test_nombres_reales_sin_heuristica_linguistica(self):
        for value in ('Ana Maria', 'Mar\u00eda Jos\u00e9', 'Mu\u00f1oz de la Cruz', "O'Neill", 'Jean-Luc', 'Ng', 'Rhys'):
            self.assertEqual(nombre_persona(value), value)
        self.assertEqual(nombre_persona('  Ana   Maria '), 'Ana Maria')

    def test_nombres_invalidos_y_placeholders(self):
        for value in ('Ana1', '@Ana', ' ', 'Prueba', 'TEST', 'xxx', 'asdf', '---'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                nombre_persona(value)

    def test_direccion_urbana_y_rural(self):
        for value in ('Calle 1 # 2-3', 'Vereda La Esperanza', 'Finca El Porvenir'):
            self.assertEqual(direccion_personal(value), value)
        for value in ('#######', '1234567', 'test', 'asdf', 'cl', 'sin direccion'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                direccion_personal(value)

    def test_nit_estructura_y_dv(self):
        self.assertEqual(normalizar_nit(' 900.123.456-8 '), '900123456-8')
        self.assertEqual(normalizar_nit('900123456'), '900123456')
        for value in ('900123456-7', '900a123456', '90.01234.56', '900123456--8'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalizar_nit(value)

    def test_formulario_valida_sin_javascript(self):
        from django import forms

        class Formulario(forms.Form):
            nombres = forms.CharField()
            celular = forms.CharField()

            def clean(self):
                datos = super().clean()
                validar_campos_personales(self, datos)
                return datos

        form = Formulario({'nombres': 'Ana1', 'celular': '(300) 123-4567'})
        self.assertFalse(form.is_valid())
        self.assertIn('nombres', form.errors)
        self.assertEqual(form.cleaned_data['celular'], '3001234567')

    def test_widgets_moviles(self):
        campos = SolicitudPrestadorForm.base_fields
        self.assertEqual(campos['numero_documento'].widget.attrs['inputmode'], 'numeric')
        self.assertEqual(campos['celular'].widget.attrs['inputmode'], 'tel')

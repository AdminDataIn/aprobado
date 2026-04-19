from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from gestion_creditos.forms import CreditoLibranzaForm


class LibranzaLimitsTests(SimpleTestCase):
    def test_valor_credito_acepta_tope_de_tres_millones(self):
        form = CreditoLibranzaForm()
        form.cleaned_data = {'valor_credito': '3000000'}

        self.assertEqual(form.clean_valor_credito(), Decimal('3000000'))

    def test_valor_credito_rechaza_valores_superiores_al_tope(self):
        form = CreditoLibranzaForm()
        form.cleaned_data = {'valor_credito': '3000001'}

        with self.assertRaises(ValidationError) as ctx:
            form.clean_valor_credito()

        self.assertIn('$3.000.000', str(ctx.exception))

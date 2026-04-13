from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos.forms import InvestorInviteForm
from gestion_creditos.services.name_normalization import build_full_name_upper, normalize_name_upper


User = get_user_model()


class NameNormalizationTest(TestCase):
    def test_normalize_name_upper_colapsa_espacios_y_mayusculiza(self):
        self.assertEqual(normalize_name_upper('  Juan   Pérez '), 'JUAN PÉREZ')
        self.assertEqual(build_full_name_upper(' Ana ', '  maria  '), 'ANA MARIA')

    def test_investor_invite_form_guarda_nombres_en_mayuscula(self):
        form = InvestorInviteForm(
            data={
                'email': 'investor-normalize@aprobado.test',
                'first_name': 'María Elena',
                'last_name': 'de la Hoz',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save_user()

        user.refresh_from_db()
        self.assertEqual(user.email, 'investor-normalize@aprobado.test')
        self.assertEqual(user.first_name, 'MARÍA ELENA')
        self.assertEqual(user.last_name, 'DE LA HOZ')

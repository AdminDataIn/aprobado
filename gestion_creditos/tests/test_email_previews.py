from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from gestion_creditos.services.email_catalog import build_email_inventory


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class EmailPreviewCommandTests(TestCase):
    def test_inventory_contains_multiple_categories(self):
        inventory = build_email_inventory()

        self.assertGreaterEqual(len(inventory), 20)
        categorias = {item['categoria'] for item in inventory}
        self.assertTrue({'usuarios', 'pagadores', 'internos', 'ejecutivos', 'marketplace'}.issubset(categorias))

    def test_preview_command_sends_selected_previews(self):
        call_command(
            'preview_emails',
            '--to', 'medios.datain@gmail.com',
            '--only', 'pagador_resumen_mensual', 'marketplace_welcome',
        )

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('[Preview] Preview | Resumen mensual de obligaciones', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['medios.datain@gmail.com'])

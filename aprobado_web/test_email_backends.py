from unittest.mock import patch

from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from aprobado_web.email_backends import SafeRoutingEmailBackend


class SafeRoutingEmailBackendTests(SimpleTestCase):
    @override_settings(
        EMAIL_QA_MODE=True,
        EMAIL_QA_REDIRECT_TO='qa@aprobado.com.co',
        EMAIL_QA_SUBJECT_PREFIX='[QA]',
    )
    @patch('django.core.mail.backends.smtp.EmailBackend.send_messages')
    def test_reroutes_all_messages_to_single_qa_recipient(self, parent_send_messages):
        captured = {}

        def _capture(messages):
            captured['messages'] = messages
            return len(messages)

        parent_send_messages.side_effect = _capture

        message = EmailMultiAlternatives(
            subject='Prueba controlada',
            body='Contenido',
            from_email='Aprobado <noreply@aprobado.com.co>',
            to=['cliente@real.com'],
            cc=['cc@real.com'],
            bcc=['bcc@real.com'],
        )

        backend = SafeRoutingEmailBackend()
        enviados = backend.send_messages([message])

        self.assertEqual(enviados, 1)
        rerouted = captured['messages'][0]
        self.assertEqual(rerouted.to, ['qa@aprobado.com.co'])
        self.assertEqual(rerouted.cc, [])
        self.assertEqual(rerouted.bcc, [])
        self.assertTrue(rerouted.subject.startswith('[QA] '))
        self.assertIn('cliente@real.com', rerouted.extra_headers['X-Aprobado-QA-Original-Recipients'])
        self.assertIn('cc@real.com', rerouted.extra_headers['X-Aprobado-QA-Original-Recipients'])
        self.assertIn('bcc@real.com', rerouted.extra_headers['X-Aprobado-QA-Original-Recipients'])

    @override_settings(
        EMAIL_QA_MODE=False,
        EMAIL_QA_REDIRECT_TO='qa@aprobado.com.co',
    )
    @patch('django.core.mail.backends.smtp.EmailBackend.send_messages')
    def test_keeps_original_recipients_when_qa_mode_is_off(self, parent_send_messages):
        backend = SafeRoutingEmailBackend()
        message = EmailMultiAlternatives(
            subject='Sin QA',
            body='Contenido',
            from_email='Aprobado <noreply@aprobado.com.co>',
            to=['cliente@real.com'],
        )

        backend.send_messages([message])

        forwarded = parent_send_messages.call_args[0][0][0]
        self.assertEqual(forwarded.to, ['cliente@real.com'])

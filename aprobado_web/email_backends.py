from copy import copy

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend


class SafeRoutingEmailBackend(SMTPEmailBackend):
    """
    SMTP backend with an optional QA guardrail.

    When EMAIL_QA_MODE=True and EMAIL_QA_REDIRECT_TO is configured, every
    outgoing message is rerouted to that single address while preserving the
    original recipients in headers. This keeps controlled server tests from
    leaking mail to real users.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        qa_enabled = getattr(settings, 'EMAIL_QA_MODE', False)
        qa_recipient = (getattr(settings, 'EMAIL_QA_REDIRECT_TO', '') or '').strip()

        if not qa_enabled or not qa_recipient:
            return super().send_messages(email_messages)

        rerouted_messages = []
        subject_prefix = (getattr(settings, 'EMAIL_QA_SUBJECT_PREFIX', '[QA]') or '[QA]').strip()

        for original_message in email_messages:
            message = copy(original_message)
            original_to = list(getattr(original_message, 'to', []) or [])
            original_cc = list(getattr(original_message, 'cc', []) or [])
            original_bcc = list(getattr(original_message, 'bcc', []) or [])
            original_recipients = [addr for addr in (original_to + original_cc + original_bcc) if addr]

            message.to = [qa_recipient]
            message.cc = []
            message.bcc = []
            message.extra_headers = dict(getattr(original_message, 'extra_headers', {}) or {})
            if original_recipients:
                message.extra_headers['X-Aprobado-QA-Original-Recipients'] = ', '.join(original_recipients)

            if subject_prefix and not str(message.subject).startswith(subject_prefix):
                message.subject = f'{subject_prefix} {message.subject}'

            rerouted_messages.append(message)

        return super().send_messages(rerouted_messages)

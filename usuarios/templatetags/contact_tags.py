from django import template
from django.conf import settings


register = template.Library()


def _get_contact_email():
    return (
        getattr(settings, 'CONTACT_EMAIL', '')
        or 'Info@aprobado.com.co'
    )


@register.simple_tag
def contact_email():
    return _get_contact_email()


@register.simple_tag
def contact_mailto():
    return f"mailto:{_get_contact_email()}"

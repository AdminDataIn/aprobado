import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import InvestorAccessToken


logger = logging.getLogger(__name__)


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _build_investor_url(raw_token, route_name):
    host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{host}{reverse(route_name, kwargs={'token': raw_token})}"


def invalidar_tokens_inversionista(usuario, tipo=InvestorAccessToken.TipoToken.ACTIVACION):
    return InvestorAccessToken.objects.filter(
        usuario=usuario,
        tipo=tipo,
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def crear_token_inversionista(usuario, tipo=InvestorAccessToken.TipoToken.ACTIVACION, created_by=None):
    raw_token = secrets.token_urlsafe(32)
    expiration_setting = 'INVESTOR_ACTIVATION_EXPIRATION_HOURS'
    default_hours = 24
    if tipo == InvestorAccessToken.TipoToken.RESET_PASSWORD:
        expiration_setting = 'INVESTOR_RESET_EXPIRATION_HOURS'
        default_hours = 2
    expiracion_horas = int(getattr(settings, expiration_setting, default_hours) or default_hours)
    invalidar_tokens_inversionista(usuario, tipo=tipo)
    token = InvestorAccessToken.objects.create(
        usuario=usuario,
        tipo=tipo,
        token_hash=_hash_token(raw_token),
        token_hint=raw_token[:10],
        email_destino=usuario.email,
        expires_at=timezone.now() + timedelta(hours=expiracion_horas),
        created_by=created_by,
    )
    return token, raw_token


def buscar_token_inversionista(raw_token, tipo=InvestorAccessToken.TipoToken.ACTIVACION):
    try:
        return InvestorAccessToken.objects.select_related('usuario').get(
            token_hash=_hash_token(raw_token),
            tipo=tipo,
        )
    except InvestorAccessToken.DoesNotExist:
        return None


def marcar_token_inversionista_como_usado(token):
    token.used_at = timezone.now()
    token.invalidated_at = timezone.now()
    token.save(update_fields=['used_at', 'invalidated_at'])
    invalidar_tokens_inversionista(token.usuario, tipo=token.tipo)


def enviar_invitacion_inversionista(usuario, created_by=None):
    if not usuario.email:
        raise ValueError('El usuario inversionista no tiene correo configurado.')

    token, raw_token = crear_token_inversionista(usuario, InvestorAccessToken.TipoToken.ACTIVACION, created_by=created_by)
    activation_url = _build_investor_url(raw_token, 'inversionista:activar_cuenta')
    expiration_hours = int(getattr(settings, 'INVESTOR_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    context = {
        'usuario': usuario,
        'activation_url': activation_url,
        'expiration_hours': expiration_hours,
    }
    html_content = render_to_string('emails/investor_activation.html', context)
    text_content = render_to_string('emails/investor_activation.txt', context)
    email = EmailMultiAlternatives(
        subject='Activa tu acceso como inversionista - Aprobado',
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)
    logger.info('Invitacion inversionista enviada a %s', usuario.email)
    return token

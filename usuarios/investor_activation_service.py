import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core import signing
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import InvestorAccessToken


logger = logging.getLogger(__name__)
INVESTOR_TOKEN_SALT = 'investor-access-token'


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _serialize_investor_token(token):
    return signing.dumps(
        {
            'token_id': token.id,
            'tipo': token.tipo,
            'scope': 'investor',
        },
        salt=INVESTOR_TOKEN_SALT,
    )


def _build_investor_url(public_token, route_name):
    host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{host}{reverse(route_name, kwargs={'token': public_token})}"


def _get_active_investor_token(usuario, tipo):
    return (
        InvestorAccessToken.objects.filter(
            usuario=usuario,
            tipo=tipo,
            used_at__isnull=True,
            invalidated_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .select_related('usuario')
        .order_by('-created_at')
        .first()
    )


def invalidar_tokens_inversionista(usuario, tipo=InvestorAccessToken.TipoToken.ACTIVACION):
    return InvestorAccessToken.objects.filter(
        usuario=usuario,
        tipo=tipo,
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def crear_token_inversionista(usuario, tipo=InvestorAccessToken.TipoToken.ACTIVACION, created_by=None, force_new=False):
    expiration_setting = 'INVESTOR_ACTIVATION_EXPIRATION_HOURS'
    default_hours = 24
    if tipo == InvestorAccessToken.TipoToken.RESET_PASSWORD:
        expiration_setting = 'INVESTOR_RESET_EXPIRATION_HOURS'
        default_hours = 2
    expiracion_horas = int(getattr(settings, expiration_setting, default_hours) or default_hours)

    if not force_new:
        existing_token = _get_active_investor_token(usuario, tipo)
        if existing_token:
            return existing_token, _serialize_investor_token(existing_token)

    raw_token = secrets.token_urlsafe(32)
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
    return token, _serialize_investor_token(token)


def buscar_token_inversionista(raw_token, tipo=InvestorAccessToken.TipoToken.ACTIVACION):
    try:
        payload = signing.loads(raw_token, salt=INVESTOR_TOKEN_SALT)
        if payload.get('scope') != 'investor' or payload.get('tipo') != tipo:
            return None
        return InvestorAccessToken.objects.select_related('usuario').get(
            id=payload.get('token_id'),
            tipo=tipo,
        )
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        InvestorAccessToken.DoesNotExist,
        TypeError,
        ValueError,
    ):
        pass

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


def enviar_invitacion_inversionista(usuario, created_by=None, force_new=False):
    if not usuario.email:
        raise ValueError('El usuario inversionista no tiene correo configurado.')

    if usuario.last_login is None and not usuario.has_usable_password():
        update_fields = []
        if usuario.is_active:
            usuario.is_active = False
            update_fields.append('is_active')
        if not update_fields:
            update_fields = []
        usuario.set_unusable_password()
        update_fields.append('password')
        usuario.save(update_fields=list(dict.fromkeys(update_fields)))

    token, public_token = crear_token_inversionista(
        usuario,
        InvestorAccessToken.TipoToken.ACTIVACION,
        created_by=created_by,
        force_new=force_new,
    )
    activation_url = _build_investor_url(public_token, 'inversionista:activar_cuenta')
    expiration_hours = int(getattr(settings, 'INVESTOR_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    context = {
        'usuario': usuario,
        'display_name': usuario.first_name or usuario.get_full_name() or usuario.email,
        'activation_url': activation_url,
        'expiration_hours': expiration_hours,
        'expires_at': token.expires_at,
    }
    html_content = render_to_string('emails/inversionistas/investor_activation.html', context)
    text_content = render_to_string('emails/inversionistas/investor_activation.txt', context)
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

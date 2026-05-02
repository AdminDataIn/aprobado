import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.core import signing
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import PagadorAccessToken


logger = logging.getLogger(__name__)
User = get_user_model()
PAGADOR_TOKEN_SALT = 'pagador-access-token'


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _serialize_pagador_token(token):
    return signing.dumps(
        {
            'token_id': token.id,
            'tipo': token.tipo,
            'scope': 'pagador',
        },
        salt=PAGADOR_TOKEN_SALT,
    )


def _build_pagador_url(public_token, route_name):
    pagador_host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{pagador_host}{reverse(route_name, kwargs={'token': public_token})}"


def _get_active_pagador_token(usuario, tipo):
    return (
        PagadorAccessToken.objects.filter(
            usuario=usuario,
            tipo=tipo,
            used_at__isnull=True,
            invalidated_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .select_related('usuario', 'perfil_pagador__empresa')
        .order_by('-created_at')
        .first()
    )


def invalidar_tokens_pagador(usuario, tipo=PagadorAccessToken.TipoToken.ACTIVACION):
    return PagadorAccessToken.objects.filter(
        usuario=usuario,
        tipo=tipo,
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def crear_token_pagador(perfil_pagador, tipo=PagadorAccessToken.TipoToken.ACTIVACION, created_by=None, force_new=False):
    usuario = perfil_pagador.usuario
    expiration_setting = 'PAGADOR_ACTIVATION_EXPIRATION_HOURS'
    default_hours = 24
    if tipo == PagadorAccessToken.TipoToken.RESET_PASSWORD:
        expiration_setting = 'PAGADOR_RESET_EXPIRATION_HOURS'
        default_hours = 1
    expiracion_horas = int(getattr(settings, expiration_setting, default_hours) or default_hours)

    if not force_new:
        existing_token = _get_active_pagador_token(usuario, tipo)
        if existing_token:
            return existing_token, _serialize_pagador_token(existing_token)

    invalidar_tokens_pagador(usuario, tipo=tipo)
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)

    token = PagadorAccessToken.objects.create(
        usuario=usuario,
        perfil_pagador=perfil_pagador,
        tipo=tipo,
        token_hash=token_hash,
        token_hint=raw_token[:10],
        email_destino=usuario.email,
        expires_at=timezone.now() + timedelta(hours=expiracion_horas),
        created_by=created_by,
    )
    return token, _serialize_pagador_token(token)


def crear_token_activacion_pagador(perfil_pagador, created_by=None):
    return crear_token_pagador(
        perfil_pagador,
        tipo=PagadorAccessToken.TipoToken.ACTIVACION,
        created_by=created_by,
    )


def buscar_token_vigente(raw_token, tipo=PagadorAccessToken.TipoToken.ACTIVACION):
    try:
        payload = signing.loads(raw_token, salt=PAGADOR_TOKEN_SALT)
        if payload.get('scope') != 'pagador' or payload.get('tipo') != tipo:
            return None
        return PagadorAccessToken.objects.select_related('usuario', 'perfil_pagador__empresa').get(
            id=payload.get('token_id'),
            tipo=tipo,
        )
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        PagadorAccessToken.DoesNotExist,
        TypeError,
        ValueError,
    ):
        pass

    token_hash = _hash_token(raw_token)
    try:
        token = PagadorAccessToken.objects.select_related('usuario', 'perfil_pagador__empresa').get(
            token_hash=token_hash,
            tipo=tipo,
        )
    except PagadorAccessToken.DoesNotExist:
        return None

    if not token.esta_vigente:
        return token
    return token


def marcar_token_como_usado(token):
    token.used_at = timezone.now()
    token.invalidated_at = timezone.now()
    token.save(update_fields=['used_at', 'invalidated_at'])
    invalidar_tokens_pagador(token.usuario, tipo=token.tipo)


def enviar_invitacion_activacion_pagador(perfil_pagador, created_by=None, force_new=False):
    usuario = perfil_pagador.usuario
    if not usuario.email:
        raise ValueError('El usuario pagador no tiene correo electrónico configurado.')

    # Para cuentas nuevas sin uso previo, la activacion debe definir la primera
    # contrasena. Si el usuario ya usaba la cuenta, no lo bloqueamos de forma
    # retroactiva al reenviar un enlace.
    if usuario.last_login is None:
        usuario.is_active = False
        usuario.set_unusable_password()
        usuario.save(update_fields=['is_active', 'password'])

    token, public_token = crear_token_pagador(
        perfil_pagador,
        tipo=PagadorAccessToken.TipoToken.ACTIVACION,
        created_by=created_by,
        force_new=force_new,
    )
    activation_url = _build_pagador_url(public_token, 'pagador:activar_cuenta')
    expiracion_horas = int(getattr(settings, 'PAGADOR_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    context = {
        'perfil_pagador': perfil_pagador,
        'usuario': usuario,
        'empresa': perfil_pagador.empresa,
        'activation_url': activation_url,
        'expiration_hours': expiracion_horas,
        'expires_at': token.expires_at,
    }

    html_content = render_to_string('emails/pagadores/pagador_activacion_cuenta.html', context)
    text_content = render_to_string('emails/pagadores/pagador_activacion_cuenta.txt', context)
    email = EmailMultiAlternatives(
        subject=f"Activa tu acceso como pagador - {perfil_pagador.empresa.nombre}",
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send()

    logger.info(
        "Invitacion de activacion enviada a pagador %s (%s) para empresa %s",
        usuario.username,
        usuario.email,
        perfil_pagador.empresa.nombre,
    )
    return token


def obtener_perfil_pagador_por_identificador(identificador):
    identificador = (identificador or '').strip().lower()
    if not identificador:
        return None

    user = User.objects.filter(
        Q(email__iexact=identificador),
        perfil_pagador__isnull=False,
    ).select_related('perfil_pagador__empresa').first()

    if not user or not getattr(user, 'perfil_pagador', None):
        return None
    if not user.email:
        return None
    return user.perfil_pagador


def enviar_reset_password_pagador(perfil_pagador, created_by=None, force_new=False):
    usuario = perfil_pagador.usuario
    token, public_token = crear_token_pagador(
        perfil_pagador,
        tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD,
        created_by=created_by,
        force_new=force_new,
    )
    reset_url = _build_pagador_url(public_token, 'pagador:reset_password_confirm')
    expiracion_horas = int(getattr(settings, 'PAGADOR_RESET_EXPIRATION_HOURS', 1) or 1)

    context = {
        'perfil_pagador': perfil_pagador,
        'usuario': usuario,
        'empresa': perfil_pagador.empresa,
        'reset_url': reset_url,
        'expiration_hours': expiracion_horas,
        'expires_at': token.expires_at,
    }

    html_content = render_to_string('emails/pagadores/pagador_reset_password.html', context)
    text_content = render_to_string('emails/pagadores/pagador_reset_password.txt', context)
    email = EmailMultiAlternatives(
        subject=f"Restablece tu acceso como pagador - {perfil_pagador.empresa.nombre}",
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    email.attach_alternative(html_content, "text/html")
    email.send()

    logger.info(
        "Reset de acceso enviado a pagador %s (%s) para empresa %s",
        usuario.username,
        usuario.email,
        perfil_pagador.empresa.nombre,
    )
    return token

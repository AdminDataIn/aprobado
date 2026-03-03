import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import PagadorAccessToken


logger = logging.getLogger(__name__)
User = get_user_model()


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _build_pagador_url(raw_token, route_name):
    pagador_host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{pagador_host}{reverse(route_name, kwargs={'token': raw_token})}"


def invalidar_tokens_pagador(usuario, tipo=PagadorAccessToken.TipoToken.ACTIVACION):
    return PagadorAccessToken.objects.filter(
        usuario=usuario,
        tipo=tipo,
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def crear_token_pagador(perfil_pagador, tipo=PagadorAccessToken.TipoToken.ACTIVACION, created_by=None):
    usuario = perfil_pagador.usuario
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expiration_setting = 'PAGADOR_ACTIVATION_EXPIRATION_HOURS'
    default_hours = 24
    if tipo == PagadorAccessToken.TipoToken.RESET_PASSWORD:
        expiration_setting = 'PAGADOR_RESET_EXPIRATION_HOURS'
        default_hours = 1
    expiracion_horas = int(getattr(settings, expiration_setting, default_hours) or default_hours)

    invalidar_tokens_pagador(usuario, tipo=tipo)

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
    return token, raw_token


def crear_token_activacion_pagador(perfil_pagador, created_by=None):
    return crear_token_pagador(
        perfil_pagador,
        tipo=PagadorAccessToken.TipoToken.ACTIVACION,
        created_by=created_by,
    )


def buscar_token_vigente(raw_token, tipo=PagadorAccessToken.TipoToken.ACTIVACION):
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


def enviar_invitacion_activacion_pagador(perfil_pagador, created_by=None):
    usuario = perfil_pagador.usuario
    if not usuario.email:
        raise ValueError('El usuario pagador no tiene correo electronico configurado.')

    # Para cuentas nuevas sin uso previo, la activacion debe definir la primera
    # contrasena. Si el usuario ya usaba la cuenta, no lo bloqueamos de forma
    # retroactiva al reenviar un enlace.
    if usuario.last_login is None:
        usuario.is_active = False
        usuario.set_unusable_password()
        usuario.save(update_fields=['is_active', 'password'])

    token, raw_token = crear_token_activacion_pagador(perfil_pagador, created_by=created_by)
    activation_url = _build_pagador_url(raw_token, 'pagador:activar_cuenta')
    expiracion_horas = int(getattr(settings, 'PAGADOR_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    context = {
        'perfil_pagador': perfil_pagador,
        'usuario': usuario,
        'empresa': perfil_pagador.empresa,
        'activation_url': activation_url,
        'expiration_hours': expiracion_horas,
    }

    html_content = render_to_string('emails/pagador_activacion_cuenta.html', context)
    text_content = render_to_string('emails/pagador_activacion_cuenta.txt', context)
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
    identificador = (identificador or '').strip()
    if not identificador:
        return None

    user = User.objects.filter(
        Q(username__iexact=identificador) | Q(email__iexact=identificador),
        perfil_pagador__isnull=False,
    ).select_related('perfil_pagador__empresa').first()

    if not user or not getattr(user, 'perfil_pagador', None):
        return None
    if not user.email:
        return None
    return user.perfil_pagador


def enviar_reset_password_pagador(perfil_pagador, created_by=None):
    usuario = perfil_pagador.usuario
    token, raw_token = crear_token_pagador(
        perfil_pagador,
        tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD,
        created_by=created_by,
    )
    reset_url = _build_pagador_url(raw_token, 'pagador:reset_password_confirm')
    expiracion_horas = int(getattr(settings, 'PAGADOR_RESET_EXPIRATION_HOURS', 1) or 1)

    context = {
        'perfil_pagador': perfil_pagador,
        'usuario': usuario,
        'empresa': perfil_pagador.empresa,
        'reset_url': reset_url,
        'expiration_hours': expiracion_horas,
    }

    html_content = render_to_string('emails/pagador_reset_password.html', context)
    text_content = render_to_string('emails/pagador_reset_password.txt', context)
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

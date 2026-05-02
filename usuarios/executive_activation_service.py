import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import ExecutiveAccessToken


logger = logging.getLogger(__name__)
User = get_user_model()
EXECUTIVE_TOKEN_SALT = 'executive-access-token'


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _serialize_executive_token(token):
    return signing.dumps(
        {
            'token_id': token.id,
            'tipo': token.tipo,
            'scope': 'executive',
        },
        salt=EXECUTIVE_TOKEN_SALT,
    )


def _build_executive_url(public_token, route_name):
    host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{host}{reverse(route_name, kwargs={'token': public_token})}"


def _get_active_executive_token(usuario, tipo):
    return (
        ExecutiveAccessToken.objects.filter(
            usuario=usuario,
            tipo=tipo,
            used_at__isnull=True,
            invalidated_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .select_related('usuario', 'asesor')
        .order_by('-created_at')
        .first()
    )


def invalidar_tokens_ejecutivo(usuario, tipo=ExecutiveAccessToken.TipoToken.ACTIVACION):
    return ExecutiveAccessToken.objects.filter(
        usuario=usuario,
        tipo=tipo,
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def crear_token_ejecutivo(asesor, tipo=ExecutiveAccessToken.TipoToken.ACTIVACION, created_by=None, force_new=False):
    usuario = asesor.usuario
    expiracion_horas = int(getattr(settings, 'EXECUTIVE_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    if not usuario:
        raise ValueError('El ejecutivo no tiene usuario vinculado.')

    if not force_new:
        existing_token = _get_active_executive_token(usuario, tipo)
        if existing_token:
            return existing_token, _serialize_executive_token(existing_token)

    invalidar_tokens_ejecutivo(usuario, tipo=tipo)
    raw_token = secrets.token_urlsafe(32)
    token = ExecutiveAccessToken.objects.create(
        usuario=usuario,
        asesor=asesor,
        tipo=tipo,
        token_hash=_hash_token(raw_token),
        token_hint=raw_token[:10],
        email_destino=usuario.email,
        expires_at=timezone.now() + timedelta(hours=expiracion_horas),
        created_by=created_by,
    )
    return token, _serialize_executive_token(token)


def buscar_token_ejecutivo(raw_token, tipo=ExecutiveAccessToken.TipoToken.ACTIVACION):
    try:
        payload = signing.loads(raw_token, salt=EXECUTIVE_TOKEN_SALT)
        if payload.get('scope') != 'executive' or payload.get('tipo') != tipo:
            return None
        return ExecutiveAccessToken.objects.select_related('usuario', 'asesor').get(
            id=payload.get('token_id'),
            tipo=tipo,
        )
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        ExecutiveAccessToken.DoesNotExist,
        TypeError,
        ValueError,
    ):
        pass

    try:
        return ExecutiveAccessToken.objects.select_related('usuario', 'asesor').get(
            token_hash=_hash_token(raw_token),
            tipo=tipo,
        )
    except ExecutiveAccessToken.DoesNotExist:
        return None


def marcar_token_ejecutivo_como_usado(token):
    token.used_at = timezone.now()
    token.invalidated_at = timezone.now()
    token.save(update_fields=['used_at', 'invalidated_at'])
    invalidar_tokens_ejecutivo(token.usuario, tipo=token.tipo)


def ensure_executive_user(asesor):
    email = (asesor.email or '').strip().lower()
    if not email:
        raise ValueError('El ejecutivo debe tener correo para habilitar la activación.')

    usuario = asesor.usuario
    existing_user = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()

    if usuario and existing_user and existing_user.pk != usuario.pk:
        raise ValueError('Ya existe otro usuario con ese correo. Ajusta el correo del ejecutivo antes de continuar.')

    if usuario:
        if getattr(usuario, 'asesor_comercial', None) and usuario.asesor_comercial.pk != asesor.pk:
            raise ValueError('El usuario seleccionado ya está vinculado a otro ejecutivo.')
        old_email = (usuario.email or '').strip().lower()
        usuario.email = email
        if not usuario.username or usuario.username.lower() == old_email:
            usuario.username = email
        if not usuario.first_name:
            usuario.first_name = asesor.nombre
        usuario.is_active = usuario.is_active and usuario.has_usable_password()
        usuario.save(update_fields=['email', 'username', 'first_name', 'is_active'])
        if asesor.usuario_id != usuario.pk:
            asesor.usuario = usuario
            asesor.save(update_fields=['usuario'])
        return usuario, False

    if existing_user:
        if getattr(existing_user, 'asesor_comercial', None) and existing_user.asesor_comercial.pk != asesor.pk:
            raise ValueError('El correo ya pertenece a otro ejecutivo registrado.')
        asesor.usuario = existing_user
        asesor.save(update_fields=['usuario'])
        return existing_user, False

    usuario = User.objects.create(
        username=email,
        email=email,
        first_name=asesor.nombre,
        is_active=False,
    )
    usuario.set_unusable_password()
    usuario.save(update_fields=['password'])
    asesor.usuario = usuario
    asesor.save(update_fields=['usuario'])
    return usuario, True


def enviar_invitacion_activacion_ejecutivo(asesor, created_by=None, force_new=False):
    usuario, _ = ensure_executive_user(asesor)
    if not usuario.email:
        raise ValueError('El usuario del ejecutivo no tiene correo configurado.')

    if usuario.last_login is None:
        usuario.is_active = False
        usuario.set_unusable_password()
        usuario.save(update_fields=['is_active', 'password'])

    token, public_token = crear_token_ejecutivo(
        asesor,
        tipo=ExecutiveAccessToken.TipoToken.ACTIVACION,
        created_by=created_by,
        force_new=force_new,
    )
    activation_url = _build_executive_url(public_token, 'asesores:activar_cuenta')
    expiration_hours = int(getattr(settings, 'EXECUTIVE_ACTIVATION_EXPIRATION_HOURS', 24) or 24)

    context = {
        'asesor': asesor,
        'usuario': usuario,
        'activation_url': activation_url,
        'expiration_hours': expiration_hours,
        'expires_at': token.expires_at,
    }
    html_content = render_to_string('emails/ejecutivos/executive_activation.html', context)
    text_content = render_to_string('emails/ejecutivos/executive_activation.txt', context)
    email = EmailMultiAlternatives(
        subject='Activa tu acceso como ejecutivo - Aprobado',
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)
    logger.info('Invitacion ejecutivo enviada a %s para %s', usuario.email, asesor.nombre)
    return token

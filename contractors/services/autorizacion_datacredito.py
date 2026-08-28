import hashlib
import hmac
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from contractors.models import AutorizacionConsultaDatacreditoPrestador


@dataclass(frozen=True)
class ConfiguracionAutorizacionDatacredito:
    version_texto: str
    texto_hash: str

    @property
    def configurada(self):
        return bool(self.version_texto and self.texto_hash)


def obtener_configuracion_autorizacion_datacredito():
    version = str(
        getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION', '') or ''
    ).strip()
    texto = str(getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT', '') or '').strip()
    return ConfiguracionAutorizacionDatacredito(
        version_texto=version,
        texto_hash=hashlib.sha256(texto.encode('utf-8')).hexdigest() if texto else '',
    )


@transaction.atomic
def registrar_autorizacion_datacredito_desde_solicitud(solicitud, *, usuario, request=None):
    configuracion = obtener_configuracion_autorizacion_datacredito()
    if not configuracion.configurada or not solicitud.autoriza_consulta_centrales:
        return None
    if not getattr(usuario, 'is_authenticated', False) or solicitud.usuario_id != usuario.id:
        raise PermissionDenied('No puedes registrar autorización para otra solicitud.')

    autorizacion, _ = AutorizacionConsultaDatacreditoPrestador.objects.get_or_create(
        solicitud=solicitud,
        usuario=usuario,
        autorizada=True,
        version_texto=configuracion.version_texto,
        texto_hash=configuracion.texto_hash,
        defaults={
            'aceptada_en': timezone.now(),
            'ip_hash': _hash_ip(_resolver_ip(request)),
            'user_agent': _resolver_user_agent(request),
        },
    )
    return autorizacion


def obtener_autorizacion_datacredito_vigente(solicitud):
    configuracion = obtener_configuracion_autorizacion_datacredito()
    if (
        not configuracion.configurada
        or not solicitud.autoriza_consulta_centrales
        or not solicitud.usuario_id
    ):
        return None
    return (
        solicitud.autorizaciones_datacredito.filter(
            usuario_id=solicitud.usuario_id,
            autorizada=True,
            version_texto=configuracion.version_texto,
            texto_hash=configuracion.texto_hash,
        )
        .order_by('-aceptada_en', '-id')
        .first()
    )


def _resolver_ip(request):
    if request is None:
        return ''
    forwarded = str(request.META.get('HTTP_X_FORWARDED_FOR', '') or '')
    return (forwarded.split(',')[0] if forwarded else request.META.get('REMOTE_ADDR', '') or '').strip()


def _hash_ip(ip):
    if not ip:
        return ''
    secreto = f'contractors-datacredito-ip:{settings.SECRET_KEY}'.encode('utf-8')
    return hmac.new(secreto, ip.encode('utf-8'), hashlib.sha256).hexdigest()


def _resolver_user_agent(request):
    if request is None:
        return ''
    return str(request.META.get('HTTP_USER_AGENT', '') or '')[:255]

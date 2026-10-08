import hashlib
import hmac
from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors import consentimiento_centrales
from contractors.models import AutorizacionConsultaDatacreditoPrestador


@dataclass(frozen=True)
class ConfiguracionAutorizacionDatacredito:
    version_texto: str
    texto_hash: str
    texto: str
    texto_override_compatible: bool
    version_override_compatible: bool

    @property
    def configurada(self):
        return bool(
            self.version_texto and self.texto_hash
            and self.texto_override_compatible and self.version_override_compatible
        )


def obtener_configuracion_autorizacion_datacredito():
    # Environment values can confirm the code definition, never replace it.
    version = consentimiento_centrales.VERSION_CONSENTIMIENTO_CENTRALES
    texto = consentimiento_centrales.TEXTO_CONSENTIMIENTO_CENTRALES
    return ConfiguracionAutorizacionDatacredito(
        version_texto=version,
        texto_hash=hashlib.sha256(texto.encode('utf-8')).hexdigest() if texto else '',
        texto=texto,
        texto_override_compatible=getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT', texto) == texto,
        version_override_compatible=getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION', version) == version,
    )


def crear_confirmacion_consentimiento(usuario):
    configuracion = obtener_configuracion_autorizacion_datacredito()
    if not configuracion.configurada or not getattr(usuario, 'is_authenticated', False):
        return ''
    return signing.dumps({
        'usuario_id': usuario.pk, 'version': configuracion.version_texto,
        'texto_hash': configuracion.texto_hash,
    }, salt='contractors.consentimiento-centrales.v1')


def validar_confirmacion_consentimiento(confirmacion, usuario):
    configuracion = obtener_configuracion_autorizacion_datacredito()
    if not configuracion.configurada:
        raise ValidationError('La autorizacion de centrales no esta configurada. No puedes aceptarla todavia.')
    try:
        datos = signing.loads(confirmacion or '', salt='contractors.consentimiento-centrales.v1')
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise ValidationError('Debes revisar y aceptar la autorizacion vigente.') from exc
    if (not getattr(usuario, 'is_authenticated', False) or datos != {
        'usuario_id': usuario.pk, 'version': configuracion.version_texto,
        'texto_hash': configuracion.texto_hash,
    }):
        raise ValidationError('La autorizacion cambio. Revisa el texto vigente y acepta nuevamente.')


@transaction.atomic
def registrar_autorizacion_datacredito_desde_solicitud(solicitud, *, usuario, request=None):
    configuracion = obtener_configuracion_autorizacion_datacredito()
    if not configuracion.configurada:
        raise ValidationError('La autorizacion de centrales no esta configurada.')
    if not solicitud.autoriza_consulta_centrales:
        return None
    if not getattr(usuario, 'is_authenticated', False) or solicitud.usuario_id != usuario.id:
        raise PermissionDenied('No puedes registrar autorización para otra solicitud.')

    if request is not None:
        validar_confirmacion_consentimiento(request.POST.get('consentimiento_centrales'), usuario)
    # Serialize acceptance with ownership/authorization edits on the same request.
    solicitud = type(solicitud).objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.usuario_id != usuario.pk or not solicitud.autoriza_consulta_centrales:
        raise PermissionDenied('La solicitud no permite registrar esta autorizacion.')
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

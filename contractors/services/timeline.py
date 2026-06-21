from decimal import Decimal
import logging

from django.db import transaction

from contractors.models import TimelinePrestador


logger = logging.getLogger(__name__)


CLAVES_SENSIBLES_TIMELINE = (
    'raw',
    'respuesta_cruda',
    'prompt',
    'base64',
    'token',
    'access_token',
    'client_secret',
    'password',
    'api_key',
    'credencial',
    'credentials',
    'pdf',
    'archivo',
    'file',
    'contrato_completo',
    'html',
    'payload_sensible',
    'datacredito_raw',
)


def registrar_evento_timeline_prestador(
    *,
    solicitud=None,
    credito=None,
    tipo_evento,
    titulo,
    descripcion='',
    estado_resultante='',
    metadata=None,
    usuario=None,
    request=None,
    fail_silently=True,
):
    try:
        usuario_resuelto = _resolver_usuario(usuario=usuario, request=request)
        evento = TimelinePrestador(
            solicitud=solicitud,
            credito=credito,
            tipo_evento=tipo_evento,
            titulo=(titulo or '')[:160],
            descripcion=descripcion or '',
            estado_resultante=estado_resultante or '',
            metadata=_sanitizar_valor(metadata or {}),
            usuario=usuario_resuelto,
            request_id=_resolver_request_id(request),
            ip_address=_resolver_ip(request),
            user_agent=_resolver_user_agent(request),
        )
        evento.full_clean()
        with transaction.atomic():
            evento.save()
        return evento
    except Exception:
        logger.exception('No fue posible registrar timeline operativo de prestador.')
        if fail_silently:
            return None
        raise


def listar_timeline_por_solicitud(solicitud):
    if not solicitud:
        return TimelinePrestador.objects.none()
    return (
        TimelinePrestador.objects
        .select_related('solicitud', 'credito', 'usuario')
        .filter(solicitud=solicitud)
        .order_by('-created_at', '-id')
    )


def listar_timeline_por_credito(credito):
    if not credito:
        return TimelinePrestador.objects.none()
    return (
        TimelinePrestador.objects
        .select_related('solicitud', 'credito', 'usuario')
        .filter(credito=credito)
        .order_by('-created_at', '-id')
    )


def _sanitizar_valor(valor, clave=''):
    if _clave_sensible(clave):
        return '[redactado]'
    if isinstance(valor, dict):
        return {
            str(subclave): _sanitizar_valor(subvalor, str(subclave))
            for subclave, subvalor in valor.items()
            if not _clave_sensible(str(subclave))
        }
    if isinstance(valor, (list, tuple, set)):
        return [_sanitizar_valor(item, clave) for item in valor]
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, str):
        return _sanitizar_texto(valor, clave)
    return valor


def _clave_sensible(clave):
    clave = (clave or '').lower()
    return any(fragmento in clave for fragmento in CLAVES_SENSIBLES_TIMELINE)


def _sanitizar_texto(valor, clave):
    texto = str(valor)
    texto_bajo = texto.lower()
    if _clave_sensible(clave):
        return '[redactado]'
    if 'base64,' in texto_bajo or texto_bajo.startswith('data:application/pdf') or texto_bajo.startswith('data:image/'):
        return '[redactado]'
    if len(texto) > 3000:
        return '[redactado_por_longitud]'
    return texto


def _resolver_usuario(*, usuario, request):
    if usuario is not None:
        return usuario if getattr(usuario, 'is_authenticated', True) else None
    if request is None:
        return None
    usuario_request = getattr(request, 'user', None)
    if usuario_request is not None and getattr(usuario_request, 'is_authenticated', False):
        return usuario_request
    return None


def _resolver_request_id(request):
    if request is None:
        return ''
    return (
        request.META.get('HTTP_X_REQUEST_ID')
        or request.META.get('HTTP_X_CORRELATION_ID')
        or ''
    )[:120]


def _resolver_ip(request):
    if request is None:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _resolver_user_agent(request):
    if request is None:
        return ''
    return (request.META.get('HTTP_USER_AGENT') or '')[:255]

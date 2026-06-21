from decimal import Decimal, InvalidOperation

from contractors.models import PredecisionPrestadorAudit
from contractors.services.timeline import registrar_evento_timeline_prestador


CLAVES_SENSIBLES = (
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
)


def serializar_predecision_prestador(resultado):
    if hasattr(resultado, 'como_dict'):
        datos = resultado.como_dict()
    elif isinstance(resultado, dict):
        datos = dict(resultado)
    else:
        datos = {}
    return _sanitizar_valor(datos)


def crear_auditoria_predecision_prestador(solicitud, resultado, usuario=None, request=None, datacredito_contexto=None):
    snapshot = serializar_predecision_prestador(resultado)
    usuario = _resolver_usuario(usuario=usuario, request=request)
    request_id = _resolver_request_id(request)
    ip_address = _resolver_ip(request)
    user_agent = _resolver_user_agent(request)

    score_resultado = snapshot.get('score_resultado') or {}
    banda = score_resultado.get('banda') or {}
    datacredito = snapshot.get('datacredito_resultado') or {}
    datacredito_contexto = datacredito_contexto or None

    auditoria = PredecisionPrestadorAudit.objects.create(
        solicitud=solicitud,
        usuario=usuario,
        escenario_credito=snapshot.get('escenario_credito') or getattr(solicitud, 'escenario_credito', ''),
        decision=snapshot.get('decision') or '',
        eligible=bool(snapshot.get('eligible')),
        requiere_revision_manual=bool(snapshot.get('requiere_revision_manual')),
        monto_maximo_sugerido=_decimal(snapshot.get('monto_maximo_sugerido')),
        plazo_maximo_sugerido=int(snapshot.get('plazo_maximo_sugerido') or 0),
        score_status=snapshot.get('score_status') or '',
        score_final=_decimal_o_none(score_resultado.get('score_final')),
        score_banda=banda.get('nombre') or '',
        score_version_configuracion=score_resultado.get('version_configuracion') or '',
        datacredito_status=snapshot.get('datacredito_status') or datacredito.get('status') or '',
        datacredito_fuente=datacredito.get('fuente') or '',
        datacredito_mora_severa=bool(datacredito.get('mora_severa')),
        datacredito_mora_actual=bool(datacredito.get('mora_actual')),
        autorizacion_datacredito=getattr(datacredito_contexto, 'autorizacion_datacredito', None),
        snapshot_decisor=getattr(datacredito_contexto, 'snapshot_decisor', None),
        snapshot_historial=getattr(datacredito_contexto, 'snapshot_historial', None),
        datacredito_modo=getattr(datacredito_contexto, 'modo', '') or '',
        decisor_reutilizado=bool(getattr(datacredito_contexto, 'reutilizado_decisor', False)),
        historial_reutilizado=bool(getattr(datacredito_contexto, 'reutilizado_historial', False)),
        decisor_consultado=bool(getattr(datacredito_contexto, 'consultado_decisor', False)),
        historial_consultado=bool(getattr(datacredito_contexto, 'consultado_historial', False)),
        justificacion_consulta_forzada=getattr(datacredito_contexto, 'justificacion', '') or '',
        capacidad_status=snapshot.get('capacidad_status') or '',
        riesgo_status=snapshot.get('riesgo_status') or '',
        bloqueos=list(snapshot.get('bloqueos') or snapshot.get('blockers') or []),
        advertencias=list(snapshot.get('advertencias') or []),
        razones=list(snapshot.get('reasons') or snapshot.get('razones') or []),
        resultado_sanitizado=snapshot,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='AUDITORIA_PREDECISION_CREADA',
        titulo='Auditoría de predecisión creada',
        descripcion='Se guardó snapshot sanitizado de la predecisión formal.',
        estado_resultante=auditoria.decision,
        metadata={
            'auditoria_id': auditoria.id,
            'decision': auditoria.decision,
            'score_banda': auditoria.score_banda,
            'datacredito_status': auditoria.datacredito_status,
            'capacidad_status': auditoria.capacidad_status,
            'riesgo_status': auditoria.riesgo_status,
        },
        usuario=usuario,
        request=request,
    )
    return auditoria


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
    return any(fragmento in clave for fragmento in CLAVES_SENSIBLES)


def _sanitizar_texto(valor, clave):
    texto = str(valor)
    texto_bajo = texto.lower()
    if _clave_sensible(clave):
        return '[redactado]'
    if 'base64,' in texto_bajo or texto_bajo.startswith('data:application/pdf') or texto_bajo.startswith('data:image/'):
        return '[redactado]'
    if len(texto) > 5000:
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
    return request.META.get('HTTP_USER_AGENT', '')


def _decimal(valor):
    convertido = _decimal_o_none(valor)
    return convertido if convertido is not None else Decimal('0.00')


def _decimal_o_none(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None

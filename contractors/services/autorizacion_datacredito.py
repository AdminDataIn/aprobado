import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from contractors.models import AutorizacionConsultaDatacreditoPrestador
from contractors.services.timeline import registrar_evento_timeline_prestador
from integrations.datacredito.settings import obtener_configuracion_datacredito


FINALIDAD_CONSULTA_DATACREDITO_PRESTADOR = (
    'Consulta de informacion financiera y crediticia ante centrales de riesgo '
    'para evaluacion read-only de prestadores de servicios.'
)


class ErrorAutorizacionDatacredito(ValueError):
    pass


@dataclass(frozen=True)
class EstadoAutorizacionDatacreditoPrestador:
    estado: str
    autorizacion: AutorizacionConsultaDatacreditoPrestador | None = None
    razon: str = ''

    @property
    def vigente(self):
        return self.estado == 'VIGENTE' and self.autorizacion is not None


def obtener_autorizacion_datacredito_vigente(solicitud):
    configuracion = obtener_configuracion_texto_autorizacion()
    if not configuracion.configurada:
        return EstadoAutorizacionDatacreditoPrestador(
            estado='FALTANTE',
            razon='texto_autorizacion_datacredito_no_configurado',
        )

    if solicitud is None or not getattr(solicitud, 'usuario_id', None):
        return EstadoAutorizacionDatacreditoPrestador(
            estado='FALTANTE',
            razon='solicitud_sin_usuario_titular',
        )

    autorizacion = (
        AutorizacionConsultaDatacreditoPrestador.objects
        .filter(
            solicitud=solicitud,
            usuario_id=solicitud.usuario_id,
            autorizado=True,
            version_texto=configuracion.version,
            texto_hash=configuracion.texto_hash,
            revoked_at__isnull=True,
            accepted_at__lte=timezone.now(),
        )
        .order_by('-accepted_at', '-id')
        .first()
    )
    if autorizacion:
        return EstadoAutorizacionDatacreditoPrestador(
            estado='VIGENTE',
            autorizacion=autorizacion,
        )

    existente = (
        AutorizacionConsultaDatacreditoPrestador.objects
        .filter(solicitud=solicitud, usuario_id=solicitud.usuario_id, autorizado=True)
        .order_by('-accepted_at', '-id')
        .first()
    )
    if existente and existente.revoked_at:
        return EstadoAutorizacionDatacreditoPrestador(
            estado='REVOCADA',
            autorizacion=existente,
            razon='autorizacion_datacredito_revocada',
        )
    if existente and existente.version_texto != configuracion.version:
        return EstadoAutorizacionDatacreditoPrestador(
            estado='VERSION_NO_VIGENTE',
            autorizacion=existente,
            razon='version_autorizacion_datacredito_no_vigente',
        )
    if existente and existente.texto_hash != configuracion.texto_hash:
        return EstadoAutorizacionDatacreditoPrestador(
            estado='HASH_INVALIDO',
            autorizacion=existente,
            razon='hash_autorizacion_datacredito_no_coincide',
        )
    return EstadoAutorizacionDatacreditoPrestador(
        estado='FALTANTE',
        razon='autorizacion_datacredito_no_encontrada',
    )


def solicitud_tiene_autorizacion_datacredito(solicitud):
    return obtener_autorizacion_datacredito_vigente(solicitud).vigente


def registrar_autorizacion_datacredito_prestador(
    *,
    solicitud,
    usuario,
    source,
    request=None,
    justificacion='',
):
    configuracion = obtener_configuracion_texto_autorizacion()
    if not configuracion.configurada:
        raise ErrorAutorizacionDatacredito('texto_autorizacion_datacredito_no_configurado')
    if solicitud is None:
        raise ErrorAutorizacionDatacredito('solicitud_requerida')
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        raise PermissionDenied

    source = str(source or '').strip().upper()
    if source == AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO:
        if solicitud.usuario_id != usuario.id:
            raise PermissionDenied
    elif source == AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT:
        _validar_staff_uat(solicitud=solicitud, usuario=usuario, justificacion=justificacion)
    elif source != AutorizacionConsultaDatacreditoPrestador.Fuente.MIGRACION_CONTROLADA:
        raise ErrorAutorizacionDatacredito('fuente_autorizacion_datacredito_no_valida')

    autorizacion = AutorizacionConsultaDatacreditoPrestador.objects.create(
        solicitud=solicitud,
        usuario=solicitud.usuario,
        autorizado=True,
        version_texto=configuracion.version,
        texto_hash=configuracion.texto_hash,
        finalidad=FINALIDAD_CONSULTA_DATACREDITO_PRESTADOR,
        accepted_at=timezone.now(),
        ip_address=_resolver_ip(request),
        user_agent=_resolver_user_agent(request),
        source=source,
        justificacion=(justificacion or '')[:500],
    )

    tipo_evento = (
        'DATACREDITO_AUTORIZACION_UAT_REGISTRADA'
        if source == AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT
        else 'DATACREDITO_AUTORIZACION_ACEPTADA'
    )
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=tipo_evento,
        titulo='Autorizacion DataCredito registrada',
        descripcion='Se registro evidencia versionada para consulta DataCredito.',
        estado_resultante='VIGENTE',
        metadata=metadata_autorizacion_datacredito(autorizacion),
        usuario=usuario,
        request=request,
    )
    return autorizacion


def revocar_autorizacion_datacredito_prestador(autorizacion, usuario=None, request=None):
    if autorizacion.revoked_at:
        return autorizacion
    autorizacion.revoked_at = timezone.now()
    autorizacion.save(update_fields=['revoked_at'])
    registrar_evento_timeline_prestador(
        solicitud=autorizacion.solicitud,
        tipo_evento='DATACREDITO_AUTORIZACION_REVOCADA',
        titulo='Autorizacion DataCredito revocada',
        descripcion='Se registro revocacion de autorizacion DataCredito.',
        estado_resultante='REVOCADA',
        metadata=metadata_autorizacion_datacredito(autorizacion),
        usuario=usuario,
        request=request,
    )
    return autorizacion


def metadata_autorizacion_datacredito(autorizacion):
    if not autorizacion:
        return {}
    return {
        'autorizacion_id': autorizacion.id,
        'version_texto': autorizacion.version_texto,
        'texto_hash': autorizacion.texto_hash,
        'source': autorizacion.source,
        'estado': 'REVOCADA' if autorizacion.revoked_at else 'VIGENTE',
    }


def snapshot_tiene_autorizacion_valida(snapshot, autorizacion):
    if not snapshot or not autorizacion:
        return False
    return (
        str(snapshot.autorizacion_id or '') == str(autorizacion.id)
        and snapshot.autorizacion_version_texto == autorizacion.version_texto
        and snapshot.autorizacion_texto_hash == autorizacion.texto_hash
    )


def puede_reutilizar_snapshot_sin_autorizacion_en_uat():
    configuracion = obtener_configuracion_datacredito()
    return (
        configuracion.environment != 'prod'
        and bool(getattr(settings, 'DATACREDITO_ALLOW_LEGACY_SNAPSHOT_WITHOUT_AUTH_UAT', False))
    )


@dataclass(frozen=True)
class ConfiguracionTextoAutorizacionDatacredito:
    version: str
    texto: str
    texto_hash: str

    @property
    def configurada(self):
        return bool(self.version and self.texto and self.texto_hash)


def obtener_configuracion_texto_autorizacion():
    version = str(getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION', '') or '').strip()
    texto = str(getattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT', '') or '').strip()
    texto_hash = hashlib.sha256(texto.encode('utf-8')).hexdigest() if texto else ''
    return ConfiguracionTextoAutorizacionDatacredito(
        version=version,
        texto=texto,
        texto_hash=texto_hash,
    )


def _validar_staff_uat(*, solicitud, usuario, justificacion):
    configuracion = obtener_configuracion_datacredito()
    if configuracion.environment == 'prod':
        raise PermissionDenied
    if not getattr(usuario, 'is_staff', False):
        raise PermissionDenied
    if not usuario.has_perm('contractors.can_register_uat_datacredito_authorization'):
        raise PermissionDenied
    if not str(justificacion or '').strip():
        raise ValidationError('La autorizacion UAT requiere justificacion.')
    documentos = _documentos_demo_autorizados()
    documento = _normalizar_documento_demo(solicitud.document_number)
    if not documentos:
        _registrar_bloqueo_staff_uat(
            solicitud=solicitud,
            usuario=usuario,
            razon='allowlist_demo_no_configurada',
        )
        raise ValidationError('No hay documentos Demo autorizados configurados para pruebas UAT.')
    if documento not in documentos:
        _registrar_bloqueo_staff_uat(
            solicitud=solicitud,
            usuario=usuario,
            razon='documento_demo_no_autorizado',
        )
        raise ValidationError('El documento no esta autorizado para pruebas UAT DataCredito.')


def _documentos_demo_autorizados():
    valor = str(getattr(settings, 'DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS', '') or '')
    return {
        _normalizar_documento_demo(item)
        for item in valor.split(',')
        if _normalizar_documento_demo(item)
    }


def diagnosticar_configuracion_staff_uat_datacredito():
    configuracion = obtener_configuracion_datacredito()
    documentos = _documentos_demo_autorizados()
    hdc_server_ip_configurada = bool(
        str(getattr(settings, 'DATACREDITO_HDC_SERVER_IP_ADDRESS', '') or '').strip()
    )
    return {
        'hdc.server_ip_configurada': hdc_server_ip_configurada,
        'uat_demo_allowlist_configurada': bool(documentos),
        'uat_demo_documentos_configurados': len(documentos),
        'staff_uat_habilitable': bool(
            configuracion.environment != 'prod'
            and hdc_server_ip_configurada
            and documentos
            and obtener_configuracion_texto_autorizacion().configurada
        ),
    }


def _normalizar_documento_demo(valor):
    return ''.join(caracter for caracter in str(valor or '') if caracter.isalnum()).upper()


def _registrar_bloqueo_staff_uat(*, solicitud, usuario, razon):
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION',
        titulo='Autorizacion DataCredito UAT bloqueada',
        descripcion='Se bloqueo registro de autorizacion UAT por configuracion de documentos Demo.',
        estado_resultante='BLOQUEADA',
        metadata={
            'razon': razon,
            'source': AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
        },
        usuario=usuario,
        request=None,
    )


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
    return (request.META.get('HTTP_USER_AGENT', '') or '')[:255]

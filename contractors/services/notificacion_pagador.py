from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from contractors.models import ContractorApplication, NovedadPagadorPrestador
from contractors.services.timeline import registrar_evento_timeline_prestador
from gestion_creditos.models import Credito, Notificacion
from usuarios.models import PerfilPagador


PERMISO_NOTIFICAR_PAGADOR_PRESTADOR = 'contractors.can_notify_contractor_payer'


class ErrorNovedadPagadorPrestador(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoNovedadPagadorPrestador:
    novedad: NovedadPagadorPrestador
    creada: bool
    notificaciones_creadas: int
    destinatarios: list


def obtener_destinatarios_pagador_empresa(empresa):
    if not empresa:
        return []
    perfiles = (
        PerfilPagador.objects
        .select_related('usuario')
        .filter(empresa=empresa, es_pagador=True, usuario__is_active=True)
        .order_by('usuario__email', 'usuario__id')
    )
    destinatarios = []
    vistos = set()
    for perfil in perfiles:
        usuario = perfil.usuario
        clave = usuario.email or str(usuario.id)
        if clave in vistos:
            continue
        vistos.add(clave)
        destinatarios.append({
            'usuario_id': usuario.id,
            'email': usuario.email,
            'nombre': usuario.get_full_name() or usuario.username,
            'perfil_pagador_id': perfil.id,
        })
    return destinatarios


def construir_contexto_novedad_prestador(credito, solicitud):
    empresa = _resolver_empresa(credito=credito, solicitud=solicitud)
    prestador = f'{solicitud.first_name} {solicitud.last_name}'.strip()
    return {
        'tipo': NovedadPagadorPrestador.TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
        'titulo': 'Novedad informativa',
        'descripcion': 'Credito de prestador originado en revision',
        'mensaje_operativo': 'No requiere aprobacion del pagador',
        'prestador': prestador,
        'documento_enmascarado': _enmascarar_documento(solicitud.document_number),
        'empresa': empresa.nombre if empresa else '',
        'estado_credito': credito.estado,
        'numero_credito': credito.numero_credito,
        'monto_originado': str(credito.monto_aprobado or credito.monto_solicitado),
        'plazo': credito.plazo or credito.plazo_solicitado,
        'fecha': timezone.now().isoformat(),
    }


def registrar_novedad_pagador_prestador(credito, solicitud, usuario=None, request=None):
    usuario = _resolver_usuario(usuario=usuario, request=request)
    _validar_permiso(usuario)
    _validar_credito_solicitud(credito, solicitud)
    empresa = _resolver_empresa(credito=credito, solicitud=solicitud)
    if not empresa:
        raise ErrorNovedadPagadorPrestador('empresa_no_encontrada')

    destinatarios = obtener_destinatarios_pagador_empresa(empresa)
    metadata = construir_contexto_novedad_prestador(credito, solicitud)
    if not destinatarios:
        metadata['advertencias'] = ['sin_destinatarios_pagador']

    try:
        with transaction.atomic():
            novedad, creada = NovedadPagadorPrestador.objects.select_for_update().get_or_create(
                credito=credito,
                tipo=NovedadPagadorPrestador.TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
                defaults={
                    'solicitud': solicitud,
                    'empresa': empresa,
                    'estado': NovedadPagadorPrestador.Estado.REGISTRADA,
                    'destinatarios': destinatarios,
                    'metadata': metadata,
                    'created_by': usuario,
                    'request_id': _resolver_request_id(request),
                },
            )
    except IntegrityError:
        novedad = NovedadPagadorPrestador.objects.get(
            credito=credito,
            tipo=NovedadPagadorPrestador.TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
        )
        creada = False

    if creada:
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            credito=credito,
            tipo_evento='NOVEDAD_PAGADOR_REGISTRADA',
            titulo='Novedad al pagador registrada',
            descripcion='Se registró novedad informativa para pagador. No requiere aprobación.',
            estado_resultante=novedad.estado,
            metadata={
                'novedad_id': novedad.id,
                'tipo': novedad.tipo,
                'estado': novedad.estado,
                'destinatarios_count': len(novedad.destinatarios or destinatarios),
                'empresa_id': empresa.id,
            },
            usuario=usuario,
            request=request,
        )

    return ResultadoNovedadPagadorPrestador(
        novedad=novedad,
        creada=creada,
        notificaciones_creadas=0,
        destinatarios=list(novedad.destinatarios or destinatarios),
    )


def notificar_pagador_credito_prestador_en_revision(credito, solicitud, usuario=None, request=None):
    resultado = registrar_novedad_pagador_prestador(
        credito,
        solicitud,
        usuario=usuario,
        request=request,
    )
    if not resultado.creada:
        return resultado

    notificaciones_creadas = 0
    url = reverse('pagador:dashboard')
    for destinatario in resultado.destinatarios:
        usuario_id = destinatario.get('usuario_id')
        if not usuario_id:
            continue
        Notificacion.objects.create(
            usuario_id=usuario_id,
            tipo=Notificacion.TipoNotificacion.SISTEMA,
            titulo='Novedad informativa de prestador',
            mensaje=(
                f"Credito de prestador originado en revision para "
                f"{resultado.novedad.metadata.get('prestador', 'prestador')}. "
                "No requiere aprobacion del pagador."
            ),
            url=url,
        )
        notificaciones_creadas += 1

    resultado.novedad.estado = (
        NovedadPagadorPrestador.Estado.ENVIADA
        if notificaciones_creadas
        else NovedadPagadorPrestador.Estado.REGISTRADA
    )
    resultado.novedad.sent_at = timezone.now() if notificaciones_creadas else None
    resultado.novedad.save(update_fields=['estado', 'sent_at'])

    return ResultadoNovedadPagadorPrestador(
        novedad=resultado.novedad,
        creada=resultado.creada,
        notificaciones_creadas=notificaciones_creadas,
        destinatarios=resultado.destinatarios,
    )


def puede_notificar_pagador_prestador(auditoria):
    if not auditoria or not auditoria.solicitud_id:
        return False
    solicitud = auditoria.solicitud
    credito = solicitud.credito
    if not credito:
        return False
    if solicitud.status != ContractorApplication.Estado.CONVERTIDA:
        return False
    if credito.estado != Credito.EstadoCredito.EN_REVISION:
        return False
    return not NovedadPagadorPrestador.objects.filter(
        credito=credito,
        tipo=NovedadPagadorPrestador.TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
    ).exists()


def obtener_novedad_pagador_prestador(credito):
    if not credito:
        return None
    return NovedadPagadorPrestador.objects.filter(
        credito=credito,
        tipo=NovedadPagadorPrestador.TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
    ).first()


def _resolver_usuario(*, usuario, request):
    if usuario is not None:
        return usuario if getattr(usuario, 'is_authenticated', True) else None
    if request is None:
        return None
    usuario_request = getattr(request, 'user', None)
    if usuario_request is not None and getattr(usuario_request, 'is_authenticated', False):
        return usuario_request
    return None


def _validar_permiso(usuario):
    if not getattr(usuario, 'is_authenticated', False) or not usuario.has_perm(PERMISO_NOTIFICAR_PAGADOR_PRESTADOR):
        raise PermissionDenied('No tiene permiso para notificar novedad al pagador.')


def _validar_credito_solicitud(credito, solicitud):
    if not credito or not solicitud:
        raise ErrorNovedadPagadorPrestador('credito_o_solicitud_no_encontrado')
    if solicitud.credito_id != credito.id:
        raise ErrorNovedadPagadorPrestador('credito_no_corresponde_a_solicitud')
    if solicitud.status != ContractorApplication.Estado.CONVERTIDA:
        raise ErrorNovedadPagadorPrestador('solicitud_no_convertida')
    if credito.estado != Credito.EstadoCredito.EN_REVISION:
        raise ErrorNovedadPagadorPrestador('credito_no_esta_en_revision')


def _resolver_empresa(*, credito, solicitud):
    detalle = getattr(credito, 'detalle_libranza', None)
    if detalle and detalle.empresa_id:
        return detalle.empresa
    try:
        datos = solicitud.informacion_laboral
    except AttributeError:
        return None
    return datos.empresa


def _resolver_request_id(request):
    if request is None:
        return ''
    return (
        request.META.get('HTTP_X_REQUEST_ID')
        or request.META.get('HTTP_X_CORRELATION_ID')
        or ''
    )[:120]


def _enmascarar_documento(documento):
    texto = str(documento or '').strip()
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"

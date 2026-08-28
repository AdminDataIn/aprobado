from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from contractors.models import (
    ContractorApplication,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador


ESTADOS_REVISION_ACTIVA = {
    RevisionManualPrestador.Estado.ABIERTA,
    RevisionManualPrestador.Estado.ASIGNADA,
    RevisionManualPrestador.Estado.EN_ANALISIS,
    RevisionManualPrestador.Estado.PENDIENTE_SOLICITANTE,
    RevisionManualPrestador.Estado.PENDIENTE_VALIDACION_EMPRESA,
}

MENSAJES_PUBLICOS_SUBSANACION = {
    RequerimientoSubsanacionPrestador.Tipo.NUEVO_CONTRATO: (
        'Necesitamos que registres un contrato vigente para continuar.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.ACTUALIZAR_CONTRATO: (
        'Necesitamos que actualices el contrato registrado para continuar.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_IDENTIDAD: (
        'Necesitamos que actualices uno o más documentos de identidad.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_CONTRACTUAL: (
        'Necesitamos que actualices el documento contractual.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.CERTIFICACION_BANCARIA: (
        'Necesitamos que actualices la certificación bancaria.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.INFORMACION_CONTRACTUAL: (
        'Necesitamos que corrijas información contractual de tu solicitud.'
    ),
    RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL: (
        'Necesitamos que corrijas información personal de tu solicitud.'
    ),
}

MOTIVOS_BLOQUEO_ACCIONABLES = {
    RevisionManualPrestador.Motivo.CONTRATO_VENCIDO,
    RevisionManualPrestador.Motivo.IDENTIDAD_INCONSISTENTE,
    RevisionManualPrestador.Motivo.CAPACIDAD_EXCEDIDA,
    RevisionManualPrestador.Motivo.DATOS_MODIFICADOS,
    RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE,
}


def crear_revisiones_para_auditoria(auditoria, *, usuario=None):
    if auditoria.resultado == PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY:
        return []

    motivos = _motivos_desde_auditoria(auditoria)
    if auditoria.resultado == PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY:
        motivos = [motivo for motivo in motivos if motivo in MOTIVOS_BLOQUEO_ACCIONABLES]
    if auditoria.resultado not in {
        PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
        PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
        PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
        PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
    }:
        return []

    return [
        crear_o_reutilizar_revision(
            solicitud=auditoria.solicitud,
            auditoria=auditoria,
            motivo=motivo,
            usuario=usuario,
            prioridad=_prioridad_para_motivo(motivo),
        )[0]
        for motivo in motivos
    ]


@transaction.atomic
def crear_o_reutilizar_revision(
    *, solicitud, auditoria, motivo, usuario=None,
    prioridad=RevisionManualPrestador.Prioridad.MEDIA,
):
    if motivo not in RevisionManualPrestador.Motivo.values:
        raise ValidationError('El motivo de revision no esta permitido.')
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud.pk)
    existente = RevisionManualPrestador.objects.filter(
        solicitud=solicitud,
        motivo=motivo,
        estado__in=ESTADOS_REVISION_ACTIVA,
    ).first()
    if existente:
        return existente, False

    try:
        with transaction.atomic():
            revision = RevisionManualPrestador.objects.create(
                solicitud=solicitud,
                auditoria_predecision=auditoria,
                motivo=motivo,
                prioridad=prioridad,
                creada_por=_usuario_autenticado(usuario),
            )
    except IntegrityError:
        revision = RevisionManualPrestador.objects.get(
            solicitud=solicitud,
            motivo=motivo,
            estado__in=ESTADOS_REVISION_ACTIVA,
        )
        return revision, False

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.REVISION_CREADA,
        titulo='Revision interna creada',
        descripcion='Se creo una revision interna para continuar el analisis.',
        metadata={
            'revision_id': revision.id,
            'tipo': revision.motivo,
            'estado': revision.estado,
            'actor_id': getattr(usuario, 'id', None),
        },
        usuario=usuario,
    )
    return revision, True


@transaction.atomic
def asignar_revision(revision, *, actor, asignado_a=None):
    _exigir_permiso(actor, 'contractors.can_assign_contractor_review')
    revision = _bloquear_revision_activa(revision.pk)
    asignado_a = asignado_a or actor
    if not asignado_a.is_staff or not asignado_a.has_perm(
        'contractors.can_view_contractor_review_queue'
    ) or _es_pagador(asignado_a):
        raise ValidationError('El usuario asignado no esta habilitado para esta bandeja.')
    revision.asignado_a = asignado_a
    revision.asignada_en = timezone.now()
    revision.estado = RevisionManualPrestador.Estado.ASIGNADA
    revision.save(update_fields=['asignado_a', 'asignada_en', 'estado', 'updated_at'])
    _timeline_revision(revision, TimelinePrestador.TipoEvento.REVISION_ASIGNADA, actor)
    return revision


@transaction.atomic
def iniciar_analisis_revision(revision, *, actor):
    _exigir_permiso(actor, 'contractors.can_resolve_contractor_review')
    revision = RevisionManualPrestador.objects.select_for_update().get(pk=revision.pk)
    if revision.estado not in {
        RevisionManualPrestador.Estado.ABIERTA,
        RevisionManualPrestador.Estado.ASIGNADA,
    }:
        raise ValidationError('La revision no esta disponible para iniciar analisis.')
    revision.estado = RevisionManualPrestador.Estado.EN_ANALISIS
    revision.save(update_fields=['estado', 'updated_at'])
    _timeline_revision(revision, TimelinePrestador.TipoEvento.REVISION_INICIADA, actor)
    return revision


@transaction.atomic
def solicitar_subsanacion(revision, *, tipo, actor, detalle_interno=''):
    _exigir_permiso(actor, 'contractors.can_request_contractor_correction')
    if tipo not in MENSAJES_PUBLICOS_SUBSANACION:
        raise ValidationError('El tipo de subsanacion no esta permitido.')
    revision = _bloquear_revision_activa(revision.pk)
    requerimiento = RequerimientoSubsanacionPrestador.objects.filter(
        solicitud=revision.solicitud,
        tipo=tipo,
        estado=RequerimientoSubsanacionPrestador.Estado.PENDIENTE,
    ).first()
    if requerimiento is None:
        requerimiento = RequerimientoSubsanacionPrestador.objects.create(
            solicitud=revision.solicitud,
            revision=revision,
            tipo=tipo,
            mensaje_publico=MENSAJES_PUBLICOS_SUBSANACION[tipo],
            detalle_interno=str(detalle_interno or '').strip(),
            creado_por=actor,
        )
    revision.estado = RevisionManualPrestador.Estado.PENDIENTE_SOLICITANTE
    revision.resultado = _resultado_para_subsanacion(tipo)
    revision.save(update_fields=['estado', 'resultado', 'updated_at'])
    revision.solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    revision.solicitud.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=revision.solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.SUBSANACION_SOLICITADA,
        titulo='Subsanacion solicitada',
        descripcion='Se solicito informacion adicional al prestador.',
        metadata={
            'revision_id': revision.id,
            'requerimiento_id': requerimiento.id,
            'tipo': tipo,
            'estado': requerimiento.estado,
            'actor_id': actor.id,
        },
        visible_cliente=True,
        usuario=actor,
    )
    return requerimiento


@transaction.atomic
def solicitar_validacion_empresa(revision, *, actor, comentario_interno=''):
    _exigir_permiso(actor, 'contractors.can_request_contractor_correction')
    revision = _bloquear_revision_activa(revision.pk)
    revision.estado = RevisionManualPrestador.Estado.PENDIENTE_VALIDACION_EMPRESA
    revision.resultado = RevisionManualPrestador.Resultado.SOLICITAR_VALIDACION_EMPRESA
    revision.comentario_interno = str(comentario_interno or '').strip()
    revision.save(update_fields=['estado', 'resultado', 'comentario_interno', 'updated_at'])
    revision.solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    revision.solicitud.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=revision.solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.VALIDACION_EMPRESA_SOLICITADA,
        titulo='Validacion contractual con empresa pendiente',
        descripcion='Aprobado debe validar hechos contractuales con la empresa.',
        metadata={
            'revision_id': revision.id,
            'tipo': RevisionManualPrestador.Resultado.SOLICITAR_VALIDACION_EMPRESA,
            'estado': revision.estado,
            'actor_id': actor.id,
        },
        visible_cliente=True,
        usuario=actor,
    )
    return revision


@transaction.atomic
def resolver_revision(revision, *, resultado, actor, comentario_interno):
    _exigir_permiso(actor, 'contractors.can_resolve_contractor_review')
    if resultado not in RevisionManualPrestador.Resultado.values:
        raise ValidationError('El resultado de revision no esta permitido.')
    comentario = str(comentario_interno or '').strip()
    if resultado in {
        RevisionManualPrestador.Resultado.MANTENER_BLOQUEO,
        RevisionManualPrestador.Resultado.CERRAR_SIN_CONTINUAR,
    } and not comentario:
        raise ValidationError('Debes registrar un comentario interno para cerrar la revision.')
    revision = _bloquear_revision_activa(revision.pk)
    revision.estado = RevisionManualPrestador.Estado.RESUELTA
    revision.resultado = resultado
    revision.comentario_interno = comentario
    revision.resuelta_por = actor
    revision.resuelta_en = timezone.now()
    revision.save(update_fields=[
        'estado', 'resultado', 'comentario_interno', 'resuelta_por', 'resuelta_en',
        'updated_at',
    ])
    _timeline_revision(revision, TimelinePrestador.TipoEvento.REVISION_RESUELTA, actor)
    return revision


@transaction.atomic
def cancelar_revision(revision, *, actor, comentario_interno):
    _exigir_permiso(actor, 'contractors.can_resolve_contractor_review')
    comentario = str(comentario_interno or '').strip()
    if not comentario:
        raise ValidationError('Debes registrar un comentario interno para cancelar la revision.')
    revision = _bloquear_revision_activa(revision.pk)
    revision.estado = RevisionManualPrestador.Estado.CANCELADA
    revision.comentario_interno = comentario
    revision.resuelta_por = actor
    revision.resuelta_en = timezone.now()
    revision.save(update_fields=[
        'estado', 'comentario_interno', 'resuelta_por', 'resuelta_en', 'updated_at',
    ])
    _timeline_revision(revision, TimelinePrestador.TipoEvento.REVISION_CANCELADA, actor)
    return revision


@transaction.atomic
def marcar_subsanacion_atendida(requerimiento, *, usuario):
    requerimiento = RequerimientoSubsanacionPrestador.objects.select_for_update().get(
        pk=requerimiento.pk,
        solicitud__usuario=usuario,
    )
    if requerimiento.estado != RequerimientoSubsanacionPrestador.Estado.PENDIENTE:
        raise ValidationError('El requerimiento ya fue atendido o cerrado.')
    requerimiento.estado = RequerimientoSubsanacionPrestador.Estado.ATENDIDO
    requerimiento.atendido_en = timezone.now()
    requerimiento.save(update_fields=['estado', 'atendido_en'])
    registrar_evento_timeline_prestador(
        solicitud=requerimiento.solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.SUBSANACION_ATENDIDA,
        titulo='Subsanacion atendida',
        descripcion='El prestador registro la informacion solicitada.',
        metadata={
            'revision_id': requerimiento.revision_id,
            'requerimiento_id': requerimiento.id,
            'tipo': requerimiento.tipo,
            'estado': requerimiento.estado,
            'actor_id': usuario.id,
        },
        visible_cliente=True,
        usuario=usuario,
    )
    return requerimiento


def reintentar_evaluacion(revision, *, actor):
    _exigir_permiso(actor, 'contractors.can_resolve_contractor_review')
    if revision.estado not in ESTADOS_REVISION_ACTIVA:
        raise ValidationError('La revision no esta activa.')
    if revision.requerimientos_subsanacion.filter(
        estado=RequerimientoSubsanacionPrestador.Estado.PENDIENTE
    ).exists():
        raise ValidationError('Aun existen requerimientos pendientes del solicitante.')

    from contractors.services.evaluacion_formal import evaluar_solicitud_prestador

    resultado = evaluar_solicitud_prestador(
        revision.solicitud,
        solicitado_por=actor,
    )
    registrar_evento_timeline_prestador(
        solicitud=revision.solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_REINTENTADA,
        titulo='Evaluacion reintentada',
        descripcion='Se reintento la evaluacion con los datos vigentes.',
        metadata={
            'revision_id': revision.id,
            'auditoria_id': resultado.auditoria.id,
            'resultado': resultado.auditoria.resultado,
            'actor_id': actor.id,
        },
        usuario=actor,
    )
    if resultado.auditoria.resultado == PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY:
        revision.refresh_from_db()
        if revision.estado in ESTADOS_REVISION_ACTIVA:
            resolver_revision(
                revision,
                resultado=RevisionManualPrestador.Resultado.CONTINUAR_EVALUACION,
                actor=actor,
                comentario_interno='Evaluacion reintentada con resultado favorable read-only.',
            )
    return resultado


def usuarios_asignables_revision():
    permiso = 'contractors.can_view_contractor_review_queue'
    return [
        usuario for usuario in get_user_model().objects.filter(is_active=True, is_staff=True)
        if usuario.has_perm(permiso) and not _es_pagador(usuario)
    ]


def _motivos_desde_auditoria(auditoria):
    textos = ' '.join([
        *[str(item) for item in auditoria.razones or []],
        *[str(item) for item in auditoria.alertas or []],
        *[str(item) for item in auditoria.bloqueos or []],
        str(auditoria.error_codigo or ''),
        str(auditoria.error_etapa or ''),
    ]).lower()
    reglas = (
        (('documentos minimos', 'documentos incompletos'), RevisionManualPrestador.Motivo.DOCUMENTOS_INCOMPLETOS),
        (('documento_contrato_no_coincide', 'identidad'), RevisionManualPrestador.Motivo.IDENTIDAD_INCONSISTENTE),
        (('contrato:suspendido',), RevisionManualPrestador.Motivo.CONTRATO_SUSPENDIDO),
        (('fecha_fin_no_determinable', 'contrato no determinable'), RevisionManualPrestador.Motivo.CONTRATO_NO_DETERMINABLE),
        (('contrato:vencido', 'contrato:terminado', 'contrato:liquidado'), RevisionManualPrestador.Motivo.CONTRATO_VENCIDO),
        (('relacion_cuota_ingreso', 'capacidad excedida'), RevisionManualPrestador.Motivo.CAPACIDAD_EXCEDIDA),
        (('datos_modificados', 'datos cambiaron'), RevisionManualPrestador.Motivo.DATOS_MODIFICADOS),
        (('politica', 'simulacion no conserva', 'configuracion financiera'), RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE),
        (('solicitar_validacion_empresa', 'validacion empresa'), RevisionManualPrestador.Motivo.VALIDACION_EMPRESA_REQUERIDA),
        (('datacredito no esta disponible', 'autorizacion datacredito', 'snapshot datacredito'), RevisionManualPrestador.Motivo.DATACREDITO_NO_DISPONIBLE),
    )
    motivos = [motivo for patrones, motivo in reglas if any(patron in textos for patron in patrones)]
    if 'capacidad' in textos and not any(
        motivo in motivos for motivo in {
            RevisionManualPrestador.Motivo.CAPACIDAD_EXCEDIDA,
            RevisionManualPrestador.Motivo.CAPACIDAD_NO_DETERMINABLE,
            RevisionManualPrestador.Motivo.CONTRATO_NO_DETERMINABLE,
            RevisionManualPrestador.Motivo.CONTRATO_SUSPENDIDO,
            RevisionManualPrestador.Motivo.CONTRATO_VENCIDO,
        }
    ):
        motivos.append(RevisionManualPrestador.Motivo.CAPACIDAD_NO_DETERMINABLE)
    if 'datacredito' in textos and not any(
        motivo in motivos for motivo in {
            RevisionManualPrestador.Motivo.DATACREDITO_NO_DISPONIBLE,
            RevisionManualPrestador.Motivo.DATACREDITO_ERROR,
        }
    ):
        motivos.append(RevisionManualPrestador.Motivo.DATACREDITO_ERROR)
    if not motivos:
        motivos = [RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA]
    return list(dict.fromkeys(motivos))


def _prioridad_para_motivo(motivo):
    if motivo == RevisionManualPrestador.Motivo.IDENTIDAD_INCONSISTENTE:
        return RevisionManualPrestador.Prioridad.CRITICA
    if motivo in {
        RevisionManualPrestador.Motivo.DATACREDITO_ERROR,
        RevisionManualPrestador.Motivo.CONTRATO_VENCIDO,
        RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE,
    }:
        return RevisionManualPrestador.Prioridad.ALTA
    return RevisionManualPrestador.Prioridad.MEDIA


def _resultado_para_subsanacion(tipo):
    if tipo == RequerimientoSubsanacionPrestador.Tipo.NUEVO_CONTRATO:
        return RevisionManualPrestador.Resultado.SOLICITAR_NUEVO_CONTRATO
    if tipo in {
        RequerimientoSubsanacionPrestador.Tipo.INFORMACION_CONTRACTUAL,
        RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL,
    }:
        return RevisionManualPrestador.Resultado.SOLICITAR_CORRECCION_INFORMACION
    return RevisionManualPrestador.Resultado.SOLICITAR_DOCUMENTO


def _bloquear_revision_activa(revision_id):
    revision = RevisionManualPrestador.objects.select_for_update().select_related(
        'solicitud'
    ).get(pk=revision_id)
    if revision.estado not in ESTADOS_REVISION_ACTIVA:
        raise ValidationError('La revision ya no esta activa.')
    return revision


def _timeline_revision(revision, tipo_evento, actor):
    registrar_evento_timeline_prestador(
        solicitud=revision.solicitud,
        tipo_evento=tipo_evento,
        titulo=TimelinePrestador.TipoEvento(tipo_evento).label,
        descripcion='Se actualizo la revision interna de la solicitud.',
        metadata={
            'revision_id': revision.id,
            'tipo': revision.motivo,
            'estado': revision.estado,
            'actor_id': actor.id,
        },
        usuario=actor,
    )


def _exigir_permiso(usuario, permiso):
    if _es_pagador(usuario):
        raise PermissionDenied(
            'Los perfiles pagadores no pueden ejecutar acciones de revision de prestadores.'
        )
    if (
        not getattr(usuario, 'is_authenticated', False)
        or not usuario.is_staff
        or not usuario.has_perm(permiso)
    ):
        raise PermissionDenied('No tienes permiso para realizar esta accion.')


def _es_pagador(usuario):
    return hasattr(usuario, 'perfil_pagador')


def _usuario_autenticado(usuario):
    return usuario if getattr(usuario, 'is_authenticated', False) else None

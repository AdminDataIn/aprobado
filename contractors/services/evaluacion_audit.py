from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import ContractorApplication, PredecisionPrestadorAudit, TimelinePrestador
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.evaluacion_versionado import (
    MODO_EVALUACION_BASE,
    VERSION_POLITICA_EVALUACION,
    VERSION_SCORE_NO_HABILITADO,
    construir_clave_idempotencia,
    construir_version_datos,
)


RAZON_SERVICIOS_NO_HABILITADOS = (
    'La evaluación externa y el score aún no están habilitados.'
)


@dataclass(frozen=True)
class ResultadoInicioEvaluacionPrestador:
    auditoria: PredecisionPrestadorAudit
    reutilizada: bool = False
    en_proceso: bool = False


ESTADOS_CON_EVALUACION = {
    ContractorApplication.Estado.EVALUACION_PENDIENTE,
    ContractorApplication.Estado.EN_EVALUACION,
    ContractorApplication.Estado.EVALUACION_COMPLETADA,
    ContractorApplication.Estado.EN_REVISION_MANUAL,
    ContractorApplication.Estado.PENDIENTE_APROBACION_PAGADOR,
    ContractorApplication.Estado.APROBADO_POR_PAGADOR,
    ContractorApplication.Estado.NO_APROBADO,
    ContractorApplication.Estado.PENDIENTE_FIRMA,
    ContractorApplication.Estado.FIRMADO,
}


def registrar_solicitud_creada(solicitud, usuario=None):
    if TimelinePrestador.objects.filter(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.SOLICITUD_REGISTRADA,
    ).exists():
        return None
    return registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.SOLICITUD_REGISTRADA,
        titulo='Solicitud registrada',
        descripcion='Se registraron los datos y documentos iniciales de la solicitud.',
        visible_cliente=True,
        usuario=usuario,
    )


def marcar_evaluacion_pendiente(solicitud, usuario=None, motivo='simulacion_registrada'):
    version_datos, _ = construir_version_datos(solicitud)
    ultimo_evento = solicitud.timeline_operativo.filter(
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_PENDIENTE,
    ).first()
    if (
        solicitud.estado == ContractorApplication.Estado.EVALUACION_PENDIENTE
        and ultimo_evento
        and ultimo_evento.metadata.get('version_datos') == version_datos
    ):
        return ultimo_evento
    solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
    solicitud.save(update_fields=['estado', 'updated_at'])
    solicitud.aprobaciones_pagador.filter(
        estado__in=['PENDIENTE', 'APROBADO']
    ).update(
        estado='INVALIDADA',
        motivo='DATOS_MODIFICADOS',
        updated_at=timezone.now(),
    )
    return registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_PENDIENTE,
        titulo='Evaluación pendiente',
        descripcion='La solicitud está lista para una evaluación posterior.',
        metadata={'version_datos': version_datos, 'motivo': motivo},
        visible_cliente=True,
        usuario=usuario,
    )


def invalidar_evaluacion_si_cambiaron_datos(
    solicitud, *, version_anterior, usuario=None, campos=None, motivo='datos_relevantes_modificados'
):
    version_actual, _ = construir_version_datos(solicitud)
    if not version_anterior or version_anterior == version_actual:
        return False
    if solicitud.estado not in ESTADOS_CON_EVALUACION and not solicitud.auditorias_predecision.exists():
        return False
    solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
    solicitud.save(update_fields=['estado', 'updated_at'])
    solicitud.aprobaciones_pagador.filter(
        estado__in=['PENDIENTE', 'APROBADO']
    ).update(
        estado='INVALIDADA',
        motivo='DATOS_MODIFICADOS',
        updated_at=timezone.now(),
    )
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
        titulo='Datos relevantes modificados',
        descripcion='La evaluación previa quedó desactualizada y debe ejecutarse nuevamente.',
        metadata={
            'version_datos': version_actual,
            'campos': list(campos or []),
            'motivo': motivo,
        },
        visible_cliente=True,
        usuario=usuario,
    )
    return True


@transaction.atomic
def iniciar_evaluacion_prestador(solicitud, usuario=None):
    _validar_actor(solicitud, usuario)
    solicitud_bloqueada = (
        ContractorApplication.objects.select_for_update()
        .select_related('usuario', 'empresa')
        .get(pk=solicitud.pk)
    )
    version_datos, snapshot_entrada = construir_version_datos(solicitud_bloqueada)
    clave = construir_clave_idempotencia(
        solicitud=solicitud_bloqueada,
        version_datos=version_datos,
    )
    existente = PredecisionPrestadorAudit.objects.filter(clave_idempotencia=clave).first()
    if existente:
        return ResultadoInicioEvaluacionPrestador(
            auditoria=existente,
            reutilizada=existente.estado_ejecucion == existente.EstadoEjecucion.COMPLETADA,
            en_proceso=existente.estado_ejecucion == existente.EstadoEjecucion.EN_PROCESO,
        )

    if solicitud_bloqueada.estado != ContractorApplication.Estado.EVALUACION_PENDIENTE:
        raise ValidationError('La solicitud no está pendiente de evaluación.')

    ahora = timezone.now()
    auditoria = PredecisionPrestadorAudit.objects.create(
        solicitud=solicitud_bloqueada,
        version_datos=version_datos,
        clave_idempotencia=clave,
        estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.EN_PROCESO,
        resultado=PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
        version_score=VERSION_SCORE_NO_HABILITADO,
        version_politica=VERSION_POLITICA_EVALUACION,
        snapshot_entrada=snapshot_entrada,
        iniciada_en=ahora,
        creada_por=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )
    solicitud_bloqueada.estado = ContractorApplication.Estado.EN_EVALUACION
    solicitud_bloqueada.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=solicitud_bloqueada,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_INICIADA,
        titulo='Evaluación iniciada',
        descripcion='Se inició la evaluación operativa de la solicitud.',
        metadata={
            'auditoria_id': auditoria.id,
            'version_datos': version_datos,
            'modo_evaluacion': MODO_EVALUACION_BASE,
        },
        usuario=usuario,
    )

    auditoria.estado_ejecucion = PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA
    auditoria.resultado = PredecisionPrestadorAudit.Resultado.NO_EVALUABLE
    auditoria.razones = [RAZON_SERVICIOS_NO_HABILITADOS]
    auditoria.snapshot_salida = {
        'resultado': PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
        'requiere_revision_manual': True,
        'fuente': MODO_EVALUACION_BASE,
    }
    auditoria.finalizada_en = timezone.now()
    auditoria.save(update_fields=[
        'estado_ejecucion', 'resultado', 'razones', 'snapshot_salida',
        'finalizada_en', 'updated_at',
    ])
    solicitud_bloqueada.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    solicitud_bloqueada.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=solicitud_bloqueada,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_COMPLETADA,
        titulo='Evaluación base completada',
        descripcion='La evaluación terminó sin ejecutar servicios externos.',
        metadata={
            'auditoria_id': auditoria.id,
            'resultado': auditoria.resultado,
            'estado_ejecucion': auditoria.estado_ejecucion,
        },
        usuario=usuario,
    )
    registrar_evento_timeline_prestador(
        solicitud=solicitud_bloqueada,
        tipo_evento=TimelinePrestador.TipoEvento.REVISION_MANUAL_REQUERIDA,
        titulo='Revisión manual requerida',
        descripcion=RAZON_SERVICIOS_NO_HABILITADOS,
        metadata={'auditoria_id': auditoria.id, 'resultado': auditoria.resultado},
        visible_cliente=True,
        usuario=usuario,
    )
    return ResultadoInicioEvaluacionPrestador(auditoria=auditoria)


def _validar_actor(solicitud, usuario):
    if usuario is None:
        return
    if not getattr(usuario, 'is_authenticated', False):
        raise PermissionDenied('Debes iniciar sesión para evaluar la solicitud.')
    if usuario.is_staff or solicitud.usuario_id == usuario.id:
        return
    raise PermissionDenied('No puedes evaluar una solicitud de otro usuario.')

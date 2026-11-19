"""Owner-visible, read-only projection. No provider or orchestration calls."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from contractors.politica_decision import VERSION_POLITICA as VERSION_DECISION
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.presentacion_solicitud import construir_estado_publico_solicitud


def construir_seguimiento_evaluacion(solicitud, *, estado_publico=None):
    job = solicitud.evaluacion_jobs.order_by('-created_at', '-pk').first()
    es_v3 = job is not None and job.contexto.get('version_politica') == VERSION_DECISION
    if es_v3:
        # The worker can finish between the dashboard's first read and this projection.
        solicitud = type(solicitud).objects.get(pk=solicitud.pk)
        estado_publico = None
    publico = estado_publico or construir_estado_publico_solicitud(solicitud)
    estado = publico['codigo']
    mensaje = publico['titulo']
    etiqueta, detalle = publico['etiqueta'], publico['detalle']
    procesando = False
    continuar = False
    actualizado = solicitud.updated_at
    if job and job.contexto.get('version_datos') != construir_version_datos(solicitud)[0]:
        job = None
    flags = bool(getattr(settings, 'DATACREDITO_ENABLED', False) and getattr(settings, 'DATACREDITO_REAL_ENABLED', False))
    if solicitud.estado in {solicitud.Estado.EVALUACION_PENDIENTE, solicitud.Estado.EN_EVALUACION}:
        estado = 'EVALUACION_PENDIENTE'
        mensaje = 'Recibimos tu solicitud. La evaluación está pendiente'
        etiqueta, detalle = 'Evaluación pendiente', 'Te informaremos del siguiente paso.'
    # V3 publishes a predecision before its definitive decision commits.
    # While that job is running the browser must keep polling, even across stages.
    if job and (solicitud.estado in {solicitud.Estado.EVALUACION_PENDIENTE, solicitud.Estado.EN_EVALUACION, solicitud.Estado.EN_REVISION_MANUAL}
                or (es_v3 and job.estado == job.Estado.EN_EJECUCION)):
        actualizado = max(actualizado, job.updated_at)
        automatico = bool(getattr(settings, 'PRESTADORES_AUTO_EVALUACION_ENABLED', False))
        if job.estado in {job.Estado.PENDIENTE_PUBLICACION, job.Estado.ENCOLADA} and flags and automatico:
            estado, mensaje, continuar = 'ENVIADA', 'Tu solicitud fue enviada para evaluación', True
            etiqueta, detalle = 'Solicitud enviada', 'Te informaremos del siguiente paso.'
        elif (job.estado == job.Estado.EN_EJECUCION and flags
              and automatico
              and job.lease_hasta and job.lease_hasta > timezone.now()
              and (es_v3 or solicitud.estado == solicitud.Estado.EN_EVALUACION)):
            estado, mensaje, continuar, procesando = 'EN_EVALUACION', 'Estamos validando tu información', True, True
            etiqueta, detalle = 'En evaluación', 'Te informaremos del siguiente paso.'
        elif job.estado == job.Estado.REQUIERE_REVISION:
            estado, mensaje = 'REVISION', 'Tu solicitud requiere validaciones adicionales'
            etiqueta, detalle = 'Revisión en curso', 'Nuestro equipo revisará la información registrada.'
    if solicitud.estado == solicitud.Estado.EN_EVALUACION and flags and not procesando:
        auditoria = solicitud.auditorias_predecision.filter(estado_ejecucion='EN_PROCESO').order_by('-iniciada_en').first()
        segundos = max(int(getattr(settings, 'DATACREDITO_IN_PROGRESS_MINUTES', 5)) * 60,
                       int(getattr(settings, 'DATACREDITO_TIMEOUT_SECONDS', 15)) * 8 + 60)
        if auditoria and auditoria.iniciada_en > timezone.now() - timedelta(seconds=segundos):
            estado, mensaje, continuar, procesando = 'EN_EVALUACION', 'Estamos validando tu información', True, True
            etiqueta, detalle = 'En evaluación', 'Te informaremos del siguiente paso.'
    if estado == 'PREAPROBADO_READ_ONLY':
        estado = 'EVALUACION_INICIAL_FAVORABLE'
        mensaje = 'Tu solicitud superó la evaluación inicial y continuará con las siguientes validaciones'
    elif estado in {'BLOQUEADO_READ_ONLY', 'NO_EVALUABLE', 'ERROR_CONTROLADO', 'EN_REVISION_MANUAL'}:
        estado, mensaje = 'REVISION', 'Tu solicitud requiere validaciones adicionales'
    return {
        'estado': estado, 'etapa': 'EVALUACION', 'mensaje': mensaje,
        'etiqueta': etiqueta, 'detalle': detalle,
        'actualizado_en': actualizado.isoformat(),
        'continuar_seguimiento': continuar, 'procesando': procesando,
    }

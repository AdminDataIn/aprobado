"""Durable delivery only. Risk decisions remain in evaluacion_formal."""
from datetime import timedelta
import hashlib
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import ContractorApplication, EvaluacionPrestadorJob, PredecisionPrestadorAudit
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.capacidad_contractual import obtener_configuracion_simulador_prestador
from contractors.services.datacredito_evaluacion import REUTILIZAR_SI_VIGENTE, _validar_configuracion
from contractors.services.evaluacion_formal import (
    MODO_EVALUACION_FORMAL, VERSION_FINGERPRINT, VERSION_NORMALIZADOR,
    _contexto_consulta, _validar_actor, evaluar_solicitud_prestador,
)
from contractors.services.evaluacion_versionado import construir_clave_idempotencia, construir_version_datos
from contractors.services.solicitud import solicitud_tiene_documentos_obligatorios
from integrations.datacredito.readiness import evaluar_readiness_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito


logger = logging.getLogger(__name__)
PENDIENTES = (EvaluacionPrestadorJob.Estado.PENDIENTE_PUBLICACION, EvaluacionPrestadorJob.Estado.ENCOLADA)


class EvaluacionNoDisponible(ValidationError):
    """Controlled operational reason, never a provider message."""


def validar_readiness(solicitud, actor):
    if not getattr(settings, 'PRESTADORES_AUTO_EVALUACION_ENABLED', False):
        raise EvaluacionNoDisponible('automatizacion_desactivada')
    if (actor is None or not actor.is_active
            or str(actor.pk) != str(getattr(settings, 'PRESTADORES_EVALUACION_ACTOR_ID', '') or '')):
        raise EvaluacionNoDisponible('actor_no_disponible')
    _validar_actor(actor)
    if not obtener_configuracion_datacredito().real_enabled:
        raise EvaluacionNoDisponible('proveedores_desactivados')
    if not obtener_autorizacion_datacredito_vigente(solicitud):
        raise EvaluacionNoDisponible('consentimiento_no_vigente')
    if (not solicitud.simulada_en or not solicitud_tiene_documentos_obligatorios(solicitud)
            or solicitud.estado_analisis_contractual not in {'COMPLETADO', 'CON_ADVERTENCIAS', 'NO_DISPONIBLE'}):
        raise EvaluacionNoDisponible('solicitud_incompleta')
    politica = obtener_politica_score_activa()
    financiera = obtener_configuracion_simulador_prestador()
    if (not politica or not financiera or politica.configuracion_financiera_id != financiera.pk
            or solicitud.version_configuracion_financiera_simulacion != financiera.version
            or solicitud.version_politica_simulacion != politica.version_politica):
        raise EvaluacionNoDisponible('configuracion_no_vigente')
    servicios = tuple(s for s, aplica in (
        ('decisor', politica.requiere_midecisor or (politica.peso_midecisor or 0) > 0),
        ('historial', politica.requiere_hdcplus or (politica.peso_hdcplus or 0) > 0),
    ) if aplica) if politica.usa_fuentes_duales else (obtener_configuracion_datacredito().default_service,)
    reporte = evaluar_readiness_datacredito()
    secciones = ('GLOBAL', 'CONSENTIMIENTO') + tuple('HDC' if s == 'historial' else 'MIDECISOR' for s in servicios)
    if (not servicios or any(not r['configurado'] for s in secciones for r in reporte['secciones'][s] if r['critico'])
            or any(_validar_configuracion(obtener_configuracion_datacredito(), s) for s in servicios)):
        raise EvaluacionNoDisponible('proveedor_no_configurado')
    return politica, financiera, servicios


def construir_contexto_job(solicitud, actor):
    politica, financiera, servicios = validar_readiness(solicitud, actor)
    version_datos, _ = construir_version_datos(solicitud)
    return {
        'version_datos': version_datos, 'version_politica': politica.version_politica,
        'version_score': politica.version_score, 'version_financiera': financiera.version,
        'contexto_consulta': _contexto_consulta(solicitud, servicios),
    }


def _actor_tecnico():
    identificador = str(getattr(settings, 'PRESTADORES_EVALUACION_ACTOR_ID', '') or '')
    return get_user_model().objects.filter(pk=int(identificador)).first() if identificador.isdigit() else None


@transaction.atomic
def reservar_evaluacion_automatica(solicitud):
    # This entry is called only for a newly persisted confirmation, never on GET/replay.
    if not getattr(settings, 'PRESTADORES_AUTO_EVALUACION_ENABLED', False):
        return None
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado != solicitud.Estado.EVALUACION_PENDIENTE:
        return None
    actor = _actor_tecnico()
    try:
        contexto = construir_contexto_job(solicitud, actor)
    except (ValidationError, PermissionDenied):
        return None
    clave = construir_clave_idempotencia(
        solicitud=solicitud, version_datos=contexto['version_datos'],
        version_politica=contexto['version_politica'], version_score=contexto['version_score'],
        version_configuracion_financiera=contexto['version_financiera'],
        modo_evaluacion=f'{MODO_EVALUACION_FORMAL}:{REUTILIZAR_SI_VIGENTE}:{VERSION_FINGERPRINT}:{VERSION_NORMALIZADOR}',
    )
    clave = hashlib.sha256((clave + json.dumps(contexto, sort_keys=True)).encode('utf-8')).hexdigest()
    job, creado = EvaluacionPrestadorJob.objects.get_or_create(
        clave_contexto=clave,
        defaults={'solicitud': solicitud, 'actor': actor, 'contexto': contexto, 'proxima_publicacion': timezone.now()},
    )
    if creado:
        EvaluacionPrestadorJob.objects.filter(solicitud=solicitud, estado__in=PENDIENTES).exclude(pk=job.pk).update(
            estado=EvaluacionPrestadorJob.Estado.OBSOLETA, codigo_operativo='contexto_reemplazado',
            finalizada_en=timezone.now(), updated_at=timezone.now(),
        )
        transaction.on_commit(lambda: _publicar_sin_interrumpir(job.pk))
    return job


def publicar_job(job_id):
    from contractors.tasks import evaluar_solicitud_prestador_task

    if not getattr(settings, 'PRESTADORES_AUTO_EVALUACION_ENABLED', False):
        return False
    solicitud_id = EvaluacionPrestadorJob.objects.values_list('solicitud_id', flat=True).get(pk=job_id)
    with transaction.atomic():
        solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud_id)
        job = EvaluacionPrestadorJob.objects.select_for_update().get(pk=job_id)
        ahora = timezone.now()
        if job.estado not in PENDIENTES or job.proxima_publicacion > ahora:
            return False
        try:
            contexto = construir_contexto_job(solicitud, get_user_model().objects.filter(pk=job.actor_id).first())
        except (ValidationError, PermissionDenied):
            _cerrar_job(job, job.Estado.SUSPENDIDA_OPERATIVA, 'readiness_no_disponible')
            return False
        if contexto != job.contexto:
            _cerrar_job(job, job.Estado.OBSOLETA, 'contexto_modificado')
            return False
        job.intentos_publicacion += 1
        demora = min(3600, max(1, settings.PRESTADORES_EVALUACION_PUBLICATION_BACKOFF_SECONDS) * 2 ** min(job.intentos_publicacion - 1, 6))
        job.proxima_publicacion = ahora + timedelta(seconds=demora)
        job.save(update_fields=['intentos_publicacion', 'proxima_publicacion', 'updated_at'])
    try:
        evaluar_solicitud_prestador_task.apply_async(args=[job.pk], retry=False)
    except Exception:
        EvaluacionPrestadorJob.objects.filter(pk=job.pk, estado__in=PENDIENTES).update(
            codigo_operativo='publicacion_pendiente', updated_at=timezone.now(),
        )
        logger.warning('prestadores_publicacion_pendiente')
        return False
    # The worker may already have claimed or completed this job before broker ack.
    EvaluacionPrestadorJob.objects.filter(pk=job.pk, estado__in=PENDIENTES).update(
        estado=EvaluacionPrestadorJob.Estado.ENCOLADA, publicada_en=ahora, codigo_operativo='',
        proxima_publicacion=ahora + timedelta(seconds=max(60, settings.PRESTADORES_EVALUACION_DELIVERY_SECONDS)),
        updated_at=timezone.now(),
    )
    return True


def _publicar_sin_interrumpir(job_id):
    # A committed confirmation must not look failed, even if the DB/broker drops.
    try:
        return publicar_job(job_id)
    except Exception:
        logger.warning('prestadores_publicacion_pendiente')
        return False


def despachar_pendientes():
    if not getattr(settings, 'PRESTADORES_AUTO_EVALUACION_ENABLED', False):
        return 0
    # Expired execution is uncertain: never dispatch it again automatically.
    EvaluacionPrestadorJob.objects.filter(
        estado=EvaluacionPrestadorJob.Estado.EN_EJECUCION, lease_hasta__lte=timezone.now(),
    ).update(estado=EvaluacionPrestadorJob.Estado.REQUIERE_REVISION,
             codigo_operativo='ejecucion_incierta', finalizada_en=timezone.now(), updated_at=timezone.now())
    ids = list(EvaluacionPrestadorJob.objects.filter(
        estado__in=PENDIENTES, proxima_publicacion__lte=timezone.now(),
    ).order_by('proxima_publicacion', 'pk').values_list('pk', flat=True)[:max(1, min(100, settings.PRESTADORES_EVALUACION_DISPATCH_BATCH))])
    return sum(_publicar_sin_interrumpir(pk) for pk in ids)


def ejecutar_job(job_id):
    # Application -> job is the consistent lock order; no HTTP inside this block.
    solicitud_id = EvaluacionPrestadorJob.objects.values_list('solicitud_id', flat=True).get(pk=job_id)
    with transaction.atomic():
        solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud_id)
        job = EvaluacionPrestadorJob.objects.select_for_update().get(pk=job_id)
        if job.estado not in PENDIENTES:
            return job.estado
        actor = get_user_model().objects.filter(pk=job.actor_id).first()
        try:
            contexto = construir_contexto_job(solicitud, actor)
        except (ValidationError, PermissionDenied):
            return _cerrar_job(job, job.Estado.SUSPENDIDA_OPERATIVA, 'readiness_no_disponible')
        if contexto != job.contexto:
            return _cerrar_job(job, job.Estado.OBSOLETA, 'contexto_modificado')
        if solicitud.estado not in {solicitud.Estado.EVALUACION_PENDIENTE, solicitud.Estado.EN_REVISION_MANUAL}:
            return _cerrar_job(job, job.Estado.REQUIERE_REVISION, 'etapa_no_disponible')
        job.estado = job.Estado.EN_EJECUCION
        job.iniciada_en = timezone.now()
        job.generacion += 1
        job.lease_hasta = job.iniciada_en + timedelta(seconds=max(300, settings.PRESTADORES_EVALUACION_LEASE_SECONDS))
        job.save(update_fields=['estado', 'iniciada_en', 'generacion', 'lease_hasta', 'updated_at'])
    try:
        resultado = evaluar_solicitud_prestador(
            solicitud, solicitado_por=actor, contexto_esperado=job.contexto,
            reserva_operativa=(job.pk, job.generacion),
        )
    except (ValidationError, PermissionDenied):
        estado, codigo, auditoria_id = job.Estado.SUSPENDIDA_OPERATIVA, 'contexto_no_disponible', None
    except Exception:
        # Never persist exception strings or resubmit an uncertain provider request.
        estado, codigo, auditoria_id = job.Estado.REQUIERE_REVISION, 'ejecucion_incierta', None
        logger.warning('prestadores_ejecucion_incierta')
    else:
        auditoria_id = resultado.auditoria.pk
        estado = (job.Estado.FINALIZADA if resultado.auditoria.resultado == PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
                  and not resultado.en_proceso else job.Estado.REQUIERE_REVISION)
        codigo = 'evaluacion_en_curso' if resultado.en_proceso else ''
    EvaluacionPrestadorJob.objects.filter(pk=job.pk, estado=job.Estado.EN_EJECUCION, generacion=job.generacion).update(
        estado=estado, codigo_operativo=codigo, auditoria_id=auditoria_id,
        finalizada_en=timezone.now(), updated_at=timezone.now(),
    )
    return estado


def _cerrar_job(job, estado, codigo):
    job.estado, job.codigo_operativo, job.finalizada_en = estado, codigo, timezone.now()
    job.save(update_fields=['estado', 'codigo_operativo', 'finalizada_en', 'updated_at'])
    return estado

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import ContractorApplication, PredecisionPrestadorAudit, TimelinePrestador
from contractors.score.politica import PoliticaScoreNoDisponible, obtener_politica_score_activa
from contractors.services.datacredito_evaluacion import (
    REUTILIZAR_SI_VIGENTE,
    obtener_evaluacion_datacredito_prestador,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.evaluacion_versionado import (
    construir_clave_idempotencia,
    construir_version_datos,
)
from contractors.services.predecision import evaluar_predecision_formal_prestador


MODO_EVALUACION_FORMAL = 'FORMAL_READ_ONLY_V2'
VERSION_SIN_POLITICA = 'politica_no_configurada'
VERSION_SCORE_SIN_POLITICA = 'score_no_configurado'


@dataclass(frozen=True)
class ResultadoEvaluacionFormalPrestador:
    auditoria: PredecisionPrestadorAudit
    reutilizada: bool = False
    en_proceso: bool = False


def evaluar_solicitud_prestador(
    solicitud,
    solicitado_por=None,
    modo_datacredito=REUTILIZAR_SI_VIGENTE,
    justificacion=None,
):
    _validar_actor(solicitado_por)
    try:
        politica = obtener_politica_score_activa()
        version_politica = politica.version_politica if politica else VERSION_SIN_POLITICA
        version_score = politica.version_score if politica else VERSION_SCORE_SIN_POLITICA
    except PoliticaScoreNoDisponible as exc:
        politica = None
        version_politica = VERSION_SIN_POLITICA
        version_score = VERSION_SCORE_SIN_POLITICA
        error_politica = str(exc)
    else:
        error_politica = '' if politica else 'No existe una politica de score activa y vigente.'

    configuracion_financiera = (
        politica.configuracion_financiera if politica and politica.configuracion_financiera_id else None
    )
    version_configuracion_financiera = (
        configuracion_financiera.version if configuracion_financiera else ''
    )

    inicio = _iniciar_evaluacion(
        solicitud=solicitud,
        usuario=solicitado_por,
        version_politica=version_politica,
        version_score=version_score,
        configuracion_financiera=configuracion_financiera,
        version_configuracion_financiera=version_configuracion_financiera,
        modo_datacredito=modo_datacredito,
    )
    if inicio.reutilizada or inicio.en_proceso:
        if inicio.reutilizada:
            _crear_revisiones_operativas(inicio.auditoria, solicitado_por)
        return inicio

    auditoria = inicio.auditoria
    if politica is None:
        return _finalizar_sin_decision(
            auditoria,
            resultado=PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=(error_politica,),
            error_codigo='politica_score_no_disponible',
            usuario=solicitado_por,
        )

    try:
        # Esta llamada puede hacer HTTP. Debe permanecer fuera de transaction.atomic.
        datacredito = obtener_evaluacion_datacredito_prestador(
            solicitud,
            modo=modo_datacredito,
            solicitado_por=solicitado_por,
            justificacion=justificacion,
        )
        predecision = evaluar_predecision_formal_prestador(
            solicitud=solicitud,
            politica=politica,
            datacredito=datacredito,
        )
    except Exception as exc:  # El detalle tecnico no se persiste ni se expone.
        return _finalizar_sin_decision(
            auditoria,
            resultado=PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
            razones=('La evaluacion no pudo completarse por un error controlado.',),
            error_codigo=type(exc).__name__[:80],
            usuario=solicitado_por,
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO,
        )

    return _finalizar_evaluacion(
        auditoria=auditoria,
        predecision=predecision,
        datacredito=datacredito,
        usuario=solicitado_por,
    )


@transaction.atomic
def _iniciar_evaluacion(
    *, solicitud, usuario, version_politica, version_score, configuracion_financiera,
    version_configuracion_financiera, modo_datacredito,
):
    solicitud_bloqueada = ContractorApplication.objects.select_for_update().get(pk=solicitud.pk)
    version_datos, snapshot_entrada = construir_version_datos(solicitud_bloqueada)
    clave = construir_clave_idempotencia(
        solicitud=solicitud_bloqueada,
        version_datos=version_datos,
        version_politica=version_politica,
        version_score=version_score,
        version_configuracion_financiera=version_configuracion_financiera,
        modo_evaluacion=f'{MODO_EVALUACION_FORMAL}:{modo_datacredito}',
    )
    existente = PredecisionPrestadorAudit.objects.filter(clave_idempotencia=clave).first()
    if existente:
        return ResultadoEvaluacionFormalPrestador(
            auditoria=existente,
            reutilizada=existente.estado_ejecucion in {
                existente.EstadoEjecucion.COMPLETADA,
                existente.EstadoEjecucion.ERROR_CONTROLADO,
            },
            en_proceso=existente.estado_ejecucion == existente.EstadoEjecucion.EN_PROCESO,
        )
    if solicitud_bloqueada.estado not in {
        ContractorApplication.Estado.EVALUACION_PENDIENTE,
        ContractorApplication.Estado.EN_REVISION_MANUAL,
    }:
        raise ValidationError('La solicitud no esta pendiente ni habilitada para reintento.')

    auditoria = PredecisionPrestadorAudit.objects.create(
        solicitud=solicitud_bloqueada,
        version_datos=version_datos,
        clave_idempotencia=clave,
        estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.EN_PROCESO,
        resultado=PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
        version_score=version_score,
        version_politica=version_politica,
        version_configuracion_financiera=version_configuracion_financiera,
        tasa_mensual_configuracion=(
            configuracion_financiera.tasa_mensual if configuracion_financiera else None
        ),
        monto_maximo_configuracion=(
            configuracion_financiera.monto_maximo if configuracion_financiera else None
        ),
        plazo_maximo_configuracion=(
            configuracion_financiera.plazo_maximo_meses if configuracion_financiera else None
        ),
        snapshot_entrada=snapshot_entrada,
        iniciada_en=timezone.now(),
        creada_por=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )
    solicitud_bloqueada.estado = ContractorApplication.Estado.EN_EVALUACION
    solicitud_bloqueada.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=solicitud_bloqueada,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_INICIADA,
        titulo='Evaluacion formal iniciada',
        descripcion='Se inicio la evaluacion read-only de la solicitud.',
        metadata={
            'auditoria_id': auditoria.id,
            'version_datos': version_datos,
            'modo_evaluacion': MODO_EVALUACION_FORMAL,
        },
        usuario=usuario,
    )
    return ResultadoEvaluacionFormalPrestador(auditoria=auditoria)


@transaction.atomic
def _finalizar_evaluacion(*, auditoria, predecision, datacredito, usuario):
    auditoria_bloqueada = PredecisionPrestadorAudit.objects.select_for_update().get(pk=auditoria.pk)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=auditoria.solicitud_id)
    version_actual, _ = construir_version_datos(solicitud)
    if version_actual != auditoria_bloqueada.version_datos:
        auditoria_bloqueada.estado_ejecucion = PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO
        auditoria_bloqueada.resultado = PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO
        auditoria_bloqueada.razones = ['Los datos cambiaron durante la evaluacion.']
        auditoria_bloqueada.error_codigo = 'datos_modificados_durante_evaluacion'
        auditoria_bloqueada.error_etapa = 'cierre'
        auditoria_bloqueada.snapshot_salida = {
            'resultado': PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
            'requiere_revision_manual': True,
            'fuente': MODO_EVALUACION_FORMAL,
        }
        auditoria_bloqueada.finalizada_en = timezone.now()
        auditoria_bloqueada.save(update_fields=[
            'estado_ejecucion', 'resultado', 'razones', 'error_codigo', 'error_etapa',
            'snapshot_salida', 'finalizada_en', 'updated_at',
        ])
        solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
        solicitud.save(update_fields=['estado', 'updated_at'])
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
            titulo='Datos modificados durante la evaluacion',
            descripcion='El resultado fue descartado y la solicitud volvio a quedar pendiente.',
            metadata={
                'auditoria_id': auditoria_bloqueada.id,
                'resultado': auditoria_bloqueada.resultado,
                'version_datos': version_actual,
            },
            usuario=usuario,
        )
        _crear_revisiones_operativas(auditoria_bloqueada, usuario)
        return ResultadoEvaluacionFormalPrestador(auditoria=auditoria_bloqueada)

    score = predecision.score_resultado
    auditoria_bloqueada.estado_ejecucion = (
        PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO
        if predecision.resultado == PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO
        else PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA
    )
    auditoria_bloqueada.resultado = predecision.resultado
    auditoria_bloqueada.score = score.score_final if score else None
    auditoria_bloqueada.razones = list(predecision.razones)
    auditoria_bloqueada.alertas = list(predecision.alertas)
    auditoria_bloqueada.bloqueos = list(predecision.bloqueos)
    auditoria_bloqueada.snapshot_salida = {
        **predecision.como_dict(),
        'datacredito': _snapshot_datacredito_allowlist(datacredito),
    }
    auditoria_bloqueada.error_codigo = getattr(datacredito, 'error_codigo', '') or ''
    auditoria_bloqueada.error_etapa = (
        'datacredito' if auditoria_bloqueada.error_codigo else ''
    )
    auditoria_bloqueada.finalizada_en = timezone.now()
    auditoria_bloqueada.save(update_fields=[
        'estado_ejecucion', 'resultado', 'score', 'razones', 'alertas', 'bloqueos',
        'snapshot_salida', 'error_codigo', 'error_etapa', 'finalizada_en', 'updated_at',
    ])

    if predecision.resultado in {
        PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
        PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
    }:
        solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
    else:
        solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    solicitud.save(update_fields=['estado', 'updated_at'])
    _registrar_cierre_timeline(solicitud, auditoria_bloqueada, usuario)
    return ResultadoEvaluacionFormalPrestador(auditoria=auditoria_bloqueada)


@transaction.atomic
def _finalizar_sin_decision(
    auditoria, *, resultado, razones, error_codigo, usuario,
    estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA,
):
    auditoria_bloqueada = PredecisionPrestadorAudit.objects.select_for_update().get(pk=auditoria.pk)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=auditoria.solicitud_id)
    auditoria_bloqueada.estado_ejecucion = estado_ejecucion
    auditoria_bloqueada.resultado = resultado
    auditoria_bloqueada.razones = list(razones)
    auditoria_bloqueada.error_codigo = error_codigo
    auditoria_bloqueada.error_etapa = 'politica' if error_codigo == 'politica_score_no_disponible' else 'evaluacion'
    auditoria_bloqueada.snapshot_salida = {
        'resultado': resultado,
        'requiere_revision_manual': True,
        'razones': list(razones),
        'fuente': MODO_EVALUACION_FORMAL,
    }
    auditoria_bloqueada.finalizada_en = timezone.now()
    auditoria_bloqueada.save(update_fields=[
        'estado_ejecucion', 'resultado', 'razones', 'error_codigo', 'error_etapa',
        'snapshot_salida', 'finalizada_en', 'updated_at',
    ])
    solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    solicitud.save(update_fields=['estado', 'updated_at'])
    _registrar_cierre_timeline(solicitud, auditoria_bloqueada, usuario)
    return ResultadoEvaluacionFormalPrestador(auditoria=auditoria_bloqueada)


def _registrar_cierre_timeline(solicitud, auditoria, usuario):
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.EVALUACION_COMPLETADA,
        titulo='Evaluacion formal completada',
        descripcion='La evaluacion read-only fue registrada de forma auditable.',
        metadata={
            'auditoria_id': auditoria.id,
            'resultado': auditoria.resultado,
            'estado_ejecucion': auditoria.estado_ejecucion,
        },
        usuario=usuario,
    )
    if solicitud.estado == ContractorApplication.Estado.EN_REVISION_MANUAL:
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.REVISION_MANUAL_REQUERIDA,
            titulo='Revision manual requerida',
            descripcion='La evaluacion no habilita avance automatico.',
            metadata={'auditoria_id': auditoria.id, 'resultado': auditoria.resultado},
            visible_cliente=True,
            usuario=usuario,
        )
    _crear_revisiones_operativas(auditoria, usuario)


def _crear_revisiones_operativas(auditoria, usuario):
    from contractors.services.revision_manual import crear_revisiones_para_auditoria

    return crear_revisiones_para_auditoria(auditoria, usuario=usuario)


def _snapshot_datacredito_allowlist(resultado):
    normalizado = getattr(resultado, 'resultado_normalizado', None)
    return {
        'estado': str(getattr(resultado, 'estado', '') or ''),
        'reutilizado': bool(getattr(resultado, 'reutilizado', False)),
        'snapshot_id': str(getattr(resultado, 'snapshot_id', '') or ''),
        'servicio': str(getattr(resultado, 'servicio', '') or ''),
        'error_codigo': str(getattr(resultado, 'error_codigo', '') or ''),
        'score_externo': getattr(normalizado, 'score_externo', None),
        'mora_severa': getattr(normalizado, 'mora_severa', None),
        'mora_maxima_dias': getattr(normalizado, 'mora_maxima_dias', None),
        'obligaciones_vigentes': getattr(normalizado, 'obligaciones_vigentes', None),
        'obligaciones_en_mora': getattr(normalizado, 'obligaciones_en_mora', None),
        'consultas_recientes': getattr(normalizado, 'consultas_recientes', None),
    }


def _validar_actor(usuario):
    if usuario is None:
        return
    if not getattr(usuario, 'is_authenticated', False):
        raise PermissionDenied('Debes iniciar sesion para ejecutar la evaluacion.')
    if not usuario.is_staff or not usuario.has_perm(
        'contractors.can_evaluate_contractor_application'
    ):
        raise PermissionDenied('No tienes permiso para ejecutar la evaluacion formal.')

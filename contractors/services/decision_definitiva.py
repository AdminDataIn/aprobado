"""Final mandate over immutable risk evidence. No HTTP, score or financial formulae."""
from dataclasses import asdict, dataclass
from datetime import datetime, time
import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador, ContractorApplication, PredecisionPrestadorAudit,
    RevisionManualPrestador, TimelinePrestador,
)
from contractors.politica_decision import huella_reglas, reglas_publicadas
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.evaluacion_versionado import construir_version_datos, _hash_contrato_guardado
from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable
from contractors.services.politica_ingreso_publicacion import obtener_politica_para_solicitud
from contractors.services.revision_manual import ESTADOS_REVISION_ACTIVA, crear_revisiones_para_auditoria
from contractors.score.politica import PoliticaScoreNoDisponible

APROBADO = 'APROBADO_AUTOMATICO'
RECHAZADO = 'RECHAZADO_AUTOMATICO'
REVISION = 'REQUIERE_REVISION_MANUAL'
TIPO_EVENTO = 'DECISION_FINANCIERA_V3'


@dataclass(frozen=True)
class DecisionDefinitivaPrestador:
    resultado: str
    causas: tuple[str, ...]
    auditoria_id: int
    evento_id: int | None = None
    gate_id: int | None = None
    reutilizada: bool = False


def _actor(actor):
    if (not actor or not actor.is_authenticated or not actor.is_active or not actor.is_staff
            or hasattr(actor, 'perfil_pagador')
            or not actor.has_perm('contractors.can_evaluate_contractor_application')):
        raise PermissionDenied('No puede ejecutar el decisor de prestadores.')


def _hash(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()


def _insumos(solicitud, auditoria):
    from contractors.services.aprobacion_interna import _obtener_snapshots_datacredito
    from contractors.services.politica_score import construir_snapshot_politica_score
    causas = []
    ahora = timezone.now()
    version, _ = construir_version_datos(solicitud)
    politica = obtener_politica_para_solicitud(solicitud)
    try:
        reglas = reglas_publicadas(politica)
    except ValidationError:
        reglas = None
    if not reglas:
        causas.append('MANDATO_AUTOMATICO_NO_PUBLICADO')
    if (auditoria.estado_ejecucion != 'COMPLETADA'
            or solicitud.auditorias_predecision.order_by('-created_at', '-pk').values_list('pk', flat=True).first() != auditoria.pk
            or auditoria.version_datos != version):
        causas.append('EVALUACION_NO_VIGENTE')
    if auditoria.version_politica != politica.version_politica:
        causas.append('POLITICA_CAMBIADA')
    from contractors.score.motor import detectar_incompatibilidades_simulador
    if detectar_incompatibilidades_simulador(solicitud, politica):
        causas.append('SIMULACION_INCOMPATIBLE')
    auth = obtener_autorizacion_datacredito_vigente(solicitud)
    fuentes = _obtener_snapshots_datacredito(auditoria, auth, politica) or ()
    if ({s.servicio for s in fuentes} != {'historial', 'decisor'}
            or not auditoria.evaluacion_centrales_completa):
        causas.append('FUENTES_OFICIALES_NO_UTILIZABLES')
    ingreso = resolver_ingreso_evaluable(solicitud, politica)
    if not ingreso or ingreso.fuente != 'MANUAL_VERIFICADA':
        causas.append('INGRESO_DOCUMENTAL_REQUERIDO')
    from contractors.services.validacion_contractual import validar_contrato_prestador
    from contractors.services.solicitud import solicitud_tiene_documentos_obligatorios
    contrato = validar_contrato_prestador(solicitud)
    hash_contrato = _hash_contrato_guardado(solicitud)
    if (not contrato.capacidad_automatica or contrato.bloqueos or contrato.requiere_revision_manual
            or solicitud.estado_analisis_contractual != 'COMPLETADO'
            or not hash_contrato
            or hash_contrato != (solicitud.metadata_analisis_contractual or {}).get('archivo_hash_sha256')
            or not solicitud_tiene_documentos_obligatorios(solicitud)):
        causas.append('EVIDENCIA_CONTRACTUAL_INSUFICIENTE')
    pendientes = list(solicitud.requerimientos_subsanacion.filter(estado='PENDIENTE').values_list('pk', flat=True))
    if pendientes:
        causas.append('SUBSANACION_PENDIENTE')
    plazos = [s.vigente_hasta for s in fuentes]
    fechas = [solicitud.fecha_fin_contrato, politica.fecha_vigencia_hasta,
              ingreso.vigente_hasta if ingreso else None]
    plazos += [timezone.make_aware(datetime.combine(f, time.max)) for f in fechas if f]
    caducidad = min(plazos) if plazos else None
    if caducidad and caducidad <= ahora:
        causas.append('EVIDENCIA_CADUCADA')
    revisiones = list(solicitud.revisiones_manuales.filter(estado__in=ESTADOS_REVISION_ACTIVA)
                      .order_by('pk').values('pk', 'motivo', 'auditoria_predecision_id', 'estado', 'asignado_a_id'))
    datos = {'version_datos': version, 'auditoria_id': auditoria.pk,
             'evaluacion': _hash(auditoria.snapshot_salida),
             'politica': construir_snapshot_politica_score(politica), 'reglas': reglas,
             'autorizacion_id': auth.pk if auth else None,
             'fuentes': [(str(s.pk), s.estado, s.vigente_hasta) for s in fuentes],
             'ingreso': asdict(ingreso) if ingreso else None,
             'revisiones': revisiones, 'subsanaciones': pendientes,
             'caducada': bool(caducidad and caducidad <= ahora), 'controles': causas,
             'gate_aprobado': solicitud.aprobaciones_internas.filter(
                 auditoria_predecision=auditoria, estado='APROBADA_PARA_ORIGINAR').exists(),
             'rechazada': solicitud.estado == 'NO_APROBADO'}
    return politica, reglas, ingreso, fuentes, revisiones, causas, caducidad, _hash(datos)


def _evento(solicitud, auditoria, contexto):
    return solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO,
        metadata__auditoria_id=auditoria.pk, metadata__decision_contexto=contexto).order_by('-pk').first()


def _dto(evento, reutilizada=False):
    m = evento.metadata
    return DecisionDefinitivaPrestador(m['resultado'], tuple(m['causas']), m['auditoria_id'],
                                       evento.pk, m.get('gate_id'), reutilizada)


def consultar_decision_vigente(auditoria):
    """Read-only validity check; an old event never authorizes a new context."""
    solicitud = ContractorApplication.objects.get(pk=auditoria.solicitud_id)
    try:
        valores = _insumos(solicitud, auditoria)
    except (ValidationError, PoliticaScoreNoDisponible):
        return None
    evento = _evento(solicitud, auditoria, valores[-1])
    if not evento:
        return None
    if evento.metadata['resultado'] != REVISION and valores[5]:
        return None
    return _dto(evento, True)


def validar_mandato_gate(auditoria):
    automatica = auditoria.solicitud.timeline_operativo.filter(
        metadata__tipo=TIPO_EVENTO, metadata__auditoria_id=auditoria.pk,
        metadata__resultado=APROBADO).exists()
    if automatica:
        actual = consultar_decision_vigente(auditoria)
        if not actual or actual.resultado != APROBADO:
            raise ValidationError('La decision automatica no esta vigente; requiere revision.')


def _exclusion(auditoria, politica, reglas):
    """Only approved codes over core outputs; empty real catalog never rejects."""
    score = (auditoria.snapshot_salida or {}).get('score_resultado') or {}
    if auditoria.score is None or score.get('score_final') is None:
        return None
    for codigo in reglas['exclusiones']:
        if codigo == 'BANDA_REVISION' and score.get('banda') == 'REVISION':
            banda = politica.bandas.filter(nombre='REVISION', score_min__lte=auditoria.score,
                                           score_max__gte=auditoria.score).first()
            if banda:
                return codigo
        if (codigo == 'CAPACIDAD_EXCEDIDA' and auditoria.relacion_carga_ingreso is not None
                and auditoria.relacion_carga_ingreso > politica.cuota_ingreso_maxima):
            return codigo
        if codigo == 'MORA_SEVERA' and auditoria.snapshot_hdcplus_id:
            datos = auditoria.snapshot_hdcplus.resultado_normalizado
            dias = datos.get('mora_maxima_dias')
            if (type(dias) is int and datos.get('mora_severa') is True
                    and dias >= politica.mora_bloqueo_dias):
                return codigo
    return None


@transaction.atomic
def decidir_solicitud_prestador(auditoria, *, actor):
    _actor(actor)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=auditoria.solicitud_id)
    auditoria = PredecisionPrestadorAudit.objects.select_for_update().get(pk=auditoria.pk, solicitud=solicitud)
    politica, reglas, ingreso, fuentes, revisiones, causas, caducidad, contexto = _insumos(solicitud, auditoria)
    previo = _evento(solicitud, auditoria, contexto)
    if previo:
        return _dto(previo, True)
    resultado, gate = REVISION, None
    exclusion = _exclusion(auditoria, politica, reglas) if reglas and not causas else None
    if exclusion:
        # The legacy review classifier also labels a non-approvable policy band
        # POLITICA_INCOMPATIBLE; actual simulator compatibility is checked above.
        motivos = {'BANDA_REVISION': {'OTRA_REVISION_CONTROLADA', 'POLITICA_INCOMPATIBLE'},
                   'CAPACIDAD_EXCEDIDA': {'CAPACIDAD_EXCEDIDA'},
                   'MORA_SEVERA': {'OTRA_REVISION_CONTROLADA'}}[exclusion]
        if any(r['auditoria_predecision_id'] != auditoria.pk or r['motivo'] not in motivos
               or r['estado'] != 'ABIERTA' or r['asignado_a_id'] for r in revisiones):
            causas.append('REVISION_HUMANA_PENDIENTE')
        elif not actor.has_perm('contractors.can_resolve_contractor_review'):
            causas.append('ACTOR_SIN_FACULTAD_DE_CIERRE')
        else:
            from contractors.services.revision_manual import resolver_revision
            for revision in solicitud.revisiones_manuales.filter(pk__in=[r['pk'] for r in revisiones]):
                resolver_revision(revision, actor=actor, resultado='CERRAR_SIN_CONTINUAR',
                    comentario_interno='Causal excluyente autorizada en politica: ' + exclusion)
            solicitud.estado = ContractorApplication.Estado.NO_APROBADO
            solicitud.save(update_fields=['estado', 'updated_at'])
            resultado, causas = RECHAZADO, [exclusion]
    elif not causas and auditoria.resultado == 'PREAPROBADO_READ_ONLY':
        if revisiones:
            causas.append('REVISION_HUMANA_PENDIENTE')
        elif not reglas['aprobacion_automatica']:
            causas.append('APROBACION_HUMANA_REQUERIDA')
        elif not actor.has_perm('contractors.can_decide_contractor_internal_approval'):
            causas.append('ACTOR_SIN_FACULTAD_DE_APROBACION')
        else:
            from contractors.services.aprobacion_interna import (
                aprobar_para_originar, crear_o_reutilizar_aprobacion_interna,
            )
            try:
                with transaction.atomic():
                    from contractors.services.oferta_financiera import calcular_oferta_evaluada
                    oferta = calcular_oferta_evaluada(solicitud, auditoria, politica=politica,
                        monto=solicitud.monto_solicitado, plazo=solicitud.plazo_meses)
                    if oferta.estado != 'OFERTA_CALCULADA':
                        raise ValidationError('Oferta no viable.', code='OFERTA_' + oferta.estado)
                    gate, _ = crear_o_reutilizar_aprobacion_interna(auditoria, actor=actor)
                    gate = aprobar_para_originar(gate, actor=actor,
                        comentario_interno='Decision automatica conforme a mandato V3 publicado.')
                    if gate.estado != AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
                        raise ValidationError('El core no habilita aprobacion.')
            except ValidationError as exc:
                gate = None
                causas.append(exc.code if hasattr(exc, 'code') and exc.code else 'OFERTA_O_GATE_NO_VIABLE')
            else:
                resultado, causas = APROBADO, ['REQUISITOS_V3_ACREDITADOS']
    elif not causas:
        causas.append('RIESGO_REQUIERE_REVISION_SIN_EXCLUSION_AUTORIZADA')
    if resultado == REVISION:
        from contractors.services.aprobacion_interna import _devolver_gate_a_revision
        anteriores = solicitud.aprobaciones_internas.select_for_update().filter(
            estado=AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR)
        for anterior in anteriores:
            if solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO,
                    metadata__gate_id=anterior.pk, metadata__resultado=APROBADO).exists():
                _devolver_gate_a_revision(anterior, actor=actor, motivo='DATOS_MODIFICADOS',
                                         comentario='El mandato automatico requiere revalidacion.')
        crear_revisiones_para_auditoria(auditoria, usuario=actor)
        if not solicitud.revisiones_manuales.filter(estado__in=ESTADOS_REVISION_ACTIVA).exists():
            from contractors.services.revision_manual import crear_o_reutilizar_revision
            crear_o_reutilizar_revision(solicitud=solicitud, auditoria=auditoria, usuario=actor,
                motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA)
        if solicitud.estado != ContractorApplication.Estado.EN_REVISION_MANUAL:
            solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
            solicitud.save(update_fields=['estado', 'updated_at'])
    # Own effects (resolving a review) must not change the cache key on replay.
    contexto = _insumos(solicitud, auditoria)[-1]
    evento = registrar_evento_timeline_prestador(solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
        titulo='Decision financiera definitiva', usuario=actor, visible_cliente=False,
        metadata={'tipo': TIPO_EVENTO, 'auditoria_id': auditoria.pk, 'resultado': resultado,
                  'causas': causas, 'decision_contexto': contexto, 'gate_id': gate.pk if gate else None,
                  'version_politica': politica.version_politica,
                  'version_decision': reglas['version_decision'] if reglas else 'HUMANA_LEGACY',
                  'politica_decision_hash': huella_reglas(reglas) if reglas else '',
                  'vigente_hasta': caducidad.isoformat() if caducidad else None,
                  'snapshot_ids': [str(s.pk) for s in fuentes]})
    return _dto(evento)

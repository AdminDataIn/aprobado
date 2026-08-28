from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    BandaScorePrestador,
    ContractorApplication,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.score.motor import detectar_incompatibilidades_simulador
from contractors.score.politica import PoliticaScoreNoDisponible, obtener_politica_score_activa
from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.revision_manual import ESTADOS_REVISION_ACTIVA
from contractors.services.validacion_contractual import validar_contrato_prestador
from integrations.models import ConsultaDatacreditoSnapshot


ESTADOS_GATE_ACTIVO = {
    AprobacionInternaPrestador.Estado.PENDIENTE,
    AprobacionInternaPrestador.Estado.EN_ANALISIS,
    AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR,
}


@dataclass(frozen=True)
class ValidacionGatePrestador:
    politica: object
    configuracion_financiera: object
    contrato: object
    autorizacion: object
    snapshots_datacredito: tuple
    limites: dict


@transaction.atomic
def crear_o_reutilizar_aprobacion_interna(auditoria, *, actor):
    _exigir_permiso(actor, 'contractors.can_decide_contractor_internal_approval')
    auditoria = (
        PredecisionPrestadorAudit.objects.select_for_update()
        .select_related('solicitud', 'solicitud__empresa')
        .get(pk=auditoria.pk)
    )
    existente = AprobacionInternaPrestador.objects.filter(
        auditoria_predecision=auditoria
    ).first()
    if existente:
        return existente, False

    solicitud = ContractorApplication.objects.select_for_update().get(
        pk=auditoria.solicitud_id
    )
    activo = AprobacionInternaPrestador.objects.filter(
        solicitud=solicitud,
        estado__in=ESTADOS_GATE_ACTIVO,
    ).first()
    if activo:
        raise ValidationError('La solicitud ya tiene una aprobacion interna activa.')

    validacion = _validar_gate(solicitud, auditoria)
    limites = validacion.limites
    try:
        with transaction.atomic():
            gate = AprobacionInternaPrestador.objects.create(
                solicitud=solicitud,
                auditoria_predecision=auditoria,
                estado=AprobacionInternaPrestador.Estado.PENDIENTE,
                version_datos=auditoria.version_datos,
                version_politica=auditoria.version_politica,
                version_configuracion_financiera=(
                    auditoria.version_configuracion_financiera
                ),
                tasa_mensual_snapshot=auditoria.tasa_mensual_configuracion,
                monto_solicitado_snapshot=solicitud.monto_solicitado,
                monto_maximo_score_snapshot=limites['monto_score'],
                monto_maximo_politica_snapshot=limites['monto_politica'],
                monto_maximo_capacidad_snapshot=limites['monto_capacidad'],
                monto_maximo_contrato_snapshot=limites['monto_contrato'],
                monto_maximo_financiero_snapshot=limites['monto_financiero'],
                monto_maximo_evaluado=limites['monto_evaluado'],
                monto_autorizado=limites['monto_evaluado'],
                plazo_solicitado_snapshot=solicitud.plazo_meses,
                plazo_maximo_score_snapshot=limites['plazo_score'],
                plazo_maximo_politica_snapshot=limites['plazo_politica'],
                plazo_maximo_contrato_snapshot=limites['plazo_contrato'],
                plazo_maximo_financiero_snapshot=limites['plazo_financiero'],
                plazo_maximo_evaluado=limites['plazo_evaluado'],
                plazo_autorizado=limites['plazo_evaluado'],
                creada_por=actor,
            )
    except IntegrityError:
        gate = AprobacionInternaPrestador.objects.filter(
            auditoria_predecision=auditoria
        ).first()
        if gate is None:
            raise
        return gate, False

    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.APROBACION_INTERNA_CREADA,
        actor,
        estado_anterior='',
    )
    return gate, True


@transaction.atomic
def iniciar_analisis_aprobacion_interna(gate, *, actor):
    _exigir_permiso(actor, 'contractors.can_decide_contractor_internal_approval')
    gate = _bloquear_gate(gate.pk)
    if gate.estado != AprobacionInternaPrestador.Estado.PENDIENTE:
        raise ValidationError('La aprobacion interna no esta pendiente de analisis.')
    anterior = gate.estado
    gate.estado = AprobacionInternaPrestador.Estado.EN_ANALISIS
    gate.save(update_fields=['estado', 'updated_at'])
    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.APROBACION_INTERNA_INICIADA,
        actor,
        estado_anterior=anterior,
    )
    return gate


@transaction.atomic
def aprobar_para_originar(
    gate,
    *,
    actor,
    monto_autorizado=None,
    plazo_autorizado=None,
    comentario_interno='',
):
    _exigir_permiso(actor, 'contractors.can_decide_contractor_internal_approval')
    gate = _bloquear_gate(gate.pk)
    if gate.estado not in {
        AprobacionInternaPrestador.Estado.PENDIENTE,
        AprobacionInternaPrestador.Estado.EN_ANALISIS,
    }:
        raise ValidationError('La aprobacion interna no esta disponible para decidir.')

    motivos = _motivos_revalidacion(gate)
    if motivos:
        return _devolver_gate_a_revision(
            gate,
            actor=actor,
            motivo=motivos[0],
            comentario='La revalidacion previa a originacion detecto cambios controlados.',
        )

    monto = _decimal(monto_autorizado, gate.monto_autorizado)
    plazo = _entero(plazo_autorizado, gate.plazo_autorizado)
    if monto <= 0 or monto > gate.monto_maximo_evaluado:
        raise ValidationError('El monto autorizado debe ser positivo y no superar el evaluado.')
    if plazo <= 0 or plazo > gate.plazo_maximo_evaluado:
        raise ValidationError('El plazo autorizado debe ser positivo y no superar el evaluado.')

    anterior = gate.estado
    gate.estado = AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR
    gate.decision = AprobacionInternaPrestador.Decision.APROBAR_PARA_ORIGINAR
    gate.monto_autorizado = monto
    gate.plazo_autorizado = plazo
    gate.comentario_interno = str(comentario_interno or '').strip()
    gate.decidida_por = actor
    gate.decidida_en = timezone.now()
    gate.save(update_fields=[
        'estado', 'decision', 'monto_autorizado', 'plazo_autorizado',
        'comentario_interno', 'decidida_por', 'decidida_en', 'updated_at',
    ])
    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.APROBADA_PARA_ORIGINAR,
        actor,
        estado_anterior=anterior,
    )
    from contractors.services.aprobacion_pagador import (
        crear_o_reutilizar_aprobacion_pagador,
    )

    crear_o_reutilizar_aprobacion_pagador(gate, actor=actor)
    return gate


@transaction.atomic
def devolver_a_revision(gate, *, actor, motivo, comentario_interno):
    _exigir_permiso(actor, 'contractors.can_decide_contractor_internal_approval')
    gate = _bloquear_gate(gate.pk)
    if gate.estado not in {
        AprobacionInternaPrestador.Estado.PENDIENTE,
        AprobacionInternaPrestador.Estado.EN_ANALISIS,
    }:
        raise ValidationError('La aprobacion interna no esta disponible para devolver.')
    _validar_motivo_comentario(motivo, comentario_interno)
    return _devolver_gate_a_revision(
        gate,
        actor=actor,
        motivo=motivo,
        comentario=comentario_interno,
    )


@transaction.atomic
def cerrar_sin_originar(gate, *, actor, motivo, comentario_interno):
    _exigir_permiso(actor, 'contractors.can_close_contractor_internal_approval')
    gate = _bloquear_gate(gate.pk)
    if gate.estado not in {
        AprobacionInternaPrestador.Estado.PENDIENTE,
        AprobacionInternaPrestador.Estado.EN_ANALISIS,
        AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR,
    }:
        raise ValidationError('La aprobacion interna ya esta cerrada.')
    _validar_motivo_comentario(motivo, comentario_interno)
    anterior = gate.estado
    gate.estado = AprobacionInternaPrestador.Estado.CERRADA_SIN_ORIGINAR
    gate.decision = AprobacionInternaPrestador.Decision.CERRAR_SIN_ORIGINAR
    gate.motivo = motivo
    gate.comentario_interno = str(comentario_interno).strip()
    gate.decidida_por = actor
    gate.decidida_en = timezone.now()
    gate.save(update_fields=[
        'estado', 'decision', 'motivo', 'comentario_interno', 'decidida_por',
        'decidida_en', 'updated_at',
    ])
    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.CERRADA_SIN_ORIGINAR,
        actor,
        estado_anterior=anterior,
    )
    return gate


@transaction.atomic
def cancelar_aprobacion_interna(gate, *, actor, motivo, comentario_interno):
    _exigir_permiso(actor, 'contractors.can_close_contractor_internal_approval')
    gate = _bloquear_gate(gate.pk)
    if gate.estado not in {
        AprobacionInternaPrestador.Estado.PENDIENTE,
        AprobacionInternaPrestador.Estado.EN_ANALISIS,
    }:
        raise ValidationError('Solo se puede cancelar una aprobacion pendiente o en analisis.')
    _validar_motivo_comentario(motivo, comentario_interno)
    anterior = gate.estado
    gate.estado = AprobacionInternaPrestador.Estado.CANCELADA
    gate.decision = AprobacionInternaPrestador.Decision.CANCELAR
    gate.motivo = motivo
    gate.comentario_interno = str(comentario_interno).strip()
    gate.decidida_por = actor
    gate.decidida_en = timezone.now()
    gate.save(update_fields=[
        'estado', 'decision', 'motivo', 'comentario_interno', 'decidida_por',
        'decidida_en', 'updated_at',
    ])
    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.APROBACION_INTERNA_CANCELADA,
        actor,
        estado_anterior=anterior,
    )
    return gate


def _validar_gate(solicitud, auditoria):
    errores = []
    if auditoria.solicitud_id != solicitud.id:
        errores.append('La auditoria no corresponde a la solicitud.')
    if auditoria.estado_ejecucion != PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA:
        errores.append('La evaluacion formal no esta finalizada.')
    if auditoria.resultado != PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY:
        errores.append('Solo una predecision favorable read-only puede crear el gate.')
    ultima = solicitud.auditorias_predecision.order_by('-created_at', '-id').first()
    if ultima is None or ultima.id != auditoria.id:
        errores.append('La auditoria no es la evaluacion formal mas reciente.')
    if solicitud.revisiones_manuales.filter(estado__in=ESTADOS_REVISION_ACTIVA).exists():
        errores.append('Existe una revision manual activa.')
    if solicitud.requerimientos_subsanacion.filter(
        estado=RequerimientoSubsanacionPrestador.Estado.PENDIENTE
    ).exists():
        errores.append('Existe una subsanacion pendiente.')

    documentos = list(solicitud.documentos.all())
    tipos = {item.tipo_documento for item in documentos if item.archivo}
    if not set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(tipos):
        errores.append('Los documentos obligatorios no estan completos.')

    version_actual, _ = construir_version_datos(solicitud)
    if version_actual != auditoria.version_datos:
        errores.append('Los datos de la solicitud cambiaron despues de la evaluacion.')

    contrato = validar_contrato_prestador(solicitud)
    if (
        contrato.estado != ContractorApplication.EstadoContrato.VIGENTE
        or not contrato.capacidad_automatica
        or contrato.bloqueos
        or contrato.requiere_revision_manual
    ):
        errores.append('El contrato ya no habilita capacidad automatica vigente.')

    try:
        politica = obtener_politica_score_activa()
    except PoliticaScoreNoDisponible:
        politica = None
    if politica is None:
        errores.append('No existe una politica activa valida.')
        configuracion = None
    else:
        configuracion = politica.configuracion_financiera
        if politica.version_politica != auditoria.version_politica:
            errores.append('La politica activa no coincide con la evaluada.')
        if (
            configuracion is None
            or not configuracion.activo
            or not configuracion.version
            or configuracion.version != auditoria.version_configuracion_financiera
        ):
            errores.append('La configuracion financiera no coincide con la evaluada.')
        elif (
            auditoria.tasa_mensual_configuracion != configuracion.tasa_mensual
            or auditoria.monto_maximo_configuracion != configuracion.monto_maximo
            or auditoria.plazo_maximo_configuracion != configuracion.plazo_maximo_meses
        ):
            errores.append('El snapshot financiero de la evaluacion no coincide.')
        if detectar_incompatibilidades_simulador(solicitud, politica):
            errores.append('La simulacion no coincide con la politica evaluada.')

    autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)
    if autorizacion is None:
        errores.append('La autorizacion DataCredito ya no esta vigente.')
    snapshots = _obtener_snapshots_datacredito(auditoria, autorizacion, politica)
    if snapshots is None:
        errores.append('Los snapshots DataCredito requeridos ya no estan vigentes.')

    if errores:
        raise ValidationError(errores)
    limites = _extraer_limites(auditoria, solicitud, politica, configuracion, contrato)
    return ValidacionGatePrestador(
        politica=politica,
        configuracion_financiera=configuracion,
        contrato=contrato,
        autorizacion=autorizacion,
        snapshots_datacredito=snapshots,
        limites=limites,
    )


def _extraer_limites(auditoria, solicitud, politica, configuracion, contrato):
    salida = auditoria.snapshot_salida or {}
    score = salida.get('score_resultado') or {}
    variables = score.get('variables_calculadas') or {}
    banda_nombre = score.get('banda')
    banda = politica.bandas.filter(nombre=banda_nombre).first() if banda_nombre else None

    monto_evaluado = _decimal(salida.get('monto_maximo_sugerido'))
    plazo_evaluado = _entero(salida.get('plazo_maximo_sugerido'))
    monto_score = _decimal(banda.monto_maximo) if banda else None
    plazo_score = int(banda.plazo_maximo) if banda else None
    monto_capacidad = _decimal(variables.get('capacidad_monto_teorica'))
    monto_contrato = _decimal(solicitud.valor_pendiente_cobrar)
    plazo_contrato = int(contrato.meses_financiables)

    montos = [
        _decimal(solicitud.monto_solicitado),
        monto_score,
        _decimal(politica.monto_maximo_politica),
        monto_capacidad,
        monto_contrato,
        _decimal(configuracion.monto_maximo),
    ]
    plazos = [
        int(solicitud.plazo_meses or 0),
        plazo_score,
        int(politica.plazo_maximo_politica),
        plazo_contrato,
        int(configuracion.plazo_maximo_meses),
    ]
    if (
        monto_evaluado is None
        or monto_evaluado <= 0
        or any(valor is None or valor <= 0 for valor in montos)
        or monto_evaluado != min(montos)
    ):
        raise ValidationError('El monto maximo evaluado no tiene trazabilidad completa.')
    if (
        plazo_evaluado is None
        or plazo_evaluado <= 0
        or any(valor is None or valor <= 0 for valor in plazos)
        or plazo_evaluado != min(plazos)
    ):
        raise ValidationError('El plazo maximo evaluado no tiene trazabilidad completa.')
    return {
        'monto_score': monto_score,
        'monto_politica': _decimal(politica.monto_maximo_politica),
        'monto_capacidad': monto_capacidad,
        'monto_contrato': monto_contrato,
        'monto_financiero': _decimal(configuracion.monto_maximo),
        'monto_evaluado': monto_evaluado,
        'plazo_score': plazo_score,
        'plazo_politica': int(politica.plazo_maximo_politica),
        'plazo_contrato': plazo_contrato,
        'plazo_financiero': int(configuracion.plazo_maximo_meses),
        'plazo_evaluado': plazo_evaluado,
    }


def _motivos_revalidacion(gate):
    try:
        _validar_gate(gate.solicitud, gate.auditoria_predecision)
    except ValidationError as exc:
        textos = ' '.join(str(item) for item in exc.messages).lower()
        reglas = (
            ('datos', AprobacionInternaPrestador.Motivo.DATOS_MODIFICADOS),
            ('contrato', AprobacionInternaPrestador.Motivo.CONTRATO_NO_VIGENTE),
            ('convenio', AprobacionInternaPrestador.Motivo.EMPRESA_SIN_CONVENIO),
            ('politica activa', AprobacionInternaPrestador.Motivo.POLITICA_CAMBIO),
            ('configuracion financiera', AprobacionInternaPrestador.Motivo.CONFIGURACION_FINANCIERA_CAMBIO),
            ('simulacion', AprobacionInternaPrestador.Motivo.SIMULACION_INVALIDA),
            ('autorizacion datacredito', AprobacionInternaPrestador.Motivo.AUTORIZACION_NO_VIGENTE),
            ('snapshot datacredito', AprobacionInternaPrestador.Motivo.SNAPSHOT_NO_VIGENTE),
            ('revision manual', AprobacionInternaPrestador.Motivo.REVISION_ACTIVA),
            ('subsanacion', AprobacionInternaPrestador.Motivo.SUBSANACION_PENDIENTE),
        )
        motivos = [motivo for texto, motivo in reglas if texto in textos]
        return motivos or [AprobacionInternaPrestador.Motivo.OTRA_VALIDACION_CONTROLADA]
    return []


def _devolver_gate_a_revision(gate, *, actor, motivo, comentario):
    from contractors.services.revision_manual import crear_o_reutilizar_revision

    anterior = gate.estado
    revision, _ = crear_o_reutilizar_revision(
        solicitud=gate.solicitud,
        auditoria=gate.auditoria_predecision,
        motivo=_motivo_revision(motivo),
        usuario=actor,
        prioridad=RevisionManualPrestador.Prioridad.ALTA,
    )
    solicitud = gate.solicitud
    solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    solicitud.save(update_fields=['estado', 'updated_at'])
    gate.estado = AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION
    gate.decision = AprobacionInternaPrestador.Decision.DEVOLVER_A_REVISION
    gate.motivo = motivo
    gate.comentario_interno = str(comentario or '').strip()
    gate.revision_manual = revision
    gate.decidida_por = actor
    gate.decidida_en = timezone.now()
    gate.save(update_fields=[
        'estado', 'decision', 'motivo', 'comentario_interno', 'revision_manual',
        'decidida_por', 'decidida_en', 'updated_at',
    ])
    _registrar_evento_gate(
        gate,
        TimelinePrestador.TipoEvento.DEVUELTA_A_REVISION,
        actor,
        estado_anterior=anterior,
    )
    return gate


def _obtener_snapshots_datacredito(auditoria, autorizacion, politica):
    if autorizacion is None:
        return None

    snapshots = []
    referencias = []
    if auditoria.snapshot_midecisor_id:
        referencias.append((
            auditoria.snapshot_midecisor,
            ConsultaDatacreditoSnapshot.Servicio.DECISOR,
        ))
    if auditoria.snapshot_hdcplus_id:
        referencias.append((
            auditoria.snapshot_hdcplus,
            ConsultaDatacreditoSnapshot.Servicio.HISTORIAL,
        ))

    if referencias:
        requeridos = set()
        if politica and politica.requiere_midecisor:
            requeridos.add(ConsultaDatacreditoSnapshot.Servicio.DECISOR)
        if politica and politica.requiere_hdcplus:
            requeridos.add(ConsultaDatacreditoSnapshot.Servicio.HISTORIAL)
        servicios_presentes = {servicio for _snapshot, servicio in referencias}
        if not requeridos.issubset(servicios_presentes):
            return None
        for snapshot, servicio in referencias:
            if not _snapshot_vigente(snapshot, autorizacion, servicio=servicio):
                return None
            snapshots.append(snapshot)
        return tuple(snapshots)

    # Compatibilidad con auditorias V1 que guardaban una sola referencia
    # sanitizada dentro de snapshot_salida.
    datos = (auditoria.snapshot_salida or {}).get('datacredito') or {}
    snapshot_id = datos.get('snapshot_id')
    if not snapshot_id:
        return None
    try:
        snapshot = ConsultaDatacreditoSnapshot.objects.filter(pk=snapshot_id).first()
    except (ValidationError, ValueError):
        return None
    if not _snapshot_vigente(snapshot, autorizacion):
        return None
    return (snapshot,)


def _snapshot_vigente(snapshot, autorizacion, *, servicio=None):
    return bool(
        snapshot is not None
        and snapshot.estado == ConsultaDatacreditoSnapshot.Estado.EXITOSO
        and snapshot.vigente_hasta > timezone.now()
        and snapshot.autorizacion_referencia == str(autorizacion.pk)
        and (servicio is None or snapshot.servicio == servicio)
    )


def _bloquear_gate(gate_id):
    return (
        AprobacionInternaPrestador.objects.select_for_update()
        .select_related('solicitud', 'solicitud__empresa', 'auditoria_predecision')
        .get(pk=gate_id)
    )


def _registrar_evento_gate(gate, tipo_evento, actor, *, estado_anterior):
    registrar_evento_timeline_prestador(
        solicitud=gate.solicitud,
        tipo_evento=tipo_evento,
        titulo=TimelinePrestador.TipoEvento(tipo_evento).label,
        descripcion='Se actualizo el gate interno previo a originacion.',
        metadata={
            'gate_id': gate.id,
            'auditoria_id': gate.auditoria_predecision_id,
            'actor_id': actor.id,
            'estado_anterior': estado_anterior,
            'estado_nuevo': gate.estado,
            'version_datos': gate.version_datos,
            'version_politica': gate.version_politica,
            'version_configuracion_financiera': gate.version_configuracion_financiera,
            'monto_autorizado': format(gate.monto_autorizado, '.2f'),
            'plazo_autorizado': gate.plazo_autorizado,
        },
        usuario=actor,
    )


def _motivo_revision(motivo):
    mapa = {
        AprobacionInternaPrestador.Motivo.DATOS_MODIFICADOS: RevisionManualPrestador.Motivo.DATOS_MODIFICADOS,
        AprobacionInternaPrestador.Motivo.CONTRATO_NO_VIGENTE: RevisionManualPrestador.Motivo.CONTRATO_VENCIDO,
        AprobacionInternaPrestador.Motivo.EMPRESA_SIN_CONVENIO: RevisionManualPrestador.Motivo.VALIDACION_EMPRESA_REQUERIDA,
        AprobacionInternaPrestador.Motivo.POLITICA_CAMBIO: RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE,
        AprobacionInternaPrestador.Motivo.CONFIGURACION_FINANCIERA_CAMBIO: RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE,
        AprobacionInternaPrestador.Motivo.SIMULACION_INVALIDA: RevisionManualPrestador.Motivo.POLITICA_INCOMPATIBLE,
        AprobacionInternaPrestador.Motivo.AUTORIZACION_NO_VIGENTE: RevisionManualPrestador.Motivo.DATACREDITO_NO_DISPONIBLE,
        AprobacionInternaPrestador.Motivo.SNAPSHOT_NO_VIGENTE: RevisionManualPrestador.Motivo.DATACREDITO_NO_DISPONIBLE,
        AprobacionInternaPrestador.Motivo.REVISION_ACTIVA: RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        AprobacionInternaPrestador.Motivo.SUBSANACION_PENDIENTE: RevisionManualPrestador.Motivo.DOCUMENTOS_INCOMPLETOS,
    }
    return mapa.get(motivo, RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA)


def _validar_motivo_comentario(motivo, comentario):
    if motivo not in AprobacionInternaPrestador.Motivo.values:
        raise ValidationError('El motivo no esta permitido.')
    if not str(comentario or '').strip():
        raise ValidationError('Debes registrar un comentario interno.')


def _exigir_permiso(usuario, permiso):
    if hasattr(usuario, 'perfil_pagador'):
        raise PermissionDenied(
            'Los perfiles pagadores no pueden decidir aprobaciones internas de prestadores.'
        )
    if (
        not getattr(usuario, 'is_authenticated', False)
        or not usuario.is_staff
        or not usuario.has_perm(permiso)
    ):
        raise PermissionDenied('No tienes permiso para realizar esta accion.')


def _decimal(valor, default=None):
    valor = default if valor in (None, '') else valor
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('El valor monetario no es valido.') from exc


def _entero(valor, default=None):
    valor = default if valor in (None, '') else valor
    if valor in (None, ''):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError) as exc:
        raise ValidationError('El plazo no es valido.') from exc

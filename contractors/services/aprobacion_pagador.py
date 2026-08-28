from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    AprobacionPagadorPrestador,
    ContractorApplication,
    TimelinePrestador,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.validacion_contractual import validar_contrato_prestador


CONFIRMACIONES_REQUERIDAS = (
    'confirma_vinculo',
    'confirma_contrato_vigente',
    'confirma_forma_pago_mensual',
    'confirma_valores_contractuales',
    'confirma_capacidad_operativa',
    'acepta_gestionar_pago',
)


@dataclass(frozen=True)
class ResultadoAprobacionPagadorPrestador:
    aprobacion: AprobacionPagadorPrestador
    reutilizada: bool


@transaction.atomic
def crear_o_reutilizar_aprobacion_pagador(gate, *, actor=None):
    gate = (
        AprobacionInternaPrestador.objects.select_for_update()
        .select_related('solicitud', 'solicitud__empresa')
        .get(pk=gate.pk)
    )
    if gate.estado != AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
        raise ValidationError('La aprobación interna aún no habilita al pagador.')
    version_actual, _ = construir_version_datos(gate.solicitud)
    if version_actual != gate.version_datos:
        raise ValidationError('Los datos cambiaron después de la aprobación interna.')
    aprobacion, creada = AprobacionPagadorPrestador.objects.get_or_create(
        aprobacion_interna=gate,
        defaults={
            'solicitud': gate.solicitud,
            'empresa': gate.solicitud.empresa,
            'version_datos': gate.version_datos,
            'snapshot_condiciones': _snapshot_condiciones(gate),
        },
    )
    if aprobacion.estado == AprobacionPagadorPrestador.Estado.INVALIDADA:
        raise ValidationError('La aprobación de empresa fue invalidada por cambios de datos.')
    if creada:
        gate.solicitud.estado = (
            ContractorApplication.Estado.PENDIENTE_APROBACION_PAGADOR
        )
        gate.solicitud.save(update_fields=['estado', 'updated_at'])
        _registrar_evento(
            aprobacion,
            TimelinePrestador.TipoEvento.APROBACION_PAGADOR_PENDIENTE,
            actor,
        )
    return ResultadoAprobacionPagadorPrestador(aprobacion, not creada)


def decidir_aprobacion_pagador_prestador(
    aprobacion,
    *,
    actor,
    decision,
    motivo='',
    observacion='',
    confirmaciones=None,
):
    resultado = _decidir_aprobacion_pagador_prestador_transaccional(
        aprobacion,
        actor=actor,
        decision=decision,
        motivo=motivo,
        observacion=observacion,
        confirmaciones=confirmaciones,
    )
    if isinstance(resultado, ValidationError):
        raise resultado
    return resultado


@transaction.atomic
def _decidir_aprobacion_pagador_prestador_transaccional(
    aprobacion,
    *,
    actor,
    decision,
    motivo='',
    observacion='',
    confirmaciones=None,
):
    perfil = _exigir_pagador(actor)
    aprobacion = (
        AprobacionPagadorPrestador.objects.select_for_update()
        .select_related('solicitud', 'empresa', 'aprobacion_interna')
        .get(pk=aprobacion.pk)
    )
    if aprobacion.empresa_id != perfil.empresa_id:
        raise PermissionDenied('La solicitud pertenece a otra empresa.')
    decision = str(decision or '').upper()
    if decision not in {
        AprobacionPagadorPrestador.Estado.APROBADO,
        AprobacionPagadorPrestador.Estado.RECHAZADO,
        AprobacionPagadorPrestador.Estado.REQUIERE_AJUSTE,
    }:
        raise ValidationError('La decisión del pagador no es válida.')
    if aprobacion.estado == decision:
        return ResultadoAprobacionPagadorPrestador(aprobacion, True)
    if aprobacion.estado != AprobacionPagadorPrestador.Estado.PENDIENTE:
        raise ValidationError('La aprobación del pagador ya tiene una decisión final.')

    version_actual, _ = construir_version_datos(aprobacion.solicitud)
    if version_actual != aprobacion.version_datos:
        aprobacion.estado = AprobacionPagadorPrestador.Estado.INVALIDADA
        aprobacion.motivo = AprobacionPagadorPrestador.Motivo.DATOS_MODIFICADOS
        aprobacion.save(update_fields=['estado', 'motivo', 'updated_at'])
        aprobacion.solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
        aprobacion.solicitud.save(update_fields=['estado', 'updated_at'])
        return ValidationError(
            'Los datos cambiaron; se requiere una nueva evaluación.'
        )

    valores = {
        campo: bool((confirmaciones or {}).get(campo))
        for campo in CONFIRMACIONES_REQUERIDAS
    }
    if decision == AprobacionPagadorPrestador.Estado.APROBADO:
        faltantes = [campo for campo, confirmado in valores.items() if not confirmado]
        if faltantes:
            raise ValidationError(
                'Debes confirmar todos los hechos contractuales y operativos.'
            )
        contrato = validar_contrato_prestador(aprobacion.solicitud)
        if (
            contrato.bloqueos
            or contrato.requiere_revision_manual
            or not contrato.forma_pago_mensual
            or not contrato.capacidad_automatica
        ):
            raise ValidationError(
                'El expediente contractual ya no permite aprobación operativa.'
            )
        motivo = AprobacionPagadorPrestador.Motivo.CONFIRMACION_COMPLETA
    elif not motivo:
        raise ValidationError('Selecciona un motivo para la decisión.')

    for campo, confirmado in valores.items():
        setattr(aprobacion, campo, confirmado)
    aprobacion.estado = decision
    aprobacion.motivo = motivo
    aprobacion.observacion = str(observacion or '').strip()[:2000]
    aprobacion.decidida_por = actor
    aprobacion.decidida_en = timezone.now()
    aprobacion.save(update_fields=[
        'estado', 'motivo', 'observacion', *CONFIRMACIONES_REQUERIDAS,
        'decidida_por', 'decidida_en', 'updated_at',
    ])

    solicitud = aprobacion.solicitud
    if decision == AprobacionPagadorPrestador.Estado.APROBADO:
        solicitud.estado = ContractorApplication.Estado.APROBADO_POR_PAGADOR
    elif decision == AprobacionPagadorPrestador.Estado.RECHAZADO:
        solicitud.estado = ContractorApplication.Estado.NO_APROBADO
    else:
        solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
    solicitud.save(update_fields=['estado', 'updated_at'])
    _registrar_evento(
        aprobacion,
        TimelinePrestador.TipoEvento.APROBACION_PAGADOR_REGISTRADA,
        actor,
    )
    return ResultadoAprobacionPagadorPrestador(aprobacion, False)


def validar_aprobacion_pagador_vigente(gate):
    aprobacion = getattr(gate, 'aprobacion_pagador', None)
    if (
        aprobacion is None
        or aprobacion.estado != AprobacionPagadorPrestador.Estado.APROBADO
    ):
        raise ValidationError('Falta la aprobación final del pagador.')
    version_actual, _ = construir_version_datos(gate.solicitud)
    if version_actual != aprobacion.version_datos:
        raise ValidationError('La aprobación del pagador no corresponde a los datos vigentes.')
    return aprobacion


def _exigir_pagador(actor):
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied('Debes iniciar sesión como pagador.')
    try:
        perfil = actor.perfil_pagador
    except (AttributeError, ObjectDoesNotExist):
        raise PermissionDenied('El usuario no tiene perfil pagador.') from None
    if (
        not actor.is_active
        or not perfil.es_pagador
        or not actor.has_perm('contractors.can_decide_contractor_payer_approval')
    ):
        raise PermissionDenied('No tienes permiso para decidir esta solicitud.')
    return perfil


def _snapshot_condiciones(gate):
    solicitud = gate.solicitud
    return {
        'solicitud_id': solicitud.id,
        'empresa_id': solicitud.empresa_id,
        'forma_pago': solicitud.forma_pago,
        'forma_pago_mensual': (
            solicitud.forma_pago == ContractorApplication.FormaPago.MENSUAL
        ),
        'fecha_fin_contrato': (
            solicitud.fecha_fin_contrato.isoformat()
            if solicitud.fecha_fin_contrato else None
        ),
        'valor_mensual_contractual': (
            format(solicitud.valor_mensual_contractual, 'f')
            if solicitud.valor_mensual_contractual is not None else None
        ),
        'valor_pendiente_cobrar': (
            format(solicitud.valor_pendiente_cobrar, 'f')
            if solicitud.valor_pendiente_cobrar is not None else None
        ),
        'monto_autorizado': format(gate.monto_autorizado, 'f'),
        'plazo_autorizado': gate.plazo_autorizado,
    }


def _registrar_evento(aprobacion, tipo_evento, actor):
    return registrar_evento_timeline_prestador(
        solicitud=aprobacion.solicitud,
        tipo_evento=tipo_evento,
        titulo=TimelinePrestador.TipoEvento(tipo_evento).label,
        descripcion='Evento controlado de aprobación operativa de la empresa.',
        metadata={
            'aprobacion_pagador_id': aprobacion.id,
            'empresa_id': aprobacion.empresa_id,
            'estado': aprobacion.estado,
        },
        visible_cliente=True,
        usuario=actor,
    )

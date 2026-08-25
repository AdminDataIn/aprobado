import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction

from contractors.models import AprobacionInternaPrestador, TimelinePrestador
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.aprobacion_pagador import validar_aprobacion_pagador_vigente
from contractors.services.expediente_originacion import (
    construir_expediente_originacion_prestador,
)
from gestion_creditos.services.originacion_libranza import (
    construir_clave_idempotencia_prestador,
    originar_libranza_desde_expediente,
)


logger = logging.getLogger(__name__)


def originar_credito_prestador_desde_gate(gate, *, actor):
    _exigir_permiso(actor)
    try:
        with transaction.atomic():
            gate_bloqueado = (
                AprobacionInternaPrestador.objects.select_for_update()
                .select_related(
                    'solicitud',
                    'auditoria_predecision',
                    'aprobacion_pagador',
                )
                .get(pk=gate.pk)
            )
            validar_aprobacion_pagador_vigente(gate_bloqueado)
            expediente = construir_expediente_originacion_prestador(gate_bloqueado)
            clave = construir_clave_idempotencia_prestador(expediente)
            _registrar_evento(
                gate_bloqueado,
                TimelinePrestador.TipoEvento.ORIGINACION_INICIADA,
                actor,
                metadata={'gate_id': gate_bloqueado.id, 'actor_id': actor.id},
            )
            resultado = originar_libranza_desde_expediente(
                expediente,
                clave_idempotencia=clave,
                actor=actor,
            )
            tipo_evento = (
                TimelinePrestador.TipoEvento.ORIGINACION_REUTILIZADA
                if resultado.reutilizado
                else TimelinePrestador.TipoEvento.ORIGINACION_COMPLETADA
            )
            _registrar_evento(
                gate_bloqueado,
                tipo_evento,
                actor,
                metadata={
                    'gate_id': gate_bloqueado.id,
                    'origin_id': resultado.origen.id,
                    'credito_id': resultado.credito.id,
                    'actor_id': actor.id,
                    'estado': resultado.credito.estado,
                    'reutilizado': resultado.reutilizado,
                },
            )
            return resultado
    except Exception:
        _registrar_error_controlado(gate, actor)
        raise


def _registrar_error_controlado(gate, actor):
    try:
        gate_actual = AprobacionInternaPrestador.objects.select_related(
            'solicitud'
        ).get(pk=gate.pk)
        _registrar_evento(
            gate_actual,
            TimelinePrestador.TipoEvento.ORIGINACION_ERROR_CONTROLADO,
            actor,
            metadata={
                'gate_id': gate_actual.id,
                'actor_id': getattr(actor, 'id', None),
                'estado': gate_actual.estado,
            },
        )
    except Exception:
        logger.exception(
            'No fue posible registrar el error de originacion para gate_id=%s',
            getattr(gate, 'pk', None),
        )


def _registrar_evento(gate, tipo_evento, actor, *, metadata):
    return registrar_evento_timeline_prestador(
        solicitud=gate.solicitud,
        tipo_evento=tipo_evento,
        titulo=TimelinePrestador.TipoEvento(tipo_evento).label,
        descripcion='Evento operativo controlado de originacion.',
        metadata=metadata,
        visible_cliente=False,
        usuario=actor,
    )


def _exigir_permiso(actor):
    if (
        actor is None
        or not actor.is_authenticated
        or not actor.is_staff
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm('contractors.can_originate_contractor_credit')
    ):
        raise PermissionDenied('No tienes permiso para originar este credito.')

from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import Credito, HistorialEstado, Pagare


MOTIVO_ANULACION_ERROR_DATOS = (
    'Solicitud anulada por error de correo antes de nueva solicitud.'
)

ESTADOS_ANULABLES_POR_ERROR_DATOS = frozenset(
    {
        Credito.EstadoCredito.SOLICITUD,
        Credito.EstadoCredito.EN_REVISION,
        Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
        Credito.EstadoCredito.APROBADO_PAGADOR,
        Credito.EstadoCredito.APROBADO,
        Credito.EstadoCredito.PENDIENTE_FIRMA,
    }
)


@dataclass(frozen=True)
class ResultadoAnulacionCredito:
    credito_id: int
    numero_credito: str
    estado_anterior: str
    estado_nuevo: str
    pagare_id: Optional[int]
    pagare_estado_anterior: Optional[str]
    pagare_estado_nuevo: Optional[str]
    motivo: str
    ya_estaba_anulado: bool = False


def _resultado(credito, pagare, estado_anterior, pagare_estado_anterior, motivo, *, ya_anulado=False):
    return ResultadoAnulacionCredito(
        credito_id=credito.pk,
        numero_credito=credito.numero_credito,
        estado_anterior=estado_anterior,
        estado_nuevo=credito.estado,
        pagare_id=pagare.pk if pagare else None,
        pagare_estado_anterior=pagare_estado_anterior,
        pagare_estado_nuevo=pagare.estado if pagare else None,
        motivo=motivo,
        ya_estaba_anulado=ya_anulado,
    )


@transaction.atomic
def anular_credito_por_error_datos(*, credito, actor, motivo, cancelar_pagare=True):
    if not getattr(credito, 'pk', None):
        raise ValidationError('El credito debe existir antes de ser anulado.')

    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError('Debes registrar el motivo de la anulacion.')

    credito_bloqueado = (
        Credito.objects
        .select_for_update(of=('self',))
        .get(pk=credito.pk)
    )
    if credito_bloqueado.linea != Credito.LineaCredito.LIBRANZA:
        raise ValidationError('Esta anulacion administrativa solo aplica a creditos de libranza.')

    pagare = (
        Pagare.objects
        .select_for_update()
        .filter(credito_id=credito_bloqueado.pk)
        .first()
    )
    pagare_estado_anterior = pagare.estado if pagare else None

    if credito_bloqueado.estado == Credito.EstadoCredito.ANULADO:
        return _resultado(
            credito_bloqueado,
            pagare,
            Credito.EstadoCredito.ANULADO,
            pagare_estado_anterior,
            motivo,
            ya_anulado=True,
        )

    estado_anterior = credito_bloqueado.estado
    if estado_anterior not in ESTADOS_ANULABLES_POR_ERROR_DATOS:
        raise ValidationError(
            f'No se puede anular un credito en estado {credito_bloqueado.get_estado_display()}.'
        )

    if cancelar_pagare and pagare and pagare.estado in {
        Pagare.EstadoPagare.CREATED,
        Pagare.EstadoPagare.SENT,
    }:
        evidencias = dict(pagare.evidencias or {})
        evidencias['anulacion_administrativa_credito'] = {
            'credito_id': credito_bloqueado.pk,
            'estado_pagare_anterior': pagare.estado,
            'motivo': motivo,
            'actor_id': getattr(actor, 'pk', None),
            'fecha': timezone.now().isoformat(),
        }
        pagare.estado = Pagare.EstadoPagare.CANCELLED
        pagare.evidencias = evidencias
        pagare.save(update_fields=['estado', 'evidencias'])

    credito_bloqueado.estado = Credito.EstadoCredito.ANULADO
    credito_bloqueado.save(update_fields=['estado', 'fecha_actualizacion'])
    HistorialEstado.objects.create(
        credito=credito_bloqueado,
        estado_anterior=estado_anterior,
        estado_nuevo=Credito.EstadoCredito.ANULADO,
        usuario_modificacion=actor,
        motivo=motivo,
    )

    return _resultado(
        credito_bloqueado,
        pagare,
        estado_anterior,
        pagare_estado_anterior,
        motivo,
    )

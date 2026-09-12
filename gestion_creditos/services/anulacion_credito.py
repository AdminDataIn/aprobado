from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gestion_creditos.models import Credito, DetalleContablePago, HistorialEstado, HistorialPago, Pagare


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
        Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
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
    exigir_actor_anulacion(actor)
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

    validar_ausencia_movimientos(credito_bloqueado)

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


def exigir_actor_anulacion(actor):
    if not (actor is not None and actor.is_authenticated and actor.is_active
            and actor.is_staff and not hasattr(actor, 'perfil_pagador')
            and actor.has_perm('gestion_creditos.change_credito')):
        raise PermissionDenied('Se requiere staff autorizado con permiso change_credito y sin PerfilPagador.')


def validar_ausencia_movimientos(credito):
    # Conservador: cualquier pago registrado requiere revision antes de anular.
    if credito.fecha_desembolso is not None:
        raise ValidationError('El credito tiene un desembolso registrado.')
    if HistorialPago.objects.filter(credito=credito).exists():
        raise ValidationError('El credito tiene pagos registrados; no puede anularse por este flujo.')
    if DetalleContablePago.objects.filter(
        Q(credito=credito) | Q(pago__credito=credito) | Q(cuota__credito=credito)
    ).exists():
        raise ValidationError('El credito tiene contabilidad aplicada; no puede anularse por este flujo.')
    if credito.tabla_amortizacion.filter(Q(pagada=True) | Q(monto_pagado__gt=0)).exists():
        raise ValidationError('El credito tiene una cuota pagada o con pagos parciales.')
    if credito.historial_estados.filter(
        Q(estado_nuevo__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA,
                           Credito.EstadoCredito.PAGADO])
        | (Q(comprobante_pago__isnull=False) & ~Q(comprobante_pago=''))
    ).exists():
        raise ValidationError('Existe evidencia interna de desembolso o activacion previa.')

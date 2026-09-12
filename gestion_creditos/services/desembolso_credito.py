from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from gestion_creditos.models import Credito


@transaction.atomic
def confirmar_desembolso_credito(*, credito, actor, comprobante):
    from gestion_creditos.credit_services import gestionar_cambio_estado_credito

    if not (actor is not None and actor.is_authenticated and actor.is_active
            and actor.is_staff and not hasattr(actor, 'perfil_pagador')):
        raise PermissionDenied('Solo staff interno puede confirmar el desembolso.')
    credito = Credito.objects.select_for_update(of=('self',)).get(pk=credito.pk)
    if credito.estado != Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA:
        raise ValidationError('El credito no esta pendiente de transferencia.')
    if not comprobante:
        raise ValidationError('Es obligatorio adjuntar el comprobante de desembolso.')
    return gestionar_cambio_estado_credito(
        credito=credito, nuevo_estado=Credito.EstadoCredito.ACTIVO,
        motivo='Desembolso confirmado y comprobante adjuntado por el equipo de finanzas.',
        comprobante=comprobante, usuario_modificacion=actor,
    )

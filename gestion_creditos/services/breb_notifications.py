"""Outbox BRE-B: reserve before SMTP; uncertain deliveries never retry blindly."""
import logging

from django.db import connection, transaction
from django.utils import timezone

from gestion_creditos.models import NotificacionPagoBREB, PagoBREB

logger = logging.getLogger(__name__)
Estado = NotificacionPagoBREB.Estado


def _log(evento):
    logger.info('pago_breb_id=%s estado=%s intento=%s codigo=%s',
                evento.pago_breb_id, evento.estado, evento.numero_intentos, evento.codigo_error or 'OK')


@transaction.atomic
def registrar_alerta_interna(pago):
    if pago.estado != PagoBREB.Estado.PENDIENTE_VERIFICACION:
        return None
    evento, nuevo = NotificacionPagoBREB.objects.get_or_create(
        pago_breb=pago,
        tipo_evento=NotificacionPagoBREB.TipoEvento.NUEVO_PENDIENTE,
        canal=NotificacionPagoBREB.Canal.EMAIL_INTERNO,
    )
    if nuevo:
        transaction.on_commit(lambda: encolar_alerta_interna(evento.pk, pago.pk))
    return evento


def encolar_alerta_interna(evento_id, pago_breb_id):
    try:
        from gestion_creditos.tasks import enviar_alerta_breb_interna_task

        enviar_alerta_breb_interna_task.apply_async(args=[evento_id], retry=False)
    except Exception:
        # Publishing can fail after broker acceptance. A worker that already claimed
        # the event must not have its state overwritten by this callback.
        try:
            with transaction.atomic():
                evento = NotificacionPagoBREB.objects.select_for_update().get(pk=evento_id)
                if evento.estado == Estado.PENDIENTE:
                    evento.estado = Estado.FALLIDA
                    evento.codigo_error = 'COLA_NO_CONFIRMADA'
                    evento.save(update_fields=['estado', 'codigo_error'])
                _log(evento)
        except Exception:
            logger.error('pago_breb_id=%s estado=INCIERTA intento=0 codigo=COLA_REGISTRO_FALLIDO', pago_breb_id)


def enviar_alerta_interna(evento_id):
    from gestion_creditos.email_service import construir_alerta_interna_pago_breb

    if connection.in_atomic_block:
        raise RuntimeError('La entrega SMTP debe ejecutarse fuera de una transaccion externa.')
    with transaction.atomic():
        evento = NotificacionPagoBREB.objects.select_for_update().get(pk=evento_id)
        if evento.estado != Estado.PENDIENTE:
            return evento.estado
        evento.numero_intentos += 1
        evento.ultimo_intento_en = timezone.now()
        try:
            mensaje = construir_alerta_interna_pago_breb(evento.pago_breb)
            codigo = '' if mensaje is not None else 'SIN_DESTINATARIOS'
        except Exception:
            mensaje, codigo = None, 'PREPARACION_FALLIDA'
        evento.estado = Estado.FALLIDA if mensaje is None else Estado.INCIERTA
        evento.codigo_error = codigo or 'SMTP_EN_CURSO_O_INTERRUMPIDO'
        evento.save(update_fields=['numero_intentos', 'ultimo_intento_en', 'estado', 'codigo_error'])

    if mensaje is None:
        _log(evento)
        return evento.estado
    # The claim is committed before any SMTP traffic. Even if the worker dies,
    # another delivery of the task cannot send this event again.
    try:
        enviado = mensaje.send(fail_silently=False)
        evento.estado = Estado.ENVIADA if enviado == 1 else Estado.INCIERTA
        evento.codigo_error = '' if enviado == 1 else 'SMTP_SIN_CONFIRMACION'
        evento.enviado_en = timezone.now() if enviado == 1 else None
    except Exception:
        evento.estado = Estado.INCIERTA
        evento.codigo_error = 'SMTP_RESULTADO_INCIERTO'
    try:
        evento.save(update_fields=['estado', 'codigo_error', 'enviado_en'])
    except Exception:
        # Durable state remains INCIERTA if SMTP succeeded but this write failed.
        evento.estado = Estado.INCIERTA
        evento.codigo_error = 'RESULTADO_NO_PERSISTIDO'
    _log(evento)
    return evento.estado


@transaction.atomic
def reencolar_alerta_interna(evento_id):
    evento = NotificacionPagoBREB.objects.select_for_update().get(pk=evento_id)
    if evento.estado not in {Estado.PENDIENTE, Estado.FALLIDA}:
        return False
    evento.estado = Estado.PENDIENTE
    evento.codigo_error = ''
    evento.save(update_fields=['estado', 'codigo_error'])
    transaction.on_commit(lambda: encolar_alerta_interna(evento.pk, evento.pago_breb_id))
    return True

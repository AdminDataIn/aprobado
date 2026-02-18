from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from gestion_creditos.models import MarketplaceItem, MarketplaceItemHistorialEstado, Notificacion
from usuarios.models import PerfilEmpresaMarketing


# Mapa de transiciones permitidas para mantener el flujo consistente.
ALLOWED_MARKETPLACE_TRANSITIONS = {
    MarketplaceItem.EstadoItem.PENDIENTE: {
        MarketplaceItem.EstadoItem.APROBADO,
        MarketplaceItem.EstadoItem.RECHAZADO,
        MarketplaceItem.EstadoItem.INACTIVO,
    },
    MarketplaceItem.EstadoItem.APROBADO: {
        MarketplaceItem.EstadoItem.PENDIENTE,
        MarketplaceItem.EstadoItem.INACTIVO,
    },
    MarketplaceItem.EstadoItem.RECHAZADO: {
        MarketplaceItem.EstadoItem.PENDIENTE,
        MarketplaceItem.EstadoItem.INACTIVO,
    },
    MarketplaceItem.EstadoItem.INACTIVO: {
        MarketplaceItem.EstadoItem.PENDIENTE,
    },
}


def es_transicion_estado_valida(estado_actual, estado_nuevo):
    if estado_actual == estado_nuevo:
        return True
    return estado_nuevo in ALLOWED_MARKETPLACE_TRANSITIONS.get(estado_actual, set())


def registrar_historial_publicacion(item, estado_anterior, estado_nuevo, usuario=None, origen='sistema', comentario=''):
    if estado_anterior == estado_nuevo and estado_anterior:
        return None

    return MarketplaceItemHistorialEstado.objects.create(
        item=item,
        estado_anterior=estado_anterior or '',
        estado_nuevo=estado_nuevo,
        usuario=usuario if usuario and getattr(usuario, 'is_authenticated', False) else None,
        origen=origen,
        comentario=(comentario or '').strip()
    )


def _enviar_notificacion_email_empresa(item, estado_nuevo, comentario=''):
    perfiles = PerfilEmpresaMarketing.objects.filter(
        empresa=item.empresa,
        activo=True,
        usuario__is_active=True
    ).select_related('usuario')

    asunto = f"Marketplace: publicacion {item.get_estado_display().lower()}"

    for perfil in perfiles:
        if not perfil.usuario.email:
            continue
        context = {
            'empresa': item.empresa,
            'item': item,
            'estado_nuevo': estado_nuevo,
            'estado_nuevo_display': item.get_estado_display(),
            'comentario': comentario,
            'usuario': perfil.usuario,
        }
        html_content = render_to_string('emails/marketplace_estado_publicacion.html', context)
        text_content = (
            f"Hola {perfil.usuario.get_username()},\n\n"
            f"Tu publicacion '{item.titulo}' cambio a estado: {item.get_estado_display()}.\n"
            f"{'Motivo: ' + comentario if comentario else ''}\n\n"
            f"Empresa: {item.empresa.nombre}\n"
        )

        email = EmailMultiAlternatives(
            subject=asunto,
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[perfil.usuario.email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send(fail_silently=True)


def _crear_notificacion_interna_empresa(item, estado_nuevo, comentario=''):
    perfiles = PerfilEmpresaMarketing.objects.filter(
        empresa=item.empresa,
        activo=True,
        usuario__is_active=True
    ).select_related('usuario')

    if estado_nuevo == MarketplaceItem.EstadoItem.APROBADO:
        titulo = "Publicacion aprobada"
    elif estado_nuevo == MarketplaceItem.EstadoItem.RECHAZADO:
        titulo = "Publicacion rechazada"
    else:
        titulo = "Actualizacion de publicacion"

    mensaje = f"'{item.titulo}' ahora esta en estado {item.get_estado_display()}."
    if comentario:
        mensaje = f"{mensaje} Motivo: {comentario}"

    for perfil in perfiles:
        Notificacion.objects.create(
            usuario=perfil.usuario,
            tipo=Notificacion.TipoNotificacion.SISTEMA,
            titulo=titulo,
            mensaje=mensaje,
            url='/marketplace/panel/'
        )


def notificar_empresa_estado_publicacion(item, estado_nuevo, comentario=''):
    if estado_nuevo not in {MarketplaceItem.EstadoItem.APROBADO, MarketplaceItem.EstadoItem.RECHAZADO}:
        return
    _crear_notificacion_interna_empresa(item, estado_nuevo, comentario=comentario)
    _enviar_notificacion_email_empresa(item, estado_nuevo, comentario=comentario)


def cambiar_estado_publicacion(item, estado_nuevo, usuario=None, origen='sistema', comentario='', require_comment=False):
    estado_anterior = item.estado
    comentario = (comentario or '').strip()

    if require_comment and not comentario:
        raise ValidationError("Debe ingresar un motivo para rechazar la publicacion.")

    if not es_transicion_estado_valida(estado_anterior, estado_nuevo):
        raise ValidationError(
            f"Transicion invalida: {estado_anterior} -> {estado_nuevo} para la publicacion '{item.titulo}'."
        )

    if estado_anterior == estado_nuevo:
        return item

    item.estado = estado_nuevo
    update_fields = ['estado']
    if estado_nuevo == MarketplaceItem.EstadoItem.APROBADO and not item.fecha_publicacion:
        item.fecha_publicacion = timezone.now()
        update_fields.append('fecha_publicacion')
    item.save(update_fields=update_fields)

    registrar_historial_publicacion(
        item=item,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        usuario=usuario,
        origen=origen,
        comentario=comentario
    )
    notificar_empresa_estado_publicacion(item, estado_nuevo, comentario=comentario)
    return item

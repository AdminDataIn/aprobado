"""
Context processors para usuarios.
"""
from django.conf import settings


def user_groups_processor(request):
    """
    Context processor para agregar los grupos del usuario al contexto.
    """
    es_empleado = False
    if request.user.is_authenticated:
        es_empleado = request.user.groups.filter(name='Empleados').exists()

    return {
        'es_empleado': es_empleado
    }


def notificaciones_processor(request):
    """
    Context processor para agregar las notificaciones del usuario al contexto.
    Incluye el conteo de notificaciones no leídas y las últimas 5 notificaciones.
    """
    if request.user.is_authenticated:
        from gestion_creditos.models import Notificacion

        # Obtener notificaciones no leídas
        notificaciones_no_leidas = Notificacion.objects.filter(
            usuario=request.user,
            leida=False
        ).order_by('-fecha_creacion')[:5]

        # Contar total de notificaciones no leídas
        count_notificaciones = notificaciones_no_leidas.count()

        return {
            'notificaciones_no_leidas': notificaciones_no_leidas,
            'count_notificaciones': count_notificaciones,
        }

    return {
        'notificaciones_no_leidas': [],
        'count_notificaciones': 0,
    }


def producto_context_processor(request):
    """
    Context processor para agregar el producto actual (LIBRANZA o EMPRENDIMIENTO)
    al contexto de todos los templates.

    El producto es detectado automáticamente por el middleware ProductoContextMiddleware
    y guardado en la sesión del usuario.

    Esto permite que los templates usen logout URLs dinámicos y otros elementos
    específicos del producto sin necesidad de consultar la base de datos.
    """
    producto_actual = request.session.get('producto_actual', 'EMPRENDIMIENTO')

    return {
        'producto_actual': producto_actual,
        'es_libranza': producto_actual == 'LIBRANZA',
    }


def public_whatsapp_processor(request):
    """
    Expone configuracion del boton flotante de WhatsApp para superficies publicas.
    La visibilidad fina queda controlada por los templates base publicos.
    """
    numero_normalizado = _normalizar_whatsapp(getattr(settings, 'WHATSAPP_SUPPORT_NUMBER', ''))

    return {
        'whatsapp_support_number': numero_normalizado,
        'whatsapp_support_href': f'https://wa.me/{numero_normalizado}' if numero_normalizado else '',
        'whatsapp_support_display': _formatear_whatsapp(numero_normalizado),
        'whatsapp_default_message': getattr(settings, 'WHATSAPP_DEFAULT_MESSAGE', ''),
        'whatsapp_floating_enabled': bool(
            getattr(settings, 'WHATSAPP_FLOATING_ENABLED', False) and numero_normalizado
        ),
    }


def _normalizar_whatsapp(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if not digits:
        return ''
    if len(digits) == 10:
        return f'57{digits}'
    if len(digits) == 12 and digits.startswith('57'):
        return digits
    return digits


def _formatear_whatsapp(digits):
    if not digits:
        return ''
    if len(digits) == 12 and digits.startswith('57'):
        country = digits[:2]
        local = digits[2:]
        if len(local) == 10:
            return f'+{country} {local[:3]} {local[3:6]} {local[6:]}'
    return f'+{digits}'

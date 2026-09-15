from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

from gestion_creditos.services.documentos_privados import resolver_documento, respuesta_documental


@require_safe
def descargar_documento(request, recurso, objeto_id, tipo):
    try:
        if not request.user.is_authenticated or not request.user.is_active:
            raise PermissionDenied
        response = respuesta_documental(resolver_documento(request.user, recurso, objeto_id, tipo))
    except (PermissionDenied, Http404):
        # No confirmar existencia de objetos ajenos; tampoco cachear errores.
        response = HttpResponse(status=404)
    response['Cache-Control'] = 'private, no-store'
    return response

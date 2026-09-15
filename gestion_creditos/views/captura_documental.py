from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from gestion_creditos.models import CapturaDocumentoIdentidad, Credito, SesionCapturaDocumental
from gestion_creditos.services import captura_documental as servicio


def privado(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except (ValidationError, PermissionDenied):
            response = JsonResponse({'error': 'Operacion documental no autorizada o sesion no vigente.'}, status=400)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return wrapped


def _parametros(request, producto, sesion_id=None):
    contexto = request.POST.get('solicitud_id') or request.GET.get('solicitud_id') or None
    if contexto:
        try:
            contexto = int(contexto)
        except ValueError as exc:
            raise ValidationError('Contexto invalido.') from exc
    parametros = {'actor': request.user, 'producto': producto, 'solicitud_id': contexto}
    if sesion_id:
        parametros['sesion_id'] = sesion_id
    return parametros


def _enlace(sesion, token):
    url = reverse('captura:continuar', kwargs={'producto': sesion.producto, 'sesion_id': sesion.pk})
    if sesion.solicitud_id:
        url += f'?solicitud_id={sesion.solicitud_id}'
    return {'id': str(sesion.pk), 'enlace': f'{url}#{token}', 'expira_en': sesion.expira_en.isoformat()}


@privado
@login_required
@require_POST
def crear(request, producto):
    from gestion_creditos.views.common import _rate_limit_simple
    if not _rate_limit_simple(request, 'captura-crear', limit=15, window=600):
        return JsonResponse({'error': 'Espera antes de crear otro enlace.'}, status=429)
    sesion, token = servicio.crear_sesion(**_parametros(request, producto))
    return JsonResponse(_enlace(sesion, token), status=201)


@privado
@login_required
@require_GET
def continuar(request, producto, sesion_id):
    datos = servicio.estado_publico(**_parametros(request, producto, sesion_id))
    return render(request, 'gestion_creditos/captura_continuacion.html', {
        'sesion': datos, 'producto': producto, 'solicitud_id': request.GET.get('solicitud_id', ''),
    })


@privado
@login_required
@require_GET
def estado(request, producto, sesion_id):
    return JsonResponse(servicio.estado_publico(**_parametros(request, producto, sesion_id)))


@privado
@login_required
@require_POST
@sensitive_post_parameters('token')
def operar(request, producto, sesion_id, accion):
    parametros = _parametros(request, producto, sesion_id)
    if not request.session.session_key:
        request.session.save()
    vinculo = request.session.session_key
    if accion == 'canjear':
        servicio.canjear_sesion(**parametros, token=request.POST.get('token', ''), vinculo=vinculo)
    elif accion == 'regenerar':
        sesion, token = servicio.regenerar_enlace(**parametros)
        return JsonResponse(_enlace(sesion, token))
    elif accion == 'revocar':
        servicio.revocar_sesion(**parametros)
    elif accion == 'finalizar':
        servicio.finalizar_sesion(**parametros, vinculo=vinculo)
    elif accion in {'FRONTAL', 'TRASERA'}:
        servicio.recibir_captura(**parametros, lado=accion, vinculo=vinculo, archivo=request.FILES.get('archivo'))
    else:
        raise Http404
    return JsonResponse(servicio.estado_publico(**parametros))


def _staff_documental(usuario):
    return (usuario.is_active and usuario.is_staff and not hasattr(usuario, 'perfil_pagador')
            and usuario.has_perm('gestion_creditos.view_identity_documents'))


def respuesta_archivo(archivo, lado):
    from gestion_creditos.services.documentos_privados import respuesta_documental
    response = respuesta_documental(archivo, nombre_base=f'cedula-{lado.lower()}')
    response['Content-Disposition'] = response['Content-Disposition'].replace('inline;', 'attachment;', 1)
    return response


@privado
@login_required
@require_GET
def descargar_captura(request, captura_id):
    captura = get_object_or_404(CapturaDocumentoIdentidad.objects.select_related('sesion'), pk=captura_id,
                                purgado_en__isnull=True)
    if captura.sesion.usuario_id != request.user.pk and not _staff_documental(request.user):
        raise Http404
    if hasattr(request.user, 'perfil_pagador'):
        raise Http404
    return respuesta_archivo(captura.archivo, captura.lado)


@privado
@login_required
@require_GET
def descargar_cedula_credito(request, credito_id, lado):
    credito = get_object_or_404(Credito.objects.select_related('detalle_libranza'), pk=credito_id, linea='LIBRANZA')
    if lado not in {'FRONTAL', 'TRASERA'} or not getattr(credito, 'detalle_libranza', None):
        raise Http404
    permitido = credito.usuario_id == request.user.pk or _staff_documental(request.user)
    perfil = getattr(request.user, 'perfil_pagador', None)
    if perfil:
        permitido = perfil.es_pagador and perfil.empresa_id == credito.detalle_libranza.empresa_id
    if not permitido:
        raise Http404
    campo = 'cedula_frontal' if lado == 'FRONTAL' else 'cedula_trasera'
    return respuesta_archivo(getattr(credito.detalle_libranza, campo), lado)

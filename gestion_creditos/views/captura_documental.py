from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
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
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return wrapped


def _parametros(request, producto, sesion_id=None):
    contexto = _contexto_id(request)
    parametros = {'actor': request.user, 'producto': producto, 'solicitud_id': contexto}
    if sesion_id:
        parametros['sesion_id'] = sesion_id
    return parametros


def _contexto_id(request):
    contexto = request.POST.get('solicitud_id') or request.GET.get('solicitud_id') or None
    if contexto:
        try:
            contexto = int(contexto)
        except ValueError as exc:
            raise ValidationError('Contexto invalido.') from exc
    return contexto


def _enlace(sesion, token):
    url = reverse('captura:movil', kwargs={'producto': sesion.producto, 'sesion_id': sesion.pk})
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
        'capture_base': reverse('captura:continuar', args=[producto, sesion_id]),
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


CAPTURE_GRANT_COOKIE = '__Secure-capture_grant_token'


def _borrar_cookie_grant(response, producto, sesion_id):
    response.delete_cookie(CAPTURE_GRANT_COOKIE, path=reverse('captura:movil', args=[producto, sesion_id]),
                           samesite='Strict')
    response.cookies[CAPTURE_GRANT_COOKIE]['secure'] = True
    response.cookies[CAPTURE_GRANT_COOKIE]['httponly'] = True


def respuesta_movil(view):
    @wraps(view)
    def wrapped(request, producto, sesion_id, *args, **kwargs):
        try:
            response = view(request, producto, sesion_id, *args, **kwargs)
        except PermissionDenied:
            response = JsonResponse({'code': 'CAPTURE_GRANT_INVALID', 'error': 'Captura no disponible.'}, status=403)
            _borrar_cookie_grant(response, producto, sesion_id)
        except ValidationError:
            response = JsonResponse({'code': 'CAPTURE_INVALID', 'error': 'No se pudo completar la captura.'}, status=400)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return wrapped


@respuesta_movil
@require_GET
@ensure_csrf_cookie
def movil(request, producto, sesion_id):
    if producto not in SesionCapturaDocumental.Producto.values:
        raise Http404
    # A neutral shell deliberately does not query the session or reveal its existence.
    return render(request, 'gestion_creditos/captura_continuacion.html', {
        'sesion': {'id': str(sesion_id)}, 'producto': producto,
        'solicitud_id': request.GET.get('solicitud_id', ''), 'capture_grant': True,
        'capture_base': reverse('captura:movil', args=[producto, sesion_id]),
    })


@respuesta_movil
@require_POST
@sensitive_post_parameters('token')
@sensitive_variables()
def canjear_movil(request, producto, sesion_id):
    from gestion_creditos.views.common import _rate_limit_simple
    if not _rate_limit_simple(request, 'captura-canje', limit=20, window=600):
        return JsonResponse({'error': 'Espera antes de volver a intentar.'}, status=429)
    try:
        contexto = _contexto_id(request)
    except ValidationError as exc:
        raise PermissionDenied from exc
    sesion, secreto = servicio.canjear_capture_grant(
        sesion_id=sesion_id, producto=producto, token=request.POST.get('token', ''), solicitud_id=contexto)
    response = JsonResponse({'id': str(sesion.pk), 'estado': sesion.estado, 'lados': []})
    response.set_cookie(CAPTURE_GRANT_COOKIE, secreto,
                        max_age=max(0, int((sesion.capture_grant_expira_en - timezone.now()).total_seconds())),
                        secure=True, httponly=True, samesite='Strict',
                        path=reverse('captura:movil', args=[producto, sesion_id]))
    return response


def _operar_movil(request, producto, sesion_id, accion):
    try:
        contexto = _contexto_id(request)
    except ValidationError as exc:
        raise PermissionDenied from exc
    datos = servicio.operar_capture_grant(
        sesion_id=sesion_id, producto=producto, solicitud_id=contexto,
        grant=request.COOKIES.get(CAPTURE_GRANT_COOKIE, ''), accion=accion, archivo=request.FILES.get('archivo'))
    response = JsonResponse(datos)
    if datos['estado'] == 'FINALIZADA':
        _borrar_cookie_grant(response, producto, sesion_id)
    return response


@respuesta_movil
@require_GET
@sensitive_variables()
def estado_movil(request, producto, sesion_id):
    return _operar_movil(request, producto, sesion_id, 'estado')


@respuesta_movil
@require_POST
@sensitive_variables()
def operar_movil(request, producto, sesion_id, accion):
    from gestion_creditos.views.common import _rate_limit_simple
    if not _rate_limit_simple(request, 'captura-upload', limit=60, window=600):
        return JsonResponse({'error': 'Espera antes de volver a intentar.'}, status=429)
    return _operar_movil(request, producto, sesion_id, accion)


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

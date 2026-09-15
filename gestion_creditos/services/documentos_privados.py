"""Acceso documental por objeto; nunca por una ruta proporcionada por el cliente."""

from pathlib import Path
import re
from urllib.parse import quote

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import content_disposition_header


# Solo campos expresamente clasificados como privados. No incluye imagenes/logos publicos.
DOCUMENTOS = {
    'libranza': ('gestion_creditos.CreditoLibranza', (
        'cedula_frontal', 'cedula_trasera', 'certificado_laboral',
        'contrato_prestacion_servicios', 'desprendible_nomina', 'certificado_bancario')),
    'emprendimiento': ('gestion_creditos.CreditoEmprendimiento', ('foto_negocio',)),
    'imagen-negocio': ('gestion_creditos.ImagenNegocio', ('imagen',)),
    'pago': ('gestion_creditos.HistorialPago', ('comprobante',)),
    'lote': ('gestion_creditos.LotePagoEmpresa', ('archivo', 'comprobante')),
    'historial': ('gestion_creditos.HistorialEstado', ('comprobante_pago',)),
    'pagare': ('gestion_creditos.Pagare', ('archivo_pdf', 'archivo_pdf_firmado')),
    'movimiento': ('gestion_creditos.MovimientoAhorro', ('comprobante',)),
    'comision': ('gestion_creditos.PagoComisionEjecutivo', ('comprobante',)),
    'prestador': ('contractors.ContractorApplicationDocument', ('archivo',)),
    'video-marketplace': ('gestion_creditos.MarketplaceItem', ('video',)),
}


def recurso_documental(modelo, campo):
    return next((recurso for recurso, (label, campos) in DOCUMENTOS.items()
                 if modelo._meta.label_lower == label.lower() and campo in campos), None)


def url_documento(archivo):
    if not archivo or not archivo.instance.pk:
        return ''
    recurso = recurso_documental(type(archivo.instance), archivo.field.name)
    if not recurso:
        return ''
    return reverse('documentos:descargar', kwargs={
        'recurso': recurso, 'objeto_id': archivo.instance.pk, 'tipo': archivo.field.name,
    })


def exigir_acceso(usuario, *, propietario_id=None, empresa_id=None, permisos=()):
    if not usuario.is_authenticated or not usuario.is_active:
        raise PermissionDenied
    perfil = getattr(usuario, 'perfil_pagador', None)
    # Un perfil pagador nunca obtiene acceso transversal por ownership/permisos accidentales.
    if perfil is not None:
        if perfil.es_pagador and empresa_id and perfil.empresa_id == empresa_id:
            return
        raise PermissionDenied
    if propietario_id and usuario.pk == propietario_id:
        return
    if usuario.is_staff and any(usuario.has_perm(permiso) for permiso in permisos):
        return
    raise PermissionDenied


def exigir_acceso_credito(usuario, credito, permisos=(), identidad=False):
    detalle = getattr(credito, 'detalle_libranza', None)
    adelanto = getattr(credito, 'detalle_adelanto_nomina', None) if detalle is None else None
    empresa_id = detalle.empresa_id if detalle else (
        adelanto.vinculo_laboral.empresa_id if adelanto else None)
    permisos_staff = ('gestion_creditos.view_identity_documents',) if identidad else (
        'gestion_creditos.view_credito',) + tuple(permisos)
    exigir_acceso(usuario, propietario_id=credito.usuario_id,
                  empresa_id=empresa_id,
                  permisos=permisos_staff)


def resolver_documento(usuario, recurso, objeto_id, tipo):
    especificacion = DOCUMENTOS.get(recurso)
    if especificacion is None or tipo not in especificacion[1]:
        raise Http404('Documento no encontrado.')
    objeto = get_object_or_404(apps.get_model(especificacion[0]), pk=objeto_id)
    permiso = f'{objeto._meta.app_label}.view_{objeto._meta.model_name}'
    if recurso in {'libranza', 'emprendimiento', 'pago', 'historial', 'pagare'}:
        exigir_acceso_credito(usuario, objeto.credito, (permiso,),
                              identidad=recurso == 'libranza' and tipo in {'cedula_frontal', 'cedula_trasera'})
    elif recurso == 'imagen-negocio':
        exigir_acceso_credito(usuario, objeto.credito_emprendimiento.credito, (permiso,))
    elif recurso == 'lote':
        exigir_acceso(usuario, empresa_id=objeto.empresa_id, permisos=(permiso,))
    elif recurso == 'movimiento':
        exigir_acceso(usuario, propietario_id=objeto.cuenta.usuario_id, permisos=(permiso,))
    elif recurso == 'comision':
        exigir_acceso(usuario, propietario_id=objeto.asesor.usuario_id if objeto.asesor.activo else None,
                      permisos=(permiso,))
    elif recurso == 'prestador':
        # Antes de originacion, la revision contractual sigue siendo interna.
        exigir_acceso(usuario, propietario_id=objeto.solicitud.usuario_id,
                      permisos=('contractors.can_view_contractor_review_queue',))
    elif recurso == 'video-marketplace':
        perfil = getattr(usuario, 'perfil_marketing', None)
        propietario = usuario.pk if perfil and perfil.activo and perfil.empresa_id == objeto.empresa_id else None
        exigir_acceso(usuario, propietario_id=propietario, permisos=(permiso,))
    return getattr(objeto, tipo)


def ruta_documental(archivo):
    """Valida tambien referencias historicas y enlaces simbolicos antes de abrir."""
    if not archivo:
        raise Http404('Documento no disponible.')
    try:
        ruta = Path(archivo.path).resolve(strict=True)
        roots = (Path(settings.MEDIA_ROOT).resolve(), Path(settings.PRIVATE_DOCUMENTS_ROOT).resolve())
        if not ruta.is_file() or not any(ruta.is_relative_to(root) for root in roots):
            raise Http404('Documento no disponible.')
        return ruta
    except (OSError, ValueError, SuspiciousFileOperation):
        raise Http404('Documento no disponible.') from None


def respuesta_documental(archivo, nombre_base='documento'):
    ruta = ruta_documental(archivo)
    extension = ruta.suffix.lower()
    tipos = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
             '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.mp4': 'video/mp4', '.webm': 'video/webm'}
    content_type = tipos.get(extension, 'application/octet-stream')
    if not re.fullmatch(r'[a-z0-9-]{1,60}', nombre_base):
        nombre_base = 'documento'
    nombre = nombre_base + (extension if extension in tipos else '.bin')
    media = Path(settings.MEDIA_ROOT).resolve()
    if getattr(settings, 'PROTECTED_LEGACY_X_ACCEL', False) and ruta.is_relative_to(media):
        response = HttpResponse(content_type=content_type)
        response['X-Accel-Redirect'] = '/_protected_legacy/' + quote(ruta.relative_to(media).as_posix(), safe='/')
    else:
        # Local/UAT sin Nginx, y archivos privados fuera del alcance de su worker.
        try:
            response = FileResponse(ruta.open('rb'), content_type=content_type)
        except OSError:
            raise Http404('Documento no disponible.') from None
    response['Content-Disposition'] = content_disposition_header(extension not in tipos, nombre)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'; frame-ancestors 'self'"
    return response

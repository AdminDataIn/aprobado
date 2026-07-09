from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from contractors.forms import CambiarEstadoPrestadorForm
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
)
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar
from contractors.views import calcular_progreso_documental


def staff_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


@staff_required
def bandeja_prestadores_view(request):
    solicitudes = (
        ContractorApplication.objects
        .select_related('usuario', 'empresa')
        .prefetch_related('documentos')
        .order_by('-created_at')
    )
    solicitudes_con_progreso = [
        (solicitud, calcular_progreso_documental(solicitud))
        for solicitud in solicitudes
    ]
    return render(
        request,
        'contractors/admin_bandeja_prestadores.html',
        {'solicitudes_con_progreso': solicitudes_con_progreso},
    )


@staff_required
def detalle_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_staff(solicitud_id)
    if request.method == 'POST':
        form_estado = CambiarEstadoPrestadorForm(request.POST)
        if form_estado.is_valid():
            solicitud.estado = form_estado.cleaned_data['estado']
            solicitud.save(update_fields=['estado', 'updated_at'])
            messages.success(request, 'Estado actualizado correctamente.')
            return redirect('contractors:admin_detalle', solicitud_id=solicitud.id)
    else:
        form_estado = CambiarEstadoPrestadorForm(initial={'estado': solicitud.estado})

    documentos = {
        documento.tipo_documento: documento
        for documento in solicitud.documentos.all()
    }
    etiquetas = dict(ContractorApplicationDocument.TipoDocumento.choices)
    estado_documentos = [
        (tipo, etiquetas.get(tipo, tipo), documentos.get(tipo))
        for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR
    ]
    documentos_pendientes = [
        etiquetas.get(tipo, tipo)
        for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR
        if tipo not in documentos
    ]
    progreso_documental = calcular_progreso_documental(solicitud)
    simulacion_preliminar = evaluar_capacidad_contractual_preliminar(
        solicitud,
        documentos_completos=progreso_documental['completo'],
    )

    return render(
        request,
        'contractors/admin_detalle_prestador.html',
        {
            'solicitud': solicitud,
            'form_estado': form_estado,
            'estado_documentos': estado_documentos,
            'documentos_pendientes': documentos_pendientes,
            'progreso_documental': progreso_documental,
            'simulacion_preliminar': simulacion_preliminar,
        },
    )


@staff_required
def descargar_documento_prestador_staff_view(request, documento_id):
    try:
        documento = ContractorApplicationDocument.objects.select_related('solicitud').get(id=documento_id)
    except ContractorApplicationDocument.DoesNotExist as exc:
        raise Http404('Documento no encontrado.') from exc

    return FileResponse(
        documento.archivo.open('rb'),
        as_attachment=False,
        filename=documento.archivo.name.split('/')[-1],
    )


def _obtener_solicitud_staff(solicitud_id):
    try:
        return (
            ContractorApplication.objects
            .select_related('usuario', 'empresa')
            .prefetch_related('documentos')
            .get(id=solicitud_id)
        )
    except ContractorApplication.DoesNotExist as exc:
        raise Http404('Solicitud no encontrada.') from exc

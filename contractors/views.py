from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from contractors.forms import DocumentoPrestadorForm, SolicitudPrestadorForm
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
)
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar


def inicio_prestadores_view(request):
    return redirect('contractors:solicitar')


@login_required
def solicitar_prestador_view(request):
    if request.method == 'POST':
        form = SolicitudPrestadorForm(request.POST)
        if form.is_valid():
            solicitud = form.save(commit=False)
            solicitud.usuario = request.user
            solicitud.estado = ContractorApplication.Estado.DOCUMENTOS_PENDIENTES
            solicitud.save()
            messages.success(request, 'Solicitud registrada. Continua con la carga de documentos.')
            return redirect('contractors:documentos', solicitud_id=solicitud.id)
    else:
        form = SolicitudPrestadorForm()

    return render(
        request,
        'contractors/solicitud_prestador.html',
        {'form': form},
    )


@login_required
def documentos_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)

    if request.method == 'POST':
        form = DocumentoPrestadorForm(request.POST, request.FILES)
        if form.is_valid():
            documento, _created = ContractorApplicationDocument.objects.get_or_create(
                solicitud=solicitud,
                tipo_documento=form.cleaned_data['tipo_documento'],
                defaults={'uploaded_by': request.user},
            )
            documento.archivo = form.cleaned_data['archivo']
            documento.uploaded_by = request.user
            documento.full_clean()
            documento.save()
            _actualizar_estado_documental(solicitud)
            messages.success(request, 'Documento cargado correctamente.')
            return redirect('contractors:documentos', solicitud_id=solicitud.id)
    else:
        form = DocumentoPrestadorForm()

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
        tipo for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR if tipo not in documentos
    ]

    return render(
        request,
        'contractors/documentos_prestador.html',
        {
            'solicitud': solicitud,
            'form': form,
            'documentos': documentos,
            'estado_documentos': estado_documentos,
            'documentos_pendientes': documentos_pendientes,
            'documentos_obligatorios': DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
            'progreso_documental': calcular_progreso_documental(solicitud),
        },
    )


@login_required
def descargar_documento_prestador_view(request, solicitud_id, documento_id):
    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)
    try:
        documento = solicitud.documentos.get(id=documento_id)
    except ContractorApplicationDocument.DoesNotExist as exc:
        raise Http404('Documento no encontrado.') from exc

    return FileResponse(
        documento.archivo.open('rb'),
        as_attachment=False,
        filename=documento.archivo.name.split('/')[-1],
    )


@login_required
def simular_prestador_view(request):
    solicitud_id = request.GET.get('solicitud_id')
    if not solicitud_id:
        messages.info(request, 'Primero registra tu solicitud para habilitar la simulacion.')
        return redirect('contractors:solicitar')

    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)
    documentos_cargados = _solicitud_tiene_documentos_obligatorios(solicitud)
    progreso_documental = calcular_progreso_documental(solicitud)
    simulacion_preliminar = evaluar_capacidad_contractual_preliminar(
        solicitud,
        documentos_completos=documentos_cargados,
    )

    return render(
        request,
        'contractors/simulador_prestador.html',
        {
            'solicitud': solicitud,
            'documentos_cargados': documentos_cargados,
            'progreso_documental': progreso_documental,
            'simulacion_preliminar': simulacion_preliminar,
        },
    )


@login_required
def mi_credito_prestador_view(request):
    solicitudes = (
        ContractorApplication.objects
        .filter(usuario=request.user)
        .select_related('empresa')
        .prefetch_related('documentos')
    )
    solicitudes_con_progreso = [
        (solicitud, calcular_progreso_documental(solicitud))
        for solicitud in solicitudes
    ]
    return render(
        request,
        'contractors/mi_credito_prestador.html',
        {'solicitudes_con_progreso': solicitudes_con_progreso},
    )


def _obtener_solicitud_del_usuario(solicitud_id, usuario):
    try:
        return ContractorApplication.objects.select_related('empresa', 'usuario').get(
            id=solicitud_id,
            usuario=usuario,
        )
    except ContractorApplication.DoesNotExist as exc:
        raise Http404('Solicitud no encontrada.') from exc


def _solicitud_tiene_documentos_obligatorios(solicitud):
    tipos_cargados = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    return set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(tipos_cargados)


def _actualizar_estado_documental(solicitud):
    if _solicitud_tiene_documentos_obligatorios(solicitud):
        solicitud.estado = ContractorApplication.Estado.DOCUMENTOS_CARGADOS
        solicitud.save(update_fields=['estado', 'updated_at'])


def calcular_progreso_documental(solicitud):
    documentos = getattr(solicitud, '_prefetched_objects_cache', {}).get('documentos')
    if documentos is None:
        tipos_cargados = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    else:
        tipos_cargados = {documento.tipo_documento for documento in documentos}
    total = len(DOCUMENTOS_OBLIGATORIOS_PRESTADOR)
    cargados = len(set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).intersection(tipos_cargados))
    porcentaje = int((cargados / total) * 100) if total else 0
    return {
        'cargados': cargados,
        'total': total,
        'porcentaje': porcentaje,
        'completo': cargados == total,
    }

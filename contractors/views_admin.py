from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from contractors.forms import AccionRevisionPrestadorForm
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
)
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar
from contractors.services.revision_manual import (
    asignar_revision,
    cancelar_revision,
    iniciar_analisis_revision,
    reintentar_evaluacion,
    resolver_revision,
    solicitar_subsanacion,
    solicitar_validacion_empresa,
    usuarios_asignables_revision,
)
from contractors.views import calcular_progreso_documental


def permiso_interno_requerido(codename):
    def decorador(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if (
                not request.user.is_staff
                or hasattr(request.user, 'perfil_pagador')
                or not request.user.has_perm(codename)
            ):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorador


@permiso_interno_requerido('contractors.can_view_contractor_review_queue')
def bandeja_prestadores_view(request):
    revisiones = (
        RevisionManualPrestador.objects
        .select_related(
            'solicitud', 'solicitud__empresa', 'solicitud__usuario',
            'auditoria_predecision', 'asignado_a',
        )
        .order_by('-creada_en', '-id')
    )
    filtros = {
        'estado': request.GET.get('estado', '').strip(),
        'motivo': request.GET.get('motivo', '').strip(),
        'prioridad': request.GET.get('prioridad', '').strip(),
        'empresa': request.GET.get('empresa', '').strip(),
        'asignado_a': request.GET.get('asignado_a', '').strip(),
        'fecha_desde': request.GET.get('fecha_desde', '').strip(),
        'fecha_hasta': request.GET.get('fecha_hasta', '').strip(),
        'resultado': request.GET.get('resultado', '').strip(),
        'estado_solicitud': request.GET.get('estado_solicitud', '').strip(),
    }
    for campo in ('estado', 'motivo', 'prioridad'):
        if filtros[campo]:
            revisiones = revisiones.filter(**{campo: filtros[campo]})
    if filtros['empresa']:
        revisiones = revisiones.filter(solicitud__empresa_id=filtros['empresa'])
    if filtros['asignado_a']:
        revisiones = revisiones.filter(asignado_a_id=filtros['asignado_a'])
    fecha_desde = parse_date(filtros['fecha_desde'])
    fecha_hasta = parse_date(filtros['fecha_hasta'])
    if fecha_desde:
        revisiones = revisiones.filter(creada_en__date__gte=fecha_desde)
    if fecha_hasta:
        revisiones = revisiones.filter(creada_en__date__lte=fecha_hasta)
    if filtros['resultado']:
        revisiones = revisiones.filter(auditoria_predecision__resultado=filtros['resultado'])
    if filtros['estado_solicitud']:
        revisiones = revisiones.filter(solicitud__estado=filtros['estado_solicitud'])

    pagina = Paginator(revisiones, 25).get_page(request.GET.get('page'))
    filas = [
        {
            'revision': revision,
            'solicitante': _enmascarar_nombre(revision.solicitud.nombre_completo),
            'documento': _enmascarar_documento(revision.solicitud.numero_documento),
        }
        for revision in pagina.object_list
    ]
    query = request.GET.copy()
    query.pop('page', None)
    return render(
        request,
        'contractors/admin_bandeja_prestadores.html',
        {
            'pagina': pagina,
            'filas': filas,
            'filtros': filtros,
            'query_sin_pagina': query.urlencode(),
            'estados_revision': RevisionManualPrestador.Estado.choices,
            'motivos_revision': RevisionManualPrestador.Motivo.choices,
            'prioridades_revision': RevisionManualPrestador.Prioridad.choices,
            'resultados_predecision': PredecisionPrestadorAudit.Resultado.choices,
            'estados_solicitud': ContractorApplication.Estado.choices,
            'usuarios_asignables': usuarios_asignables_revision(),
        },
    )


@permiso_interno_requerido('contractors.can_view_contractor_review_queue')
def detalle_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_staff(solicitud_id)
    documentos = {documento.tipo_documento: documento for documento in solicitud.documentos.all()}
    etiquetas = dict(ContractorApplicationDocument.TipoDocumento.choices)
    estado_documentos = [
        (tipo, etiquetas.get(tipo, tipo), documentos.get(tipo))
        for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR
    ]
    progreso = calcular_progreso_documental(solicitud)
    auditoria = solicitud.auditorias_predecision.order_by('-created_at', '-id').first()
    puede_ver_score = request.user.has_perm('contractors.can_view_contractor_score_details')
    revisiones = solicitud.revisiones_manuales.select_related(
        'asignado_a', 'creada_por', 'resuelta_por', 'auditoria_predecision'
    ).prefetch_related('requerimientos_subsanacion')
    return render(
        request,
        'contractors/admin_detalle_prestador.html',
        {
            'solicitud': solicitud,
            'documento_enmascarado': _enmascarar_documento(solicitud.numero_documento),
            'estado_documentos': estado_documentos,
            'documentos_pendientes': [
                etiquetas.get(tipo, tipo)
                for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR
                if tipo not in documentos
            ],
            'progreso_documental': progreso,
            'simulacion_preliminar': evaluar_capacidad_contractual_preliminar(
                solicitud,
                documentos_completos=progreso['completo'],
            ),
            'analisis_contractual_detalle': _construir_resumen_analisis_contractual(
                solicitud
            ),
            'auditoria': auditoria,
            'auditoria_detalle': _construir_detalle_auditoria(auditoria, puede_ver_score),
            'revisiones': revisiones,
            'requerimientos': solicitud.requerimientos_subsanacion.select_related(
                'revision', 'creado_por'
            ),
            'timeline': solicitud.timeline_operativo.select_related('creado_por')[:100],
            'usuarios_asignables': usuarios_asignables_revision(),
            'tipos_subsanacion': RequerimientoSubsanacionPrestador.Tipo.choices,
            'resultados_revision': RevisionManualPrestador.Resultado.choices,
            'puede_asignar': request.user.has_perm('contractors.can_assign_contractor_review'),
            'puede_resolver': request.user.has_perm('contractors.can_resolve_contractor_review'),
            'puede_solicitar': request.user.has_perm('contractors.can_request_contractor_correction'),
            'puede_ver_score': puede_ver_score,
        },
    )


@require_POST
@permiso_interno_requerido('contractors.can_view_contractor_review_queue')
def accion_revision_prestador_view(request, revision_id):
    revision = get_object_or_404(
        RevisionManualPrestador.objects.select_related('solicitud'),
        pk=revision_id,
    )
    form = AccionRevisionPrestadorForm(
        request.POST,
        usuarios_asignables=usuarios_asignables_revision(),
    )
    if not form.is_valid():
        messages.error(request, 'Revisa los datos de la accion solicitada.')
        return redirect('contractors:admin_detalle', solicitud_id=revision.solicitud_id)

    accion = form.cleaned_data['accion']
    try:
        if accion == AccionRevisionPrestadorForm.Accion.ASIGNAR:
            asignar_revision(
                revision,
                actor=request.user,
                asignado_a=form.cleaned_data.get('asignado_a'),
            )
        elif accion == AccionRevisionPrestadorForm.Accion.INICIAR:
            iniciar_analisis_revision(revision, actor=request.user)
        elif accion == AccionRevisionPrestadorForm.Accion.SOLICITAR_SUBSANACION:
            solicitar_subsanacion(
                revision,
                tipo=form.cleaned_data['tipo_subsanacion'],
                actor=request.user,
                detalle_interno=form.cleaned_data.get('comentario_interno', ''),
            )
        elif accion == AccionRevisionPrestadorForm.Accion.VALIDAR_EMPRESA:
            solicitar_validacion_empresa(
                revision,
                actor=request.user,
                comentario_interno=form.cleaned_data.get('comentario_interno', ''),
            )
        elif accion == AccionRevisionPrestadorForm.Accion.REINTENTAR:
            reintentar_evaluacion(revision, actor=request.user)
        elif accion == AccionRevisionPrestadorForm.Accion.RESOLVER:
            resolver_revision(
                revision,
                resultado=form.cleaned_data['resultado'],
                actor=request.user,
                comentario_interno=form.cleaned_data.get('comentario_interno', ''),
            )
        elif accion == AccionRevisionPrestadorForm.Accion.CANCELAR:
            cancelar_revision(
                revision,
                actor=request.user,
                comentario_interno=form.cleaned_data.get('comentario_interno', ''),
            )
        else:
            raise ValidationError('La accion no esta permitida.')
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'La accion fue registrada correctamente.')
    return redirect('contractors:admin_detalle', solicitud_id=revision.solicitud_id)


@permiso_interno_requerido('contractors.can_view_contractor_review_queue')
def descargar_documento_prestador_staff_view(request, documento_id):
    documento = get_object_or_404(ContractorApplicationDocument, id=documento_id)
    if not documento.archivo:
        raise Http404('Documento no encontrado.')
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


def _enmascarar_documento(documento):
    valor = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum())
    return f"{'*' * max(0, len(valor) - 4)}{valor[-4:]}" if valor else 'No disponible'


def _enmascarar_nombre(nombre):
    partes = [parte for parte in str(nombre or '').split() if parte]
    return ' '.join(f'{parte[0]}.' for parte in partes) if partes else 'Solicitante'


def _construir_detalle_auditoria(auditoria, puede_ver_score):
    if auditoria is None:
        return None
    resumenes = {
        PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY: (
            'La evaluación read-only no registró bloqueos operativos.'
        ),
        PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL: (
            'La solicitud requiere validación interna antes de continuar.'
        ),
        PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY: (
            'La evaluación read-only registró un bloqueo operativo.'
        ),
        PredecisionPrestadorAudit.Resultado.NO_EVALUABLE: (
            'La información disponible no permite completar la evaluación.'
        ),
        PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO: (
            'La evaluación terminó con un error controlado.'
        ),
    }
    detalle = {
        'resultado': auditoria.get_resultado_display(),
        'resumen': resumenes.get(
            auditoria.resultado,
            'Consulta el historial de revisión para continuar.',
        ),
        'razones': [],
        'alertas': [],
        'bloqueos': [],
        'score': None,
    }
    if puede_ver_score:
        detalle.update({
            'razones': _lista_auditoria_controlada(auditoria.razones),
            'alertas': _lista_auditoria_controlada(auditoria.alertas),
            'bloqueos': _lista_auditoria_controlada(auditoria.bloqueos),
            'score': auditoria.score,
        })
    return detalle


def _lista_auditoria_controlada(valores):
    return [str(valor)[:300] for valor in (valores or []) if isinstance(valor, (str, int, float))]


def _construir_resumen_analisis_contractual(solicitud):
    metadata = solicitud.metadata_analisis_contractual or {}
    empresa = metadata.get('empresa_sugerida')
    empresa_segura = None
    if isinstance(empresa, dict):
        empresa_segura = {
            'nombre': str(empresa.get('nombre') or '')[:160],
            'tipo_coincidencia': str(empresa.get('tipo_coincidencia') or '')[:40],
        }
    return {
        'estado': solicitud.get_estado_analisis_contractual_display(),
        'fecha': solicitud.fecha_analisis_contractual,
        'confianza': str(metadata.get('confianza_general') or '0.00')[:12],
        'empresa_sugerida': empresa_segura,
        'advertencias': _lista_auditoria_controlada(metadata.get('advertencias')),
        'bloqueos': _lista_auditoria_controlada(metadata.get('bloqueos')),
    }

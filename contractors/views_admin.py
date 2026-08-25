import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from contractors.forms import (
    AccionAprobacionInternaPrestadorForm,
    AccionRevisionPrestadorForm,
)
from contractors.models import (
    AprobacionInternaPrestador,
    AprobacionPagadorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    FormalizacionCreditoPrestador,
    NovedadOperativaPrestador,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
)
from contractors.services.aprobacion_interna import (
    aprobar_para_originar,
    cancelar_aprobacion_interna,
    cerrar_sin_originar,
    crear_o_reutilizar_aprobacion_interna,
    devolver_a_revision,
    iniciar_analisis_aprobacion_interna,
)
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar
from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
)
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
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
from contractors.services.originacion import originar_credito_prestador_desde_gate
from contractors.services.formalizacion import (
    enviar_formalizacion_prestador_a_firma,
    preparar_formalizacion_credito_prestador,
)
from contractors.services.novedad_operativa import (
    crear_o_reutilizar_novedad_operativa_prestador,
    enviar_novedad_operativa_prestador,
)
from contractors.views import calcular_progreso_documental
from gestion_creditos.models import OrigenCreditoPrestador


logger = logging.getLogger(__name__)


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
    puede_ver_aprobaciones = request.user.has_perm(
        'contractors.can_view_contractor_internal_approval'
    )
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
        'estado_aprobacion': request.GET.get('estado_aprobacion', '').strip(),
        'monto_desde': request.GET.get('monto_desde', '').strip(),
        'monto_hasta': request.GET.get('monto_hasta', '').strip(),
    }
    solicitudes_pendientes = (
        ContractorApplication.objects
        .filter(estado__in={
            ContractorApplication.Estado.EVALUACION_PENDIENTE,
            ContractorApplication.Estado.EN_EVALUACION,
        })
        .select_related('usuario', 'empresa')
        .prefetch_related('documentos')
        .order_by('created_at', 'id')
    )
    if filtros['empresa']:
        solicitudes_pendientes = solicitudes_pendientes.filter(
            empresa_id=filtros['empresa']
        )
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
        solicitudes_pendientes = solicitudes_pendientes.filter(
            created_at__date__gte=fecha_desde
        )
    if fecha_hasta:
        revisiones = revisiones.filter(creada_en__date__lte=fecha_hasta)
        solicitudes_pendientes = solicitudes_pendientes.filter(
            created_at__date__lte=fecha_hasta
        )
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

    pagina_pendientes = Paginator(solicitudes_pendientes, 25).get_page(
        request.GET.get('evaluation_page')
    )
    filas_pendientes = [
        {
            'solicitud': solicitud,
            'solicitante': _enmascarar_nombre(solicitud.nombre_completo),
            'documento': _enmascarar_documento(solicitud.numero_documento),
            'progreso': calcular_progreso_documental(solicitud),
        }
        for solicitud in pagina_pendientes.object_list
    ]
    query_pendientes = request.GET.copy()
    query_pendientes.pop('evaluation_page', None)

    aprobaciones = (
        AprobacionInternaPrestador.objects
        .select_related(
            'solicitud', 'solicitud__empresa', 'auditoria_predecision',
            'creada_por', 'decidida_por',
        )
        .order_by('-creada_en', '-id')
    )
    if filtros['estado_aprobacion']:
        aprobaciones = aprobaciones.filter(estado=filtros['estado_aprobacion'])
    if filtros['empresa']:
        aprobaciones = aprobaciones.filter(solicitud__empresa_id=filtros['empresa'])
    if filtros['asignado_a']:
        aprobaciones = aprobaciones.filter(decidida_por_id=filtros['asignado_a'])
    if fecha_desde:
        aprobaciones = aprobaciones.filter(creada_en__date__gte=fecha_desde)
    if fecha_hasta:
        aprobaciones = aprobaciones.filter(creada_en__date__lte=fecha_hasta)
    if filtros['resultado']:
        aprobaciones = aprobaciones.filter(
            auditoria_predecision__resultado=filtros['resultado']
        )
    try:
        if filtros['monto_desde']:
            aprobaciones = aprobaciones.filter(
                monto_autorizado__gte=filtros['monto_desde']
            )
        if filtros['monto_hasta']:
            aprobaciones = aprobaciones.filter(
                monto_autorizado__lte=filtros['monto_hasta']
            )
    except (TypeError, ValueError, ValidationError):
        aprobaciones = aprobaciones.none()
    pagina_aprobaciones = Paginator(aprobaciones, 25).get_page(
        request.GET.get('approval_page')
    )
    candidatos_gate_qs = (
        PredecisionPrestadorAudit.objects
        .filter(
            resultado=PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA,
            aprobaciones_internas__isnull=True,
        )
        .select_related('solicitud', 'solicitud__empresa')
        .order_by('-created_at', '-id')
    )
    if filtros['empresa']:
        candidatos_gate_qs = candidatos_gate_qs.filter(
            solicitud__empresa_id=filtros['empresa']
        )
    if fecha_desde:
        candidatos_gate_qs = candidatos_gate_qs.filter(created_at__date__gte=fecha_desde)
    if fecha_hasta:
        candidatos_gate_qs = candidatos_gate_qs.filter(created_at__date__lte=fecha_hasta)
    total_candidatos_gate = candidatos_gate_qs.count()
    candidatos_gate = []
    for candidato in candidatos_gate_qs[:100]:
        ultima = candidato.solicitud.auditorias_predecision.order_by(
            '-created_at', '-id'
        ).first()
        if ultima and ultima.id == candidato.id:
            candidatos_gate.append(candidato)
        if len(candidatos_gate) == 25:
            break
    if not puede_ver_aprobaciones:
        pagina_aprobaciones = Paginator(aprobaciones.none(), 25).get_page(1)
        candidatos_gate = []
        total_candidatos_gate = 0
    query_aprobaciones = request.GET.copy()
    query_aprobaciones.pop('approval_page', None)
    return render(
        request,
        'contractors/admin_bandeja_prestadores.html',
        {
            'pagina': pagina,
            'filas': filas,
            'pagina_pendientes': pagina_pendientes,
            'filas_pendientes': filas_pendientes,
            'query_sin_pagina_evaluacion': query_pendientes.urlencode(),
            'filtros': filtros,
            'query_sin_pagina': query.urlencode(),
            'estados_revision': RevisionManualPrestador.Estado.choices,
            'motivos_revision': RevisionManualPrestador.Motivo.choices,
            'prioridades_revision': RevisionManualPrestador.Prioridad.choices,
            'resultados_predecision': PredecisionPrestadorAudit.Resultado.choices,
            'estados_solicitud': ContractorApplication.Estado.choices,
            'usuarios_asignables': usuarios_asignables_revision(),
            'pagina_aprobaciones': pagina_aprobaciones,
            'query_sin_pagina_aprobacion': query_aprobaciones.urlencode(),
            'estados_aprobacion': AprobacionInternaPrestador.Estado.choices,
            'candidatos_gate': candidatos_gate,
            'puede_ver_aprobaciones': puede_ver_aprobaciones,
            'resumen_bandeja': {
                'pendientes': solicitudes_pendientes.count(),
                'revisiones': revisiones.count(),
                'preaprobados': total_candidatos_gate,
                'aprobaciones': aprobaciones.count() if puede_ver_aprobaciones else 0,
            },
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
    autorizacion_datacredito = obtener_autorizacion_datacredito_vigente(solicitud)
    puede_ver_score = request.user.has_perm('contractors.can_view_contractor_score_details')
    revisiones = solicitud.revisiones_manuales.select_related(
        'asignado_a', 'creada_por', 'resuelta_por', 'auditoria_predecision'
    ).prefetch_related('requerimientos_subsanacion')
    aprobacion_interna = solicitud.aprobaciones_internas.select_related(
        'auditoria_predecision', 'revision_manual', 'creada_por', 'decidida_por'
    ).first()
    aprobacion_pagador = (
        AprobacionPagadorPrestador.objects.select_related('decidida_por', 'empresa')
        .filter(solicitud=solicitud)
        .order_by('-created_at', '-id')
        .first()
    )
    origen_credito = None
    if aprobacion_interna:
        origen_credito = OrigenCreditoPrestador.objects.select_related(
            'credito', 'credito_libranza'
        ).filter(gate_id=aprobacion_interna.id).first()
    formalizacion = None
    if origen_credito:
        formalizacion = FormalizacionCreditoPrestador.objects.select_related(
            'pagare', 'credito', 'credito_libranza'
        ).filter(origen_credito_prestador=origen_credito).first()
    novedad_operativa = None
    if formalizacion:
        novedad_operativa = NovedadOperativaPrestador.objects.filter(
            formalizacion=formalizacion
        ).first()
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
            'estado_evaluacion': _construir_estado_evaluacion(solicitud, auditoria),
            'autorizacion_datacredito_vigente': autorizacion_datacredito is not None,
            'puede_ejecutar_evaluacion': bool(
                solicitud.estado == ContractorApplication.Estado.EVALUACION_PENDIENTE
                and request.user.has_perm(
                    'contractors.can_evaluate_contractor_application'
                )
            ),
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
            'aprobacion_interna': aprobacion_interna,
            'aprobacion_pagador': aprobacion_pagador,
            'origen_credito': origen_credito,
            'formalizacion': formalizacion,
            'novedad_operativa': novedad_operativa,
            'motivos_aprobacion': AprobacionInternaPrestador.Motivo.choices,
            'puede_ver_aprobacion': request.user.has_perm(
                'contractors.can_view_contractor_internal_approval'
            ),
            'puede_decidir_aprobacion': request.user.has_perm(
                'contractors.can_decide_contractor_internal_approval'
            ),
            'puede_cerrar_aprobacion': request.user.has_perm(
                'contractors.can_close_contractor_internal_approval'
            ),
            'puede_originar': bool(
                aprobacion_interna
                and aprobacion_interna.estado
                == AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR
                and aprobacion_pagador
                and aprobacion_pagador.estado
                == AprobacionPagadorPrestador.Estado.APROBADO
                and request.user.has_perm(
                    'contractors.can_originate_contractor_credit'
                )
            ),
            'puede_ver_formalizacion': request.user.has_perm(
                'contractors.can_view_contractor_formalization'
            ),
            'puede_preparar_formalizacion': bool(
                origen_credito
                and origen_credito.estado == OrigenCreditoPrestador.Estado.COMPLETADO
                and origen_credito.credito.estado == 'EN_REVISION'
                and aprobacion_pagador
                and aprobacion_pagador.estado
                == AprobacionPagadorPrestador.Estado.APROBADO
                and formalizacion is None
                and request.user.has_perm(
                    'contractors.can_prepare_contractor_formalization'
                )
            ),
            'puede_reintentar_firma': bool(
                formalizacion
                and formalizacion.estado in {
                    FormalizacionCreditoPrestador.Estado.IDENTIDAD_VALIDADA,
                    FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
                }
                and request.user.has_perm(
                    'contractors.can_retry_contractor_signature'
                )
            ),
            'puede_ver_novedad_operativa': request.user.has_perm(
                'contractors.can_view_contractor_operational_notice'
            ),
            'puede_crear_novedad_operativa': bool(
                formalizacion
                and formalizacion.estado
                == FormalizacionCreditoPrestador.Estado.FIRMADO
                and novedad_operativa is None
                and request.user.has_perm(
                    'contractors.can_create_contractor_operational_notice'
                )
            ),
            'puede_enviar_novedad_operativa': bool(
                novedad_operativa
                and (
                    (
                        novedad_operativa.estado
                        == NovedadOperativaPrestador.Estado.PENDIENTE_ENVIO
                        and request.user.has_perm(
                            'contractors.can_create_contractor_operational_notice'
                        )
                    )
                    or (
                        novedad_operativa.estado
                        == NovedadOperativaPrestador.Estado.ERROR_CONTROLADO
                        and request.user.has_perm(
                            'contractors.can_retry_contractor_operational_notice'
                        )
                    )
                )
            ),
            'puede_crear_aprobacion': bool(
                auditoria
                and auditoria.resultado
                == PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
                and aprobacion_interna is None
                and request.user.has_perm(
                    'contractors.can_decide_contractor_internal_approval'
                )
            ),
        },
    )


@require_POST
@permiso_interno_requerido('contractors.can_evaluate_contractor_application')
def ejecutar_evaluacion_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_staff(solicitud_id)
    try:
        resultado = evaluar_solicitud_prestador(
            solicitud,
            solicitado_por=request.user,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        if resultado.en_proceso:
            messages.warning(request, 'La evaluacion ya se encuentra en proceso.')
        elif resultado.reutilizada:
            messages.info(
                request,
                'Se reutilizo la evaluacion vigente; no se realizo una consulta duplicada.',
            )
        elif (
            resultado.auditoria.estado_ejecucion
            == PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO
        ):
            messages.error(
                request,
                'La evaluacion termino con un error controlado. Revisa el detalle.',
            )
        else:
            messages.success(
                request,
                'La evaluacion formal fue ejecutada y auditada.',
            )
    return redirect('contractors:admin_detalle', solicitud_id=solicitud.id)


@require_POST
@permiso_interno_requerido('contractors.can_decide_contractor_internal_approval')
def crear_aprobacion_interna_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_staff(solicitud_id)
    auditoria = solicitud.auditorias_predecision.order_by('-created_at', '-id').first()
    if auditoria is None:
        messages.error(request, 'La solicitud no tiene una evaluacion formal finalizada.')
        return redirect('contractors:admin_detalle', solicitud_id=solicitud.id)
    try:
        _, creada = crear_o_reutilizar_aprobacion_interna(
            auditoria,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'La aprobacion interna fue creada.' if creada
            else 'La aprobacion interna existente fue reutilizada.',
        )
    return redirect('contractors:admin_detalle', solicitud_id=solicitud.id)


@require_POST
@permiso_interno_requerido('contractors.can_view_contractor_internal_approval')
def accion_aprobacion_interna_prestador_view(request, gate_id):
    gate = get_object_or_404(
        AprobacionInternaPrestador.objects.select_related('solicitud'),
        pk=gate_id,
    )
    form = AccionAprobacionInternaPrestadorForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Revisa los datos de la decision interna.')
        return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)
    accion = form.cleaned_data['accion']
    datos = form.cleaned_data
    try:
        if accion == AccionAprobacionInternaPrestadorForm.Accion.INICIAR:
            iniciar_analisis_aprobacion_interna(gate, actor=request.user)
        elif accion == AccionAprobacionInternaPrestadorForm.Accion.APROBAR:
            aprobar_para_originar(
                gate,
                actor=request.user,
                monto_autorizado=datos.get('monto_autorizado'),
                plazo_autorizado=datos.get('plazo_autorizado'),
                comentario_interno=datos.get('comentario_interno', ''),
            )
        elif accion == AccionAprobacionInternaPrestadorForm.Accion.DEVOLVER:
            devolver_a_revision(
                gate,
                actor=request.user,
                motivo=datos['motivo'],
                comentario_interno=datos['comentario_interno'],
            )
        elif accion == AccionAprobacionInternaPrestadorForm.Accion.CERRAR:
            cerrar_sin_originar(
                gate,
                actor=request.user,
                motivo=datos['motivo'],
                comentario_interno=datos['comentario_interno'],
            )
        elif accion == AccionAprobacionInternaPrestadorForm.Accion.CANCELAR:
            cancelar_aprobacion_interna(
                gate,
                actor=request.user,
                motivo=datos['motivo'],
                comentario_interno=datos['comentario_interno'],
            )
        else:
            raise ValidationError('La accion no esta permitida.')
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'La decision interna fue registrada.')
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


@require_POST
@permiso_interno_requerido('contractors.can_originate_contractor_credit')
def originar_credito_prestador_view(request, gate_id):
    gate = get_object_or_404(
        AprobacionInternaPrestador.objects.select_related('solicitud'),
        pk=gate_id,
    )
    try:
        resultado = originar_credito_prestador_desde_gate(
            gate,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    except Exception:
        logger.exception('Error controlado al originar gate_id=%s', gate.id)
        messages.error(
            request,
            'No fue posible completar la originacion. No se crearon obligaciones parciales.',
        )
    else:
        if resultado.reutilizado:
            messages.info(
                request,
                f'La originacion ya existia y se reutilizo el credito '
                f'{resultado.credito.numero_credito}.',
            )
        else:
            messages.success(
                request,
                f'Credito {resultado.credito.numero_credito} originado en revision.',
            )
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


@require_POST
@permiso_interno_requerido('contractors.can_prepare_contractor_formalization')
def preparar_formalizacion_prestador_view(request, origen_id):
    origen = get_object_or_404(
        OrigenCreditoPrestador.objects.select_related('credito'),
        pk=origen_id,
    )
    try:
        resultado = preparar_formalizacion_credito_prestador(
            origen,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'La formalizacion existente fue reutilizada.'
            if resultado.reutilizada
            else 'El pagare fue generado. La identidad debe validarse antes del envio.',
        )
    gate = get_object_or_404(AprobacionInternaPrestador, pk=origen.gate_id)
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


@require_POST
@permiso_interno_requerido('contractors.can_retry_contractor_signature')
def enviar_formalizacion_prestador_view(request, formalizacion_id):
    formalizacion = get_object_or_404(
        FormalizacionCreditoPrestador.objects.select_related(
            'origen_credito_prestador'
        ),
        pk=formalizacion_id,
    )
    try:
        resultado = enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, str(exc))
    except Exception:
        logger.exception(
            'Error controlado al enviar formalizacion_id=%s', formalizacion.id
        )
        messages.error(request, 'No fue posible enviar el documento a firma.')
    else:
        messages.success(
            request,
            'El envio existente fue reutilizado.'
            if resultado.reutilizada
            else 'El documento fue enviado a firma.',
        )
    gate = get_object_or_404(
        AprobacionInternaPrestador,
        pk=formalizacion.origen_credito_prestador.gate_id,
    )
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


@require_POST
@permiso_interno_requerido('contractors.can_create_contractor_operational_notice')
def crear_novedad_operativa_prestador_view(request, formalizacion_id):
    formalizacion = get_object_or_404(
        FormalizacionCreditoPrestador,
        pk=formalizacion_id,
    )
    try:
        resultado = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'La novedad operativa existente fue reutilizada.'
            if resultado.reutilizada
            else 'La novedad operativa fue generada sin modificar el credito.',
        )
    gate = get_object_or_404(
        AprobacionInternaPrestador,
        pk=formalizacion.origen_credito_prestador.gate_id,
    )
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


@require_POST
@permiso_interno_requerido('contractors.can_view_contractor_operational_notice')
def enviar_novedad_operativa_prestador_view(request, novedad_id):
    novedad = get_object_or_404(
        NovedadOperativaPrestador.objects.select_related(
            'formalizacion__origen_credito_prestador'
        ),
        pk=novedad_id,
    )
    try:
        resultado = enviar_novedad_operativa_prestador(
            novedad,
            actor=request.user,
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    except Exception:
        logger.exception('Error controlado al enviar novedad_id=%s', novedad.id)
        messages.error(request, 'No fue posible enviar la novedad operativa.')
    else:
        messages.success(
            request,
            'El envio ya estaba registrado.'
            if resultado.reutilizado
            else f'Novedad enviada a {resultado.destinatarios} destinatario(s).',
        )
    gate = get_object_or_404(
        AprobacionInternaPrestador,
        pk=novedad.formalizacion.origen_credito_prestador.gate_id,
    )
    return redirect('contractors:admin_detalle', solicitud_id=gate.solicitud_id)


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
        'version_politica': auditoria.version_politica,
        'centrales': None,
    }
    if puede_ver_score:
        detalle.update({
            'razones': _lista_auditoria_controlada(auditoria.razones),
            'alertas': _lista_auditoria_controlada(auditoria.alertas),
            'bloqueos': _lista_auditoria_controlada(auditoria.bloqueos),
            'score': auditoria.score,
            'centrales': _construir_resumen_centrales_auditoria(auditoria),
        })
    return detalle


def _construir_resumen_centrales_auditoria(auditoria):
    centrales = (auditoria.snapshot_salida or {}).get('centrales')
    if not isinstance(centrales, dict):
        return None
    return {
        'estado_global': str(centrales.get('estado_global') or '')[:32],
        'completa': bool(centrales.get('completa')),
        'decisor': _fuente_central_allowlist(centrales.get('decisor')),
        'historial': _fuente_central_allowlist(centrales.get('historial')),
    }


def _fuente_central_allowlist(datos):
    if not isinstance(datos, dict):
        return None
    return {
        'estado': str(datos.get('estado') or '')[:24],
        'consultado_en': str(datos.get('consultado_en') or '')[:40],
        'reutilizado': bool(datos.get('reutilizado')),
        'score_externo': datos.get('score_externo'),
        'obligaciones_vigentes': datos.get('obligaciones_vigentes'),
        'obligaciones_en_mora': datos.get('obligaciones_en_mora'),
        'cuota_mensual_total': datos.get('cuota_mensual_total'),
        'saldo_total': datos.get('saldo_total'),
        'saldo_mora': datos.get('saldo_mora'),
        'mora_actual': datos.get('mora_actual'),
        'mora_severa': datos.get('mora_severa'),
    }


def _construir_estado_evaluacion(solicitud, auditoria):
    if solicitud.estado == ContractorApplication.Estado.EN_EVALUACION:
        codigo = PredecisionPrestadorAudit.EstadoEjecucion.EN_PROCESO
    elif auditoria is not None:
        codigo = auditoria.estado_ejecucion
    else:
        codigo = PredecisionPrestadorAudit.EstadoEjecucion.PENDIENTE
    etiquetas = {
        PredecisionPrestadorAudit.EstadoEjecucion.PENDIENTE: 'Pendiente',
        PredecisionPrestadorAudit.EstadoEjecucion.EN_PROCESO: 'En proceso',
        PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA: 'Completada',
        PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO: 'Error controlado',
    }
    return {
        'codigo': codigo,
        'etiqueta': etiquetas.get(codigo, 'No determinada'),
    }


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

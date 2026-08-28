from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from contractors.forms import DecisionPagadorPrestadorForm
from contractors.models import AprobacionPagadorPrestador, NovedadOperativaPrestador
from contractors.services.aprobacion_pagador import (
    decidir_aprobacion_pagador_prestador,
)
from contractors.services.novedad_operativa import (
    confirmar_recepcion_novedad_operativa_prestador,
    construir_dto_novedad_operativa_prestador,
    exigir_pagador_operativo,
    marcar_novedad_operativa_prestador_gestionada,
)
from gestion_creditos.decorators import pagador_required


PERMISO_VER = 'contractors.can_view_contractor_operational_notice'
PERMISO_DECIDIR_PRESTADOR = 'contractors.can_decide_contractor_payer_approval'


@login_required(login_url='/pagador/login/')
@pagador_required
def aprobaciones_prestadores_view(request):
    perfil = request.user.perfil_pagador
    if not request.user.has_perm(PERMISO_DECIDIR_PRESTADOR):
        raise PermissionDenied('No tienes permiso para ver aprobaciones de prestadores.')
    aprobaciones = (
        AprobacionPagadorPrestador.objects
        .filter(empresa=perfil.empresa)
        .select_related('solicitud', 'empresa', 'aprobacion_interna')
        .order_by('-created_at', '-id')
    )
    estado = request.GET.get('estado')
    if estado in AprobacionPagadorPrestador.Estado.values:
        aprobaciones = aprobaciones.filter(estado=estado)
    pagina = Paginator(aprobaciones, 25).get_page(request.GET.get('page'))
    return render(
        request,
        'contractors/pagador_aprobaciones_prestadores.html',
        {'pagina': pagina, 'empresa': perfil.empresa, 'estado_filtro': estado or ''},
    )


@login_required(login_url='/pagador/login/')
@pagador_required
def detalle_aprobacion_prestador_view(request, aprobacion_id):
    perfil = request.user.perfil_pagador
    if not request.user.has_perm(PERMISO_DECIDIR_PRESTADOR):
        raise PermissionDenied('No tienes permiso para ver esta aprobación.')
    aprobacion = _obtener_aprobacion_empresa(aprobacion_id, perfil.empresa_id)
    return render(
        request,
        'contractors/pagador_detalle_aprobacion_prestador.html',
        {
            'aprobacion': aprobacion,
            'solicitud': aprobacion.solicitud,
            'empresa': perfil.empresa,
            'form': DecisionPagadorPrestadorForm(),
        },
    )


@require_POST
@login_required(login_url='/pagador/login/')
@pagador_required
def decidir_aprobacion_prestador_view(request, aprobacion_id):
    aprobacion = _obtener_aprobacion_empresa(aprobacion_id, request.empresa.id)
    form = DecisionPagadorPrestadorForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Revisa la decisión y las confirmaciones.')
    else:
        try:
            resultado = decidir_aprobacion_pagador_prestador(
                aprobacion,
                actor=request.user,
                decision=form.cleaned_data['decision'],
                motivo=form.cleaned_data.get('motivo') or '',
                observacion=form.cleaned_data.get('observacion') or '',
                confirmaciones={
                    campo: form.cleaned_data.get(campo, False)
                    for campo in (
                        'confirma_vinculo',
                        'confirma_contrato_vigente',
                        'confirma_forma_pago_mensual',
                        'confirma_valores_contractuales',
                        'confirma_capacidad_operativa',
                        'acepta_gestionar_pago',
                    )
                },
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                'La decisión ya estaba registrada.'
                if resultado.reutilizada
                else 'Decisión registrada correctamente.',
            )
    return redirect(
        'pagador:prestadores_aprobacion_detalle',
        aprobacion_id=aprobacion.id,
    )


@login_required(login_url='/pagador/login/')
@pagador_required
def novedades_operativas_prestadores_view(request):
    perfil = exigir_pagador_operativo(request.user, permiso=PERMISO_VER)
    novedades = (
        NovedadOperativaPrestador.objects
        .filter(empresa=perfil.empresa)
        .select_related('credito', 'credito_libranza', 'formalizacion', 'empresa')
        .order_by('-created_at', '-id')
    )
    pagina = Paginator(novedades, 25).get_page(request.GET.get('page'))
    filas = [
        {'novedad': novedad, 'dto': construir_dto_novedad_operativa_prestador(novedad)}
        for novedad in pagina.object_list
    ]
    return render(
        request,
        'contractors/pagador_novedades_prestadores.html',
        {'pagina': pagina, 'filas': filas, 'empresa': perfil.empresa},
    )


@login_required(login_url='/pagador/login/')
@pagador_required
def detalle_novedad_operativa_prestador_view(request, novedad_id):
    perfil = exigir_pagador_operativo(request.user, permiso=PERMISO_VER)
    novedad = _obtener_novedad_empresa(novedad_id, perfil.empresa_id)
    return render(
        request,
        'contractors/pagador_detalle_novedad_prestador.html',
        {
            'novedad': novedad,
            'dto': construir_dto_novedad_operativa_prestador(novedad),
            'empresa': perfil.empresa,
            'puede_confirmar_recepcion': (
                novedad.estado == NovedadOperativaPrestador.Estado.ENVIADA
                and request.user.has_perm(
                    'contractors.can_acknowledge_contractor_operational_notice'
                )
            ),
            'puede_marcar_gestionada': (
                novedad.estado == NovedadOperativaPrestador.Estado.RECIBIDA
                and request.user.has_perm(
                    'contractors.can_acknowledge_contractor_operational_notice'
                )
            ),
        },
    )


@require_POST
@login_required(login_url='/pagador/login/')
@pagador_required
def confirmar_recepcion_novedad_prestador_view(request, novedad_id):
    novedad = _obtener_novedad_empresa(novedad_id, request.empresa.id)
    try:
        resultado = confirmar_recepcion_novedad_operativa_prestador(
            novedad,
            actor=request.user,
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'La recepcion ya estaba confirmada.'
            if resultado.reutilizada
            else 'Recepcion confirmada correctamente.',
        )
    return redirect('pagador:prestadores_novedad_detalle', novedad_id=novedad.id)


@require_POST
@login_required(login_url='/pagador/login/')
@pagador_required
def marcar_gestionada_novedad_prestador_view(request, novedad_id):
    novedad = _obtener_novedad_empresa(novedad_id, request.empresa.id)
    try:
        resultado = marcar_novedad_operativa_prestador_gestionada(
            novedad,
            actor=request.user,
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'La gestion ya estaba registrada.'
            if resultado.reutilizada
            else 'La novedad fue marcada como gestionada.',
        )
    return redirect('pagador:prestadores_novedad_detalle', novedad_id=novedad.id)


def _obtener_novedad_empresa(novedad_id, empresa_id):
    return get_object_or_404(
        NovedadOperativaPrestador.objects.select_related(
            'credito', 'credito_libranza', 'formalizacion', 'empresa'
        ),
        pk=novedad_id,
        empresa_id=empresa_id,
    )


def _obtener_aprobacion_empresa(aprobacion_id, empresa_id):
    return get_object_or_404(
        AprobacionPagadorPrestador.objects.select_related(
            'solicitud', 'empresa', 'aprobacion_interna'
        ),
        pk=aprobacion_id,
        empresa_id=empresa_id,
    )

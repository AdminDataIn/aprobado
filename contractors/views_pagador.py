from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from contractors.models import NovedadOperativaPrestador
from contractors.services.novedad_operativa import (
    confirmar_recepcion_novedad_operativa_prestador,
    construir_dto_novedad_operativa_prestador,
    exigir_pagador_operativo,
    marcar_novedad_operativa_prestador_gestionada,
)
from gestion_creditos.decorators import pagador_required


PERMISO_VER = 'contractors.can_view_contractor_operational_notice'


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

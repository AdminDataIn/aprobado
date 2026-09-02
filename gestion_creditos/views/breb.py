import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from gestion_creditos.forms import ReportePagoBREBForm, RevisionPagoBREBForm
from gestion_creditos.models import Credito, Empresa, PagoBREB
from gestion_creditos.services.breb_payments import (
    ESTADOS_CREDITO_REPORTABLES,
    aprobar_pago_breb,
    calcular_monto_sugerido,
    obtener_configuracion_breb_activa,
    rechazar_pago_breb,
    reportar_pago_breb,
    usuario_puede_revisar_pago_breb,
)


def _notificar_breb_despues_commit(nombre_funcion, pago):
    from django.db import transaction
    from gestion_creditos import email_service

    funcion = getattr(email_service, nombre_funcion)
    transaction.on_commit(lambda: funcion(pago))


@login_required
def usuario_reportar_pago_breb_view(request, credito_id):
    credito = get_object_or_404(
        Credito.objects.select_related(
            'usuario', 'detalle_libranza__empresa',
            'detalle_adelanto_nomina__vinculo_laboral__empresa',
        ),
        pk=credito_id,
        usuario=request.user,
        estado__in=ESTADOS_CREDITO_REPORTABLES,
    )
    configuracion = obtener_configuracion_breb_activa()
    if not configuracion:
        messages.info(request, 'El pago por BRE-B no está disponible en este momento.')
        return redirect('libranza:mi_credito_detalle', credito_id=credito.id)

    monto_sugerido = calcular_monto_sugerido(credito)
    if request.method == 'POST':
        form = ReportePagoBREBForm(
            request.POST,
            request.FILES,
            configuracion=configuracion,
            monto_sugerido=monto_sugerido,
        )
        if form.is_valid():
            try:
                pago = reportar_pago_breb(
                    credito=credito,
                    usuario=request.user,
                    configuracion=configuracion,
                    **form.cleaned_data,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                _notificar_breb_despues_commit('enviar_pago_breb_reportado', pago)
                messages.success(
                    request,
                    'Tu pago quedó pendiente de verificación. El comprobante aún no ha sido aplicado.',
                )
                return redirect('libranza:pago_breb', credito_id=credito.id)
    else:
        form = ReportePagoBREBForm(
            configuracion=configuracion,
            monto_sugerido=monto_sugerido,
        )

    reportes = credito.pagos_breb.filter(usuario=request.user).order_by('-creado_en')
    return render(request, 'usuariocreditos/pago_breb.html', {
        'credito': credito,
        'configuracion_breb': configuracion,
        'monto_sugerido': monto_sugerido,
        'form': form,
        'reportes_breb': reportes,
    })


@login_required
def usuario_qr_breb_view(request, credito_id):
    get_object_or_404(
        Credito,
        pk=credito_id,
        usuario=request.user,
        estado__in=ESTADOS_CREDITO_REPORTABLES,
    )
    configuracion = obtener_configuracion_breb_activa()
    if not configuracion or not configuracion.qr:
        raise Http404('El QR BRE-B no está disponible.')
    return _respuesta_archivo_privado(configuracion.qr, inline=True, filename='qr-breb.png')


@login_required
def comprobante_pago_breb_view(request, pago_id):
    pago = get_object_or_404(PagoBREB.objects.select_related('usuario', 'empresa'), pk=pago_id)
    puede_revisar = usuario_puede_revisar_pago_breb(
        usuario=request.user,
        pago_breb=pago,
    )
    if request.user.id != pago.usuario_id and not puede_revisar:
        raise PermissionDenied('No puedes consultar este comprobante.')

    return _respuesta_archivo_privado(pago.comprobante, inline=False)


def _respuesta_archivo_privado(campo_archivo, *, inline, filename=None):
    if not campo_archivo:
        raise Http404('El archivo no está disponible.')
    try:
        archivo = campo_archivo.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404('El archivo no está disponible.')
    content_type = mimetypes.guess_type(campo_archivo.name)[0] or 'application/octet-stream'
    nombre = filename or f'comprobante-breb{os.path.splitext(campo_archivo.name)[1].lower()}'
    return FileResponse(
        archivo,
        content_type=content_type,
        as_attachment=not inline,
        filename=nombre,
    )


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
def admin_pagos_breb_view(request):
    queryset = PagoBREB.objects.select_related(
        'credito', 'usuario', 'empresa', 'revisado_por', 'historial_pago'
    )
    estado = (request.GET.get('estado') or '').strip()
    empresa_id = (request.GET.get('empresa') or '').strip()
    if estado in PagoBREB.Estado.values:
        queryset = queryset.filter(estado=estado)
    if empresa_id.isdigit():
        queryset = queryset.filter(empresa_id=int(empresa_id))

    perfil_pagador = getattr(request.user, 'perfil_pagador', None)
    if perfil_pagador:
        queryset = queryset.filter(empresa=perfil_pagador.empresa)

    page_obj = Paginator(queryset, 25).get_page(request.GET.get('page'))
    empresas = Empresa.objects.filter(pagos_breb__isnull=False)
    if perfil_pagador:
        empresas = empresas.filter(pk=perfil_pagador.empresa_id)
    empresas = empresas.distinct().order_by('nombre')
    return render(request, 'gestion_creditos/admin_pagos_breb.html', {
        'pagos_breb': page_obj,
        'estados_breb': PagoBREB.Estado.choices,
        'estado_actual': estado,
        'empresa_actual': empresa_id,
        'empresas_breb': empresas,
    })


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
@require_POST
def admin_decidir_pago_breb_view(request, pago_id):
    pago = get_object_or_404(PagoBREB, pk=pago_id)
    form = RevisionPagoBREBForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            ' '.join(error for errores in form.errors.values() for error in errores),
        )
        return redirect('gestion:pagos_breb')

    try:
        if request.POST.get('accion') == 'aprobar':
            pago, aplicado = aprobar_pago_breb(
                pago_breb=pago,
                usuario=request.user,
                valor_aprobado=form.cleaned_data['valor_aprobado'],
            )
            if aplicado:
                _notificar_breb_despues_commit('enviar_pago_breb_aprobado', pago)
                messages.success(request, 'El pago BRE-B fue verificado y aplicado correctamente.')
            else:
                messages.info(request, 'El pago BRE-B ya había sido aplicado.')
        else:
            pago = rechazar_pago_breb(
                pago_breb=pago,
                usuario=request.user,
                motivo=form.cleaned_data['motivo_rechazo'],
            )
            _notificar_breb_despues_commit('enviar_pago_breb_rechazado', pago)
            messages.success(request, 'El reporte BRE-B fue rechazado sin modificar la cartera.')
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    return redirect('gestion:pagos_breb')

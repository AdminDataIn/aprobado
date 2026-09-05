import mimetypes
import os
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from gestion_creditos.forms import RevisionPagoBREBForm
from gestion_creditos.models import Empresa, PagoBREB
from gestion_creditos.services.breb_payments import (
    aprobar_pago_breb,
    obtener_configuracion_breb_activa,
    rechazar_pago_breb,
    usuario_puede_consultar_pago_breb,
    usuario_puede_revisar_pago_breb,
)


def _notificar_breb_despues_commit(nombre_funcion, pago):
    from django.db import transaction
    from gestion_creditos import email_service

    funcion = getattr(email_service, nombre_funcion)
    transaction.on_commit(lambda: funcion(pago))


def _respuesta_archivo_privado(campo_archivo, *, inline, filename=None):
    if not campo_archivo:
        raise Http404('El archivo no esta disponible.')
    try:
        archivo = campo_archivo.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404('El archivo no esta disponible.')
    content_type = mimetypes.guess_type(campo_archivo.name)[0] or 'application/octet-stream'
    nombre = filename or f'comprobante-breb{os.path.splitext(campo_archivo.name)[1].lower()}'
    return FileResponse(
        archivo,
        content_type=content_type,
        as_attachment=not inline,
        filename=nombre,
    )


@login_required(login_url='/pagador/login/')
def pagador_qr_breb_view(request):
    perfil = getattr(request.user, 'perfil_pagador', None)
    if not perfil or not perfil.es_pagador:
        raise PermissionDenied('El QR BRE-B es exclusivo para pagadores autorizados.')
    configuracion = obtener_configuracion_breb_activa()
    if not configuracion or not configuracion.qr:
        raise Http404('El QR BRE-B no esta disponible.')
    return _respuesta_archivo_privado(configuracion.qr, inline=True, filename='qr-breb.png')


@login_required
def comprobante_pago_breb_view(request, pago_id):
    pago = get_object_or_404(PagoBREB.objects.select_related('usuario', 'empresa'), pk=pago_id)
    if not usuario_puede_consultar_pago_breb(usuario=request.user, pago_breb=pago):
        raise PermissionDenied('No puedes consultar este comprobante.')
    return _respuesta_archivo_privado(pago.comprobante, inline=False)


def _pago_breb_para_revision(request, pago_id):
    pago = get_object_or_404(PagoBREB.objects.select_related('usuario', 'empresa'), pk=pago_id)
    if not usuario_puede_revisar_pago_breb(usuario=request.user, pago_breb=pago):
        raise PermissionDenied('La revision BRE-B es exclusiva del equipo interno autorizado.')
    return pago


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
def admin_comprobante_pago_breb_view(request, pago_id):
    pago = _pago_breb_para_revision(request, pago_id)
    return _respuesta_archivo_privado(pago.comprobante, inline=False)


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
def admin_previsualizar_comprobante_pago_breb_view(request, pago_id):
    pago = _pago_breb_para_revision(request, pago_id)
    return _respuesta_archivo_privado(pago.comprobante, inline=True)


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
def admin_pagos_breb_view(request):
    if not usuario_puede_revisar_pago_breb(usuario=request.user, pago_breb=None):
        raise PermissionDenied('La bandeja BRE-B es exclusiva del equipo interno autorizado.')

    queryset = PagoBREB.objects.select_related(
        'usuario', 'empresa', 'revisado_por', 'credito', 'historial_pago'
    ).prefetch_related(
        'detalles__credito__usuario',
        'detalles__cuota',
        'detalles__historial_pago',
    ).annotate(
        prioridad_revision=Case(
            When(estado=PagoBREB.Estado.PENDIENTE_VERIFICACION, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by('prioridad_revision', '-creado_en', '-pk')
    estado = (request.GET.get('estado') or '').strip()
    empresa_id = (request.GET.get('empresa') or '').strip()
    referencia = (request.GET.get('referencia') or '').strip()
    fecha_desde = (request.GET.get('fecha_desde') or '').strip()
    fecha_hasta = (request.GET.get('fecha_hasta') or '').strip()
    reportado_por = (request.GET.get('reportado_por') or '').strip()
    if estado in PagoBREB.Estado.values:
        queryset = queryset.filter(estado=estado)
    if empresa_id.isdigit():
        queryset = queryset.filter(empresa_id=int(empresa_id))
    if referencia:
        queryset = queryset.filter(referencia_reportada__icontains=referencia)
    zona_horaria = timezone.get_current_timezone()
    if fecha_desde and (fecha_desde_valida := parse_date(fecha_desde)):
        inicio = timezone.make_aware(datetime.combine(fecha_desde_valida, time.min), zona_horaria)
        queryset = queryset.filter(creado_en__gte=inicio)
    if fecha_hasta and (fecha_hasta_valida := parse_date(fecha_hasta)):
        fin_exclusivo = timezone.make_aware(
            datetime.combine(fecha_hasta_valida + timedelta(days=1), time.min),
            zona_horaria,
        )
        queryset = queryset.filter(creado_en__lt=fin_exclusivo)
    if reportado_por:
        queryset = queryset.filter(
            Q(usuario__username__icontains=reportado_por)
            | Q(usuario__email__icontains=reportado_por)
            | Q(usuario__first_name__icontains=reportado_por)
            | Q(usuario__last_name__icontains=reportado_por)
        )

    page_obj = Paginator(queryset, 10).get_page(request.GET.get('page'))
    for pago in page_obj.object_list:
        extension = os.path.splitext(pago.comprobante.name or '')[1].lower()
        pago.comprobante_tipo_ui = 'pdf' if extension == '.pdf' else 'image'

    query_params = request.GET.copy()
    query_params.pop('page', None)
    empresas = Empresa.objects.filter(pagos_breb__isnull=False).distinct().order_by('nombre')
    return render(request, 'gestion_creditos/admin_pagos_breb.html', {
        'pagos_breb': page_obj,
        'estados_breb': PagoBREB.Estado.choices,
        'estado_actual': estado,
        'empresa_actual': empresa_id,
        'empresas_breb': empresas,
        'referencia_actual': referencia,
        'fecha_desde_actual': fecha_desde,
        'fecha_hasta_actual': fecha_hasta,
        'reportado_por_actual': reportado_por,
        'querystring_without_page': query_params.urlencode(),
    })


@login_required
@permission_required('gestion_creditos.review_pagobreb', raise_exception=True)
@require_POST
def admin_decidir_pago_breb_view(request, pago_id):
    pago = get_object_or_404(PagoBREB, pk=pago_id)
    if not usuario_puede_revisar_pago_breb(usuario=request.user, pago_breb=pago):
        raise PermissionDenied('La revision BRE-B es exclusiva del equipo interno autorizado.')

    form = RevisionPagoBREBForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect('gestion:pagos_breb')

    try:
        if request.POST.get('accion') == 'aprobar':
            valores_aprobados = {
                detalle.pk: request.POST.get(f'valor_detalle_{detalle.pk}')
                for detalle in pago.detalles.all()
            }
            pago, aplicado = aprobar_pago_breb(
                pago_breb=pago,
                usuario=request.user,
                valores_aprobados=valores_aprobados,
                valor_aprobado=form.cleaned_data.get('valor_aprobado'),
            )
            if aplicado:
                _notificar_breb_despues_commit('enviar_pago_breb_aprobado', pago)
                messages.success(request, 'El pago BRE-B agrupado fue verificado y aplicado completamente.')
            else:
                messages.info(request, 'El pago BRE-B ya habia sido aplicado.')
        else:
            pago = rechazar_pago_breb(
                pago_breb=pago,
                usuario=request.user,
                motivo=form.cleaned_data.get('motivo_rechazo'),
            )
            _notificar_breb_despues_commit('enviar_pago_breb_rechazado', pago)
            messages.success(request, 'El reporte BRE-B fue rechazado sin modificar la cartera.')
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    return redirect('gestion:pagos_breb')

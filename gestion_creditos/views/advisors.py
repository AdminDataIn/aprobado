from .common import *
from gestion_creditos.forms import PagoComisionEjecutivoForm
from gestion_creditos.models import PagoComisionEjecutivo
from gestion_creditos.services.advisors import (
    build_admin_asesores_context,
    get_asesor_performance_snapshot,
)


@login_required(login_url='/ejecutivos/login/')
def asesor_dashboard_view(request):
    asesor = get_object_or_404(
        AsesorComercial.objects.select_related('usuario'),
        usuario=request.user,
        activo=True,
    )
    empresa_filter = (request.GET.get('empresa') or '').strip()
    selected_empresa = None
    if empresa_filter.isdigit():
        selected_empresa = asesor.empresas_referidas.filter(pk=int(empresa_filter)).first()

    summary = get_asesor_performance_snapshot(asesor, empresa=selected_empresa)
    per_page_options = [5, 10, 20, 50]
    try:
        per_page = int(request.GET.get('per_page') or 10)
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in per_page_options:
        per_page = 10

    paginator = Paginator(summary['creditos_qs'], per_page)
    page_obj = paginator.get_page(request.GET.get('page'))
    pagos_comision = asesor.pagos_comision.all()[:8]
    return render(
        request,
        'asesores/dashboard.html',
        {
            'asesor': asesor,
            'summary': summary,
            'empresas': summary['empresas_qs'],
            'empresa_filter': empresa_filter,
            'selected_empresa': selected_empresa,
            'creditos_recientes': page_obj.object_list,
            'creditos_page_obj': page_obj,
            'creditos_paginator': paginator,
            'per_page': per_page,
            'per_page_options': per_page_options,
            'pagos_comision': pagos_comision,
        },
    )


@staff_member_required
def admin_asesores_dashboard_view(request):
    asesor_filter = (request.GET.get('asesor') or '').strip()
    selected_asesor = None
    if asesor_filter.isdigit():
        selected_asesor = AsesorComercial.objects.filter(pk=int(asesor_filter), activo=True).first()

    if request.method == 'POST' and request.POST.get('action') == 'registrar_pago_comision':
        form = PagoComisionEjecutivoForm(request.POST, request.FILES)
        if form.is_valid():
            pago = form.save(commit=False)
            pago.creado_por = request.user
            pago.save()
            messages.success(request, 'Pago de comision registrado correctamente.')
            redirect_url = reverse('gestion:ejecutivos')
            if asesor_filter:
                redirect_url = f'{redirect_url}?asesor={asesor_filter}'
            return redirect(redirect_url)
        messages.error(request, 'Revisa los datos del pago de comision.')
    else:
        form = PagoComisionEjecutivoForm(initial={'asesor': selected_asesor} if selected_asesor else None)

    context = build_admin_asesores_context(selected_asesor=selected_asesor)
    summary_source = context.get('all_asesores_summary', context['asesores_summary'])
    commission_summaries = [
        {
            'id': item['asesor'].id,
            'nombre': item['asesor'].nombre,
            'comision_generada': format(item['comision_generada'], '.2f'),
            'comision_pagada': format(item['comision_pagada'], '.2f'),
            'comision_pendiente': format(item['comision_pendiente'], '.2f'),
        }
        for item in summary_source
    ]
    pagos_recientes = PagoComisionEjecutivo.objects.select_related('asesor', 'creado_por')
    if selected_asesor:
        pagos_recientes = pagos_recientes.filter(asesor=selected_asesor)
    context['asesor_filter'] = asesor_filter
    context['pago_comision_form'] = form
    context['commission_summaries'] = commission_summaries
    context['pagos_comision_recientes'] = pagos_recientes[:10]
    return render(request, 'gestion_creditos/admin_asesores_dashboard.html', context)

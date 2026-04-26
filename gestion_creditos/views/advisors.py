from .common import *
from gestion_creditos.services.advisors import (
    build_admin_asesores_context,
    get_asesor_performance_snapshot,
)


@login_required(login_url='/asesores/login/')
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
    return render(
        request,
        'asesores/dashboard.html',
        {
            'asesor': asesor,
            'summary': summary,
            'empresas': summary['empresas_qs'],
            'empresa_filter': empresa_filter,
            'selected_empresa': selected_empresa,
            'creditos_recientes': summary['creditos_recientes_qs'],
        },
    )


@staff_member_required
def admin_asesores_dashboard_view(request):
    asesor_filter = (request.GET.get('asesor') or '').strip()
    selected_asesor = None
    if asesor_filter.isdigit():
        selected_asesor = AsesorComercial.objects.filter(pk=int(asesor_filter), activo=True).first()

    context = build_admin_asesores_context(selected_asesor=selected_asesor)
    context['asesor_filter'] = asesor_filter
    return render(request, 'gestion_creditos/admin_asesores_dashboard.html', context)

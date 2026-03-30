from decimal import Decimal

from django.shortcuts import render

from .models import InvestorAccount
from usuarios.product_flow import flow_login_required


@flow_login_required('INVERSIONISTA', '/inversionista/login/')
def investor_dashboard_view(request):
    account = (
        InvestorAccount.objects
        .filter(usuario=request.user)
        .prefetch_related('positions__cashflows', 'snapshots')
        .first()
    )

    base_context = {
        'account': account,
        'positions': [],
        'latest_snapshot': None,
        'kpis': {
            'aporte_inicial': Decimal('0.00'),
            'capital_activo': Decimal('0.00'),
            'capital_recuperado': Decimal('0.00'),
            'roi_acumulado': Decimal('0.00'),
            'roi_mensual': Decimal('0.00'),
            'tasa_proyectada': Decimal('0.00'),
            'tiempo_promedio_retorno_dias': 0,
        },
        'showcase_positions': [
            {
                'titulo': 'Proyecto agroindustrial senior',
                'estado': 'En seguimiento',
                'capital': '$8.500.000',
                'retorno': '15,8% EA estimada',
            },
            {
                'titulo': 'Cartera libranza corporativa',
                'estado': 'Flujo mensual',
                'capital': '$12.400.000',
                'retorno': 'ROI acumulado 6,4%',
            },
            {
                'titulo': 'Vehículo de liquidez táctica',
                'estado': 'Disponible para asignación',
                'capital': '$3.200.000',
                'retorno': 'Salida programada 30 días',
            },
        ],
        'showcase_timeline': [
            'Activa tu cuenta y recibe confirmación operativa.',
            'Visualiza posiciones activas, capital recuperado y próximos cortes.',
            'Consulta snapshots mensuales con ROI y flujo acumulado.',
        ],
    }

    if not account:
        base_context['is_demo_mode'] = True
        return render(request, 'inversionista/dashboard.html', base_context)

    positions = list(account.positions.all())
    latest_snapshot = account.snapshots.order_by('-fecha_corte', '-created_at').first()

    kpis = {
        'aporte_inicial': sum((position.aporte_inicial for position in positions), Decimal('0.00')),
        'capital_activo': sum((position.capital_activo for position in positions), Decimal('0.00')),
        'capital_recuperado': sum((position.capital_recuperado for position in positions), Decimal('0.00')),
        'roi_acumulado': getattr(latest_snapshot, 'roi_acumulado', Decimal('0.00')),
        'roi_mensual': getattr(latest_snapshot, 'roi_mensual', Decimal('0.00')),
        'tasa_proyectada': getattr(latest_snapshot, 'tasa_retorno_proyectada', Decimal('0.00')),
        'tiempo_promedio_retorno_dias': getattr(latest_snapshot, 'tiempo_promedio_retorno_dias', 0),
    }

    base_context.update({
        'account': account,
        'positions': positions,
        'latest_snapshot': latest_snapshot,
        'kpis': kpis,
        'is_demo_mode': not positions,
    })
    return render(request, 'inversionista/dashboard.html', base_context)

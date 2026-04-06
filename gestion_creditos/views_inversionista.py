from decimal import Decimal

from django.shortcuts import render

from .models import InvestorAccount
from usuarios.product_flow import flow_login_required


def _to_decimal(value):
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or '0'))


def _build_chart_payload(series):
    values = [_to_decimal(item['valor_total']) for item in series] or [Decimal('1')]
    max_value = max(values)
    min_value = min(values)
    spread = max(max_value - min_value, Decimal('1'))

    width = 720
    height = 260
    left = 26
    top = 18
    usable_width = width - (left * 2)
    usable_height = height - (top * 2)

    points = []
    for index, item in enumerate(series):
        value = _to_decimal(item['valor_total'])
        x = left if len(series) == 1 else left + (usable_width * index / (len(series) - 1))
        normalized = (value - min_value) / spread
        y = top + usable_height - (usable_height * float(normalized))
        points.append({
            'x': round(x, 2),
            'y': round(y, 2),
            'label': item['label'],
            'value': value,
        })

    polyline = ' '.join(f"{item['x']},{item['y']}" for item in points)
    area = f"{left},{height - top} " + polyline + f" {points[-1]['x']},{height - top}"

    return {
        'width': width,
        'height': height,
        'polyline': polyline,
        'area': area,
        'points': points,
        'latest_value': points[-1]['value'],
        'min_value': min_value,
        'max_value': max_value,
    }


def _build_demo_payload():
    series = [
        {'label': 'Nov 2025', 'valor_total': Decimal('9200000')},
        {'label': 'Dic 2025', 'valor_total': Decimal('11100000')},
        {'label': 'Ene 2026', 'valor_total': Decimal('14750000')},
        {'label': 'Feb 2026', 'valor_total': Decimal('18100000')},
        {'label': 'Mar 2026', 'valor_total': Decimal('21450000')},
        {'label': 'Abr 2026', 'valor_total': Decimal('24100000')},
    ]

    allocation_breakdown = [
        {'label': 'Posición 01', 'monto': Decimal('12400000')},
        {'label': 'Posición 02', 'monto': Decimal('8500000')},
        {'label': 'Posición 03', 'monto': Decimal('3200000')},
    ]
    total = sum((item['monto'] for item in allocation_breakdown), Decimal('0.00')) or Decimal('1.00')
    for item in allocation_breakdown:
        item['porcentaje'] = ((item['monto'] / total) * Decimal('100')).quantize(Decimal('0.1'))

    return {
        'is_demo_mode': True,
        'latest_cutoff_label': '05/04/2026',
        'portfolio_series': series,
        'portfolio_chart': _build_chart_payload(series),
        'allocation_breakdown': allocation_breakdown,
        'positions_display': [
            {
                'referencia': 'INV-000241',
                'titulo': 'Posición 01',
                'subtitulo': 'Capital privado con retorno periódico',
                'estado_label': 'Activa',
                'capital_activo': Decimal('12400000'),
                'capital_recuperado': Decimal('1900000'),
                'tasa_proyectada_anual': Decimal('15.80'),
            },
            {
                'referencia': 'INV-000198',
                'titulo': 'Posición 02',
                'subtitulo': 'Estrategia de flujo mensual',
                'estado_label': 'Activa',
                'capital_activo': Decimal('8500000'),
                'capital_recuperado': Decimal('1250000'),
                'tasa_proyectada_anual': Decimal('14.20'),
            },
            {
                'referencia': 'INV-000225',
                'titulo': 'Posición 03',
                'subtitulo': 'Capital recuperado y posición cerrada',
                'estado_label': 'Cerrada',
                'capital_activo': Decimal('0'),
                'capital_recuperado': Decimal('3200000'),
                'tasa_proyectada_anual': Decimal('9.10'),
            },
        ],
        'kpis': {
            'aporte_inicial': Decimal('20700000'),
            'capital_activo': Decimal('20900000'),
            'capital_recuperado': Decimal('6350000'),
            'roi_acumulado': Decimal('8.40'),
            'roi_mensual': Decimal('1.34'),
            'tasa_proyectada': Decimal('14.70'),
            'tiempo_promedio_retorno_dias': 142,
        },
    }


def _build_real_payload(account):
    positions = list(account.positions.all())
    latest_snapshot = account.snapshots.order_by('-fecha_corte', '-created_at').first()
    snapshots = list(account.snapshots.order_by('fecha_corte', 'created_at')[:6])

    series = [
        {
            'label': snapshot.fecha_corte.strftime('%b %Y'),
            'valor_total': snapshot.capital_activo + snapshot.capital_recuperado,
        }
        for snapshot in snapshots
    ]
    if not series:
        series = [{'label': 'Sin corte', 'valor_total': Decimal('1')}]

    total_activo = sum((position.capital_activo for position in positions), Decimal('0.00'))
    total_alloc = total_activo or Decimal('1.00')
    allocation_breakdown = [
        {
            'label': position.titulo,
            'monto': position.capital_activo,
            'porcentaje': ((position.capital_activo / total_alloc) * Decimal('100')).quantize(Decimal('0.1')),
        }
        for position in positions
        if position.capital_activo > 0
    ]

    positions_display = [
        {
            'referencia': position.referencia,
            'titulo': position.titulo,
            'subtitulo': position.descripcion or 'Posición registrada en el portafolio.',
            'estado_label': position.get_estado_display(),
            'capital_activo': position.capital_activo,
            'capital_recuperado': position.capital_recuperado,
            'tasa_proyectada_anual': position.tasa_proyectada_anual,
        }
        for position in positions
    ]

    return {
        'is_demo_mode': not positions,
        'latest_cutoff_label': latest_snapshot.fecha_corte.strftime('%d/%m/%Y') if latest_snapshot else 'Sin corte',
        'portfolio_series': series,
        'portfolio_chart': _build_chart_payload(series),
        'allocation_breakdown': allocation_breakdown,
        'positions_display': positions_display,
        'kpis': {
            'aporte_inicial': sum((position.aporte_inicial for position in positions), Decimal('0.00')),
            'capital_activo': sum((position.capital_activo for position in positions), Decimal('0.00')),
            'capital_recuperado': sum((position.capital_recuperado for position in positions), Decimal('0.00')),
            'roi_acumulado': getattr(latest_snapshot, 'roi_acumulado', Decimal('0.00')),
            'roi_mensual': getattr(latest_snapshot, 'roi_mensual', Decimal('0.00')),
            'tasa_proyectada': getattr(latest_snapshot, 'tasa_retorno_proyectada', Decimal('0.00')),
            'tiempo_promedio_retorno_dias': getattr(latest_snapshot, 'tiempo_promedio_retorno_dias', 0),
        },
    }


@flow_login_required('INVERSIONISTA', '/inversionista/login/')
def investor_dashboard_view(request):
    account = (
        InvestorAccount.objects
        .filter(usuario=request.user)
        .prefetch_related('positions__cashflows', 'snapshots')
        .first()
    )

    context = {
        'account': account,
        'account_email': request.user.email or request.user.username,
    }

    if not account:
        context.update(_build_demo_payload())
        return render(request, 'inversionista/dashboard.html', context)

    payload = _build_real_payload(account)
    if payload['is_demo_mode']:
        demo = _build_demo_payload()
        payload['portfolio_series'] = demo['portfolio_series']
        payload['portfolio_chart'] = demo['portfolio_chart']
        payload['allocation_breakdown'] = demo['allocation_breakdown']
        payload['positions_display'] = demo['positions_display']

    context.update(payload)
    return render(request, 'inversionista/dashboard.html', context)

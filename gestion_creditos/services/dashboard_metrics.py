import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.paginator import Paginator
from django.http import QueryDict
from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest, TruncMonth
from django.utils import timezone

from gestion_creditos.models import (
    AsesorComercial,
    Credito,
    CuotaAmortizacion,
    DetalleContablePago,
)
from gestion_creditos.services.accounting import get_platform_disbursed_creditos_queryset
from gestion_creditos.services.admin_dashboard_filters import parse_admin_dashboard_filters
from gestion_creditos.services.advisors import filter_creditos_by_asesor


DINERO = DecimalField(max_digits=14, decimal_places=2)
CERO = Value(Decimal('0.00'), output_field=DINERO)
ESTADOS_CARTERA = [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]
MESES_ES = (
    'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
    'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic',
)


def calcular_total_en_mora(creditos=None, filtros=None):
    cuotas = CuotaAmortizacion.objects.filter(
        pagada=False,
        fecha_vencimiento__lt=timezone.localdate(),
    )
    if not getattr(settings, 'LIBRANZA_AUTO_MARK_MORA_ENABLED', True):
        cuotas = cuotas.exclude(credito__linea=Credito.LineaCredito.LIBRANZA)
    if creditos is not None:
        cuotas = cuotas.filter(credito__in=creditos)
    else:
        cuotas = cuotas.filter(credito__estado__in=ESTADOS_CARTERA)
    if filtros is not None:
        cuotas = filtros.aplicar_fecha_vencimiento(cuotas)

    pendiente = Greatest(
        Coalesce(F('valor_cuota'), CERO) - Coalesce(F('monto_pagado'), CERO),
        CERO,
    )
    return cuotas.aggregate(
        total=Coalesce(Sum(pendiente), CERO, output_field=DINERO),
    )['total']


def _base_admin_queryset():
    return (
        Credito.objects.select_related(
            'usuario',
            'detalle_libranza__empresa',
            'detalle_emprendimiento',
            'detalle_adelanto_nomina__vinculo_laboral__empresa',
        )
        .annotate(
            empresa_nombre=Case(
                When(
                    linea=Credito.LineaCredito.LIBRANZA,
                    then=Coalesce('detalle_libranza__empresa__nombre', Value('SIN EMPRESA')),
                ),
                When(
                    linea=Credito.LineaCredito.ADELANTO_NOMINA,
                    then=Coalesce(
                        'detalle_adelanto_nomina__vinculo_laboral__empresa__nombre',
                        Value('SIN EMPRESA'),
                    ),
                ),
                default=Value('SIN EMPRESA'),
                output_field=CharField(),
            )
        )
    )


def _build_accounting_metrics(creditos_qs, filtros):
    detalles = filtros.aplicar_fecha_recaudo(
        DetalleContablePago.objects.filter(credito__in=creditos_qs)
    )
    agregados = detalles.aggregate(
        total_recaudado=Coalesce(Sum('monto_total_aplicado'), CERO, output_field=DINERO),
        capital_recuperado=Coalesce(Sum('capital_principal_aplicado'), CERO, output_field=DINERO),
        interes_recuperado=Coalesce(Sum('interes_aplicado'), CERO, output_field=DINERO),
        comision_recuperada=Coalesce(Sum('comision_aplicada'), CERO, output_field=DINERO),
        iva_recuperado=Coalesce(Sum('iva_aplicado'), CERO, output_field=DINERO),
    )
    return {
        **agregados,
        'rentabilidad_breakdown_supported': detalles.exists(),
        'creditos_con_trazabilidad_contable': detalles.values('credito_id').distinct().count(),
        'pagos_con_trazabilidad_contable': detalles.values('pago_id').distinct().count(),
        'detalles': detalles,
    }


def _obligaciones_queryset(creditos_cartera, filtros):
    primera_pendiente = (
        CuotaAmortizacion.objects
        .filter(credito_id=OuterRef('pk'), pagada=False)
        .order_by('fecha_vencimiento', 'numero_cuota', 'pk')
    )
    queryset = creditos_cartera.annotate(
        cuota_id=Subquery(primera_pendiente.values('pk')[:1]),
        cuota_numero=Subquery(primera_pendiente.values('numero_cuota')[:1]),
        cuota_fecha_vencimiento=Subquery(primera_pendiente.values('fecha_vencimiento')[:1]),
        cuota_valor=Subquery(
            primera_pendiente.values('valor_cuota')[:1],
            output_field=DINERO,
        ),
        cuota_monto_pagado=Subquery(
            primera_pendiente.values('monto_pagado')[:1],
            output_field=DINERO,
        ),
    ).filter(cuota_id__isnull=False)
    return filtros.aplicar_fecha_vencimiento(queryset, 'cuota_fecha_vencimiento')


def _clasificar_obligacion(fecha_vencimiento, hoy):
    diferencia = (fecha_vencimiento - hoy).days
    if diferencia < 0:
        return 'VENCIDA', 'Vencida', abs(diferencia), f'{abs(diferencia)} dias vencida'
    if diferencia == 0:
        return 'VENCE_HOY', 'Vence hoy', 0, 'Vence hoy'
    if diferencia <= 15:
        return 'VENCE_PRONTO', 'Vence pronto', diferencia, f'{diferencia} dias para vencer'
    return 'AL_DIA', 'Al dia', diferencia, f'{diferencia} dias para vencer'


def _obligaciones_distribucion(base):
    hoy = timezone.localdate()
    return base.aggregate(
        VENCIDA=Count('pk', filter=Q(cuota_fecha_vencimiento__lt=hoy)),
        VENCE_HOY=Count('pk', filter=Q(cuota_fecha_vencimiento=hoy)),
        VENCE_PRONTO=Count(
            'pk',
            filter=Q(
                cuota_fecha_vencimiento__gt=hoy,
                cuota_fecha_vencimiento__lte=hoy + timedelta(days=15),
            ),
        ),
        AL_DIA=Count(
            'pk',
            filter=Q(cuota_fecha_vencimiento__gt=hoy + timedelta(days=15)),
        ),
    )


def _filtrar_estado_obligacion(queryset, estado):
    hoy = timezone.localdate()
    if estado == 'VENCIDA':
        return queryset.filter(cuota_fecha_vencimiento__lt=hoy)
    if estado == 'VENCE_HOY':
        return queryset.filter(cuota_fecha_vencimiento=hoy)
    if estado == 'VENCE_PRONTO':
        return queryset.filter(
            cuota_fecha_vencimiento__gt=hoy,
            cuota_fecha_vencimiento__lte=hoy + timedelta(days=15),
        )
    if estado == 'AL_DIA':
        return queryset.filter(cuota_fecha_vencimiento__gt=hoy + timedelta(days=15))
    return queryset


def _serializar_obligacion(credito):
    hoy = timezone.localdate()
    monto_pagado = credito.cuota_monto_pagado or Decimal('0.00')
    valor_cuota = credito.cuota_valor or Decimal('0.00')
    codigo, label, dias, texto_dias = _clasificar_obligacion(
        credito.cuota_fecha_vencimiento,
        hoy,
    )
    return {
        'credito_id': credito.pk,
        'numero_credito': credito.numero_credito,
        'cliente': credito.nombre_cliente,
        'empresa': credito.empresa_nombre,
        'numero_cuota': credito.cuota_numero,
        'fecha_vencimiento': credito.cuota_fecha_vencimiento,
        'valor_cuota': valor_cuota,
        'monto_pagado': monto_pagado,
        'valor_pendiente': max(valor_cuota - monto_pagado, Decimal('0.00')),
        'estado_codigo': codigo,
        'estado_label': label,
        'dias': dias,
        'dias_label': texto_dias,
    }


def _monthly_rows(queryset, date_field, annotations):
    return list(
        queryset.annotate(
            mes=TruncMonth(date_field, tzinfo=timezone.get_current_timezone()),
        )
        .values('mes')
        .annotate(**annotations)
        .order_by('mes')
    )


def _month_label(value):
    return f'{MESES_ES[value.month - 1]} {value.year}'


def _build_chart_series(base_creditos, accounting_details, filtros):
    recaudo_rows = _monthly_rows(
        accounting_details,
        'fecha_aplicacion',
        {
            'capital': Coalesce(Sum('capital_principal_aplicado'), CERO, output_field=DINERO),
            'interes': Coalesce(Sum('interes_aplicado'), CERO, output_field=DINERO),
            'comision': Coalesce(Sum('comision_aplicada'), CERO, output_field=DINERO),
            'iva': Coalesce(Sum('iva_aplicado'), CERO, output_field=DINERO),
        },
    )

    desembolsos = filtros.aplicar_fecha_credito(
        base_creditos.filter(fecha_desembolso__isnull=False),
        'fecha_desembolso',
    )
    desembolso_rows = _monthly_rows(
        desembolsos,
        'fecha_desembolso',
        {
            'cantidad': Count('pk', distinct=True),
            'monto': Coalesce(
                Sum(Coalesce('monto_aprobado', 'monto_solicitado', output_field=DINERO)),
                CERO,
                output_field=DINERO,
            ),
        },
    )

    solicitudes = filtros.aplicar_fecha_credito(base_creditos, 'fecha_solicitud')
    solicitud_rows = _monthly_rows(
        solicitudes,
        'fecha_solicitud',
        {'cantidad': Count('pk', distinct=True)},
    )
    return {
        'recaudo_labels': json.dumps([_month_label(row['mes']) for row in recaudo_rows]),
        'recaudo_capital': json.dumps([float(row['capital']) for row in recaudo_rows]),
        'recaudo_interes': json.dumps([float(row['interes']) for row in recaudo_rows]),
        'recaudo_comision': json.dumps([float(row['comision']) for row in recaudo_rows]),
        'recaudo_iva': json.dumps([float(row['iva']) for row in recaudo_rows]),
        'desembolso_labels': json.dumps([_month_label(row['mes']) for row in desembolso_rows]),
        'desembolso_cantidad': json.dumps([row['cantidad'] for row in desembolso_rows]),
        'desembolso_monto': json.dumps([float(row['monto']) for row in desembolso_rows]),
        'solicitud_labels': json.dumps([_month_label(row['mes']) for row in solicitud_rows]),
        'solicitud_cantidad': json.dumps([row['cantidad'] for row in solicitud_rows]),
    }


def _build_query_links(request, key, values, *, clear_dates=False):
    links = []
    for value, label in values:
        params = request.GET.copy() if request is not None else QueryDict('', mutable=True)
        if clear_dates:
            params.pop('fecha_desde', None)
            params.pop('fecha_hasta', None)
        params.pop('page', None)
        if value:
            params[key] = value
        else:
            params.pop(key, None)
        links.append({'value': value, 'label': label, 'query': params.urlencode()})
    return links


def get_admin_dashboard_context(user, request=None):
    filtros = parse_admin_dashboard_filters(request)
    base_creditos = filtros.aplicar_dimensiones_credito(_base_admin_queryset())
    creditos_cartera = base_creditos.filter(estado__in=ESTADOS_CARTERA)

    kpis = creditos_cartera.aggregate(
        saldo_cartera_total=Coalesce(Sum('saldo_pendiente'), CERO, output_field=DINERO),
        saldo_capital_pendiente=Coalesce(Sum('capital_pendiente'), CERO, output_field=DINERO),
        capital_pendiente_incompleto=Count('pk', filter=Q(capital_pendiente__isnull=True)),
    )
    monto_total_en_mora = calcular_total_en_mora(creditos_cartera, filtros=filtros)
    total_creditos = creditos_cartera.count()
    obligaciones_base = _obligaciones_queryset(creditos_cartera, filtros)
    obligaciones_distribucion = _obligaciones_distribucion(obligaciones_base)
    obligaciones_total = sum(obligaciones_distribucion.values())
    proximos_vencer = (
        obligaciones_distribucion['VENCE_HOY']
        + obligaciones_distribucion['VENCE_PRONTO']
    )

    creditos_por_linea = list(
        creditos_cartera.values('linea')
        .annotate(
            linea_label=Case(
                When(linea=Credito.LineaCredito.EMPRENDIMIENTO, then=Value('Emprendimiento')),
                When(linea=Credito.LineaCredito.LIBRANZA, then=Value('Libranza')),
                When(linea=Credito.LineaCredito.ADELANTO_NOMINA, then=Value('Adelanto de nomina')),
                default=F('linea'),
                output_field=CharField(),
            ),
            count=Count('id', distinct=True),
            saldo_total=Coalesce(Sum('saldo_pendiente'), CERO, output_field=DINERO),
        )
        .order_by('-saldo_total')
    )

    total_general_creditos = base_creditos.count()
    creditos_por_estado_q = list(
        base_creditos.values('estado').annotate(count=Count('id', distinct=True)).order_by('-count')
    )
    creditos_por_estado = [
        {
            'estado': item['estado'],
            'count': item['count'],
            'porcentaje': (
                (item['count'] / total_general_creditos) * 100
                if total_general_creditos else 0
            ),
        }
        for item in creditos_por_estado_q
    ]

    creditos_por_empresa = list(
        creditos_cartera.values('empresa_nombre')
        .annotate(
            count=Count('id', distinct=True),
            saldo_total=Coalesce(Sum('saldo_pendiente'), CERO, output_field=DINERO),
        )
        .order_by('-saldo_total', 'empresa_nombre')
    )
    top_n = max(1, min(int(getattr(settings, 'ADMIN_DASHBOARD_EMPRESA_TOP_N', 8)), 12))
    empresas_top = creditos_por_empresa[:top_n]
    empresas_otros = creditos_por_empresa[top_n:]
    otros_saldo = kpis['saldo_cartera_total'] - sum(
        (item['saldo_total'] for item in empresas_top),
        Decimal('0.00'),
    )
    cartera_empresa_labels = [item['empresa_nombre'] for item in empresas_top]
    cartera_empresa_data = [float(item['saldo_total']) for item in empresas_top]
    cartera_empresa_cantidad = [item['count'] for item in empresas_top]
    if empresas_otros:
        cartera_empresa_labels.append('OTROS')
        cartera_empresa_data.append(float(otros_saldo))
        cartera_empresa_cantidad.append(sum(item['count'] for item in empresas_otros))

    accounting_metrics = _build_accounting_metrics(base_creditos, filtros)
    chart_series = _build_chart_series(
        base_creditos,
        accounting_metrics.pop('detalles'),
        filtros,
    )

    empresas_choices = sorted(
        set(
            filter_creditos_by_asesor(_base_admin_queryset(), filtros.asesor)
            .exclude(empresa_nombre='SIN EMPRESA')
            .values_list('empresa_nombre', flat=True)
        )
    )
    asesores_choices = list(
        AsesorComercial.objects.filter(activo=True).order_by('nombre').values('id', 'nombre')
    )

    periodo_links = _build_query_links(
        request,
        'periodo',
        (
            ('este_mes', 'Este mes'),
            ('mes_anterior', 'Mes anterior'),
            ('ultimos_3_meses', 'Últimos 3 meses'),
            ('este_anio', 'Este año'),
            ('todo', 'Todo'),
        ),
        clear_dates=True,
    )
    obligacion_links = _build_query_links(
        request,
        'obligacion',
        (
            ('TODAS', 'Todas'),
            ('VENCIDA', 'Vencidas'),
            ('VENCE_HOY', 'Vence hoy'),
            ('VENCE_PRONTO', 'Próximas'),
        ),
    )

    return {
        **kpis,
        **accounting_metrics,
        **chart_series,
        'monto_total_en_mora': monto_total_en_mora,
        'total_creditos': total_creditos,
        'proximos_vencer': proximos_vencer,
        'creditos_por_linea': creditos_por_linea,
        'creditos_por_estado': creditos_por_estado,
        'creditos_por_empresa': creditos_por_empresa,
        'cartera_empresa_labels': json.dumps(cartera_empresa_labels),
        'cartera_empresa_data': json.dumps(cartera_empresa_data),
        'cartera_empresa_cantidad': json.dumps(cartera_empresa_cantidad),
        'estado_chart_labels': json.dumps([item['estado'] for item in creditos_por_estado]),
        'estado_chart_data': json.dumps([item['count'] for item in creditos_por_estado]),
        'obligaciones_chart_labels': json.dumps([
            'Vencidas', 'Vence hoy', 'Vence pronto', 'Al día',
        ]),
        'obligaciones_chart_data': json.dumps([
            obligaciones_distribucion['VENCIDA'],
            obligaciones_distribucion['VENCE_HOY'],
            obligaciones_distribucion['VENCE_PRONTO'],
            obligaciones_distribucion['AL_DIA'],
        ]),
        'obligaciones_total': obligaciones_total,
        'obligaciones_distribucion': obligaciones_distribucion,
        'filtros': filtros,
        'filtros_errores': filtros.errores,
        'fecha_desde_filter': filtros.fecha_desde.isoformat() if filtros.fecha_desde else '',
        'fecha_hasta_filter': filtros.fecha_hasta.isoformat() if filtros.fecha_hasta else '',
        'empresa_filter': filtros.empresa.nombre if filtros.empresa else '',
        'asesor_filter': filtros.asesor_raw,
        'estado_filter': filtros.estado,
        'linea_filter': filtros.linea,
        'periodo_filter': filtros.periodo,
        'obligacion_filter': filtros.obligacion_estado,
        'empresas_choices': empresas_choices,
        'asesores_choices': asesores_choices,
        'estados_choices': Credito.EstadoCredito.choices,
        'lineas_choices': Credito.LineaCredito.choices,
        'selected_asesor': filtros.asesor,
        'selected_empresa': filtros.empresa,
        'periodo_links': periodo_links,
        'obligacion_links': obligacion_links,
        'dashboard_querystring': request.GET.urlencode() if request is not None else '',
        'cartera_es_corte_actual': True,
        'filtros_avanzados_activos': bool(
            request is not None
            and any(
                request.GET.get(key)
                for key in ('fecha_desde', 'fecha_hasta', 'empresa', 'estado', 'linea', 'asesor')
            )
        ),
    }


def get_admin_obligaciones_context(request):
    filtros = parse_admin_dashboard_filters(request)
    creditos_cartera = filtros.aplicar_dimensiones_credito(
        _base_admin_queryset().filter(estado__in=ESTADOS_CARTERA)
    )
    base = _obligaciones_queryset(creditos_cartera, filtros)
    distribucion = _obligaciones_distribucion(base)
    busqueda = (request.GET.get('credito') or '').strip()[:40]
    if busqueda:
        base = base.filter(numero_credito__icontains=busqueda)
    filtradas = _filtrar_estado_obligacion(base, filtros.obligacion_estado).order_by(
        'cuota_fecha_vencimiento', 'cuota_numero', 'numero_credito'
    )

    pagina = Paginator(filtradas, 20).get_page(request.GET.get('page'))
    pagina.object_list = [_serializar_obligacion(credito) for credito in pagina.object_list]
    query_params = request.GET.copy()
    query_params.pop('page', None)

    empresas_choices = sorted(
        set(
            filter_creditos_by_asesor(_base_admin_queryset(), filtros.asesor)
            .exclude(empresa_nombre='SIN EMPRESA')
            .values_list('empresa_nombre', flat=True)
        )
    )
    return {
        'pagina_obligaciones': pagina,
        'obligaciones': pagina.object_list,
        'obligaciones_distribucion': distribucion,
        'obligaciones_total': sum(distribucion.values()),
        'filtros': filtros,
        'filtros_errores': filtros.errores,
        'fecha_desde_filter': filtros.fecha_desde.isoformat() if filtros.fecha_desde else '',
        'fecha_hasta_filter': filtros.fecha_hasta.isoformat() if filtros.fecha_hasta else '',
        'empresa_filter': filtros.empresa.nombre if filtros.empresa else '',
        'obligacion_filter': filtros.obligacion_estado,
        'credito_filter': busqueda,
        'empresas_choices': empresas_choices,
        'obligacion_links': _build_query_links(
            request,
            'obligacion',
            (
                ('TODAS', 'Todas'),
                ('VENCIDA', 'Vencidas'),
                ('VENCE_HOY', 'Vence hoy'),
                ('VENCE_PRONTO', 'Próximas'),
                ('AL_DIA', 'Al día'),
            ),
        ),
        'obligaciones_querystring': query_params.urlencode(),
    }

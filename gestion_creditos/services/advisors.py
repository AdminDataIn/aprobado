from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from gestion_creditos.models import AsesorComercial, Credito, Empresa, PagoComisionEjecutivo


EXECUTIVE_VISIBLE_STATES = {
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.PAGADO,
}

EXECUTIVE_COMMISSION_RATE = Decimal('0.01')


def _advisor_credit_filter(asesor):
    return (
        Q(linea=Credito.LineaCredito.LIBRANZA, detalle_libranza__empresa__asesor_comercial=asesor)
        |
        Q(
            linea=Credito.LineaCredito.ADELANTO_NOMINA,
            detalle_adelanto_nomina__vinculo_laboral__empresa__asesor_comercial=asesor,
        )
    )


def filter_creditos_by_asesor(creditos_qs, asesor):
    if not asesor:
        return creditos_qs
    return creditos_qs.filter(_advisor_credit_filter(asesor)).distinct()


def filter_empresas_by_asesor(empresas_qs, asesor):
    if not asesor:
        return empresas_qs
    return empresas_qs.filter(asesor_comercial=asesor)


def filter_creditos_by_empresa(creditos_qs, empresa):
    if not empresa:
        return creditos_qs
    return creditos_qs.filter(
        Q(linea=Credito.LineaCredito.LIBRANZA, detalle_libranza__empresa=empresa)
        |
        Q(
            linea=Credito.LineaCredito.ADELANTO_NOMINA,
            detalle_adelanto_nomina__vinculo_laboral__empresa=empresa,
        )
    )


def get_asesor_creditos_queryset(asesor, empresa=None):
    qs = filter_creditos_by_asesor(
        Credito.objects.select_related(
            'usuario',
            'detalle_libranza__empresa__asesor_comercial',
            'detalle_adelanto_nomina__vinculo_laboral__empresa__asesor_comercial',
        ),
        asesor,
    )
    qs = filter_creditos_by_empresa(qs, empresa)
    return qs.distinct().order_by('-fecha_solicitud')


def get_asesor_empresas_queryset(asesor):
    return Empresa.objects.filter(asesor_comercial=asesor).order_by('nombre')


def calculate_asesor_commission(monto_colocado):
    return (monto_colocado or Decimal('0.00')) * EXECUTIVE_COMMISSION_RATE


def _sumar_pagos_comision(asesor):
    return PagoComisionEjecutivo.objects.filter(asesor=asesor).aggregate(
        total=Coalesce(Sum('monto'), Decimal('0.00')),
    )['total'] or Decimal('0.00')


def _max_decimal(value, floor=Decimal('0.00')):
    return value if value > floor else floor


def get_asesor_commission_account(asesor):
    creditos_qs = get_asesor_creditos_queryset(asesor)
    visible_creditos_qs = creditos_qs.filter(estado__in=EXECUTIVE_VISIBLE_STATES)
    monto_colocado = visible_creditos_qs.aggregate(
        total=Coalesce(Sum(Coalesce('monto_aprobado', 'monto_solicitado')), Decimal('0.00')),
    )['total'] or Decimal('0.00')
    comision_generada = calculate_asesor_commission(monto_colocado)
    comision_pagada = _sumar_pagos_comision(asesor)
    return {
        'monto_colocado': monto_colocado,
        'comision_generada': comision_generada,
        'comision_pagada': comision_pagada,
        'comision_pendiente': _max_decimal(comision_generada - comision_pagada),
    }


def get_asesor_performance_snapshot(asesor, empresa=None):
    creditos_qs = get_asesor_creditos_queryset(asesor, empresa=empresa)
    empresas_qs = get_asesor_empresas_queryset(asesor)
    visible_creditos_qs = creditos_qs.filter(estado__in=EXECUTIVE_VISIBLE_STATES)
    activos_qs = visible_creditos_qs.filter(estado=Credito.EstadoCredito.ACTIVO)
    pagados_qs = visible_creditos_qs.filter(estado=Credito.EstadoCredito.PAGADO)

    aggregates = visible_creditos_qs.aggregate(
        total_creditos_colocados=Count('id', distinct=True),
        monto_colocado=Coalesce(
            Sum(Coalesce('monto_aprobado', 'monto_solicitado')),
            Decimal('0.00'),
        ),
        creditos_activos=Count(
            'id',
            filter=Q(estado=Credito.EstadoCredito.ACTIVO),
            distinct=True,
        ),
        creditos_pagados=Count(
            'id',
            filter=Q(estado=Credito.EstadoCredito.PAGADO),
            distinct=True,
        ),
    )
    cartera = activos_qs.aggregate(
        saldo_cartera=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')),
    )
    monto_colocado = aggregates['monto_colocado'] or Decimal('0.00')
    comision_acumulada = calculate_asesor_commission(monto_colocado)
    commission_account = get_asesor_commission_account(asesor)

    return {
        'asesor': asesor,
        'empresas_count': empresas_qs.count(),
        'empresas_qs': empresas_qs,
        'selected_empresa': empresa,
        'creditos_qs': visible_creditos_qs,
        'creditos_recientes_qs': visible_creditos_qs[:12],
        'total_creditos': aggregates['total_creditos_colocados'] or 0,
        'creditos_activos': aggregates['creditos_activos'] or 0,
        'creditos_pagados': aggregates['creditos_pagados'] or 0,
        'monto_colocado': monto_colocado,
        'comision_acumulada': comision_acumulada,
        'comision_generada': commission_account['comision_generada'],
        'comision_pagada': commission_account['comision_pagada'],
        'comision_pendiente': commission_account['comision_pendiente'],
        'commission_account': commission_account,
        'saldo_cartera': cartera['saldo_cartera'] or Decimal('0.00'),
    }


def build_admin_asesores_context(selected_asesor=None):
    asesores = list(
        AsesorComercial.objects.filter(activo=True)
        .select_related('usuario')
        .order_by('nombre')
    )
    summaries = [get_asesor_performance_snapshot(asesor) for asesor in asesores]

    selected_summary = None
    if selected_asesor:
        selected_summary = next(
            (item for item in summaries if item['asesor'].pk == selected_asesor.pk),
            get_asesor_performance_snapshot(selected_asesor),
        )

    visible_summaries = [selected_summary] if selected_summary else summaries

    resumen_general = {
        'total_asesores': len(visible_summaries),
        'total_empresas_referidas': sum(item['empresas_count'] for item in visible_summaries),
        'total_creditos_activos': sum(item['creditos_activos'] for item in visible_summaries),
        'total_creditos_pagados': sum(item['creditos_pagados'] for item in visible_summaries),
        'monto_total_colocado': sum(
            (item['monto_colocado'] for item in visible_summaries),
            Decimal('0.00'),
        ),
        'comision_total_generada': sum(
            (item['comision_generada'] for item in visible_summaries),
            Decimal('0.00'),
        ),
        'comision_total_pagada': sum(
            (item['comision_pagada'] for item in visible_summaries),
            Decimal('0.00'),
        ),
        'comision_total_pendiente': sum(
            (item['comision_pendiente'] for item in visible_summaries),
            Decimal('0.00'),
        ),
        'ejecutivos_con_comision_pendiente': sum(
            1 for item in visible_summaries if item['comision_pendiente'] > Decimal('0.00')
        ),
    }

    return {
        'asesores': asesores,
        'asesores_summary': visible_summaries,
        'all_asesores_summary': summaries,
        'selected_asesor': selected_asesor,
        'selected_asesor_summary': selected_summary,
        'resumen_general': resumen_general,
    }

import json
from datetime import datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db.models import Case, CharField, Count, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from gestion_creditos.models import AsesorComercial, Credito, CuotaAmortizacion, Empresa
from gestion_creditos.services.accounting import (
    get_accounting_summary_for_creditos,
    get_platform_disbursed_creditos_queryset,
)
from gestion_creditos.services.advisors import filter_creditos_by_asesor
from gestion_creditos.services.empresa_geografia import obtener_presencia_empresas


def calcular_total_en_mora(creditos=None):
    today = timezone.now().date()

    cuotas = CuotaAmortizacion.objects.filter(
        pagada=False,
        fecha_vencimiento__lt=today,
    )

    if not getattr(settings, 'LIBRANZA_AUTO_MARK_MORA_ENABLED', True):
        cuotas = cuotas.exclude(credito__linea=Credito.LineaCredito.LIBRANZA)

    if creditos is not None:
        cuotas = cuotas.filter(credito__in=creditos)
    else:
        cuotas = cuotas.filter(
            credito__estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]
        )

    total = Decimal('0.00')
    for cuota in cuotas:
        restante = (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00'))
        if restante > 0:
            total += restante
    return total


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
                    then=Coalesce('detalle_adelanto_nomina__vinculo_laboral__empresa__nombre', Value('SIN EMPRESA')),
                ),
                default=Value('SIN EMPRESA'),
            )
        )
    )


def _build_accounting_metrics(creditos_qs):
    creditos_contables_qs = get_platform_disbursed_creditos_queryset(
        creditos_qs.filter(
            estado__in=[
                Credito.EstadoCredito.ACTIVO,
                Credito.EstadoCredito.EN_MORA,
                Credito.EstadoCredito.PAGADO,
            ],
        )
    )
    if not creditos_contables_qs.exists():
        return {
            'total_recaudado': Decimal('0.00'),
            'capital_recuperado': Decimal('0.00'),
            'interes_recuperado': Decimal('0.00'),
            'comision_recuperada': Decimal('0.00'),
            'iva_recuperado': Decimal('0.00'),
            'rentabilidad_breakdown_supported': False,
            'creditos_con_trazabilidad_contable': 0,
            'pagos_con_trazabilidad_contable': 0,
        }

    summary = get_accounting_summary_for_creditos(creditos_contables_qs)

    return {
        'total_recaudado': summary['total_recaudado'],
        'capital_recuperado': summary['capital_principal_aplicado'],
        'interes_recuperado': summary['interes_aplicado'],
        'comision_recuperada': summary['comision_aplicada'],
        'iva_recuperado': summary['iva_aplicado'],
        'rentabilidad_breakdown_supported': summary['supports_breakdown'],
        'creditos_con_trazabilidad_contable': summary['creditos_con_trazabilidad'],
        'pagos_con_trazabilidad_contable': summary['pagos_con_trazabilidad'],
    }


def get_admin_dashboard_context(user, request=None):
    today = timezone.now().date()
    proximos_15_dias = today + timedelta(days=15)
    empresa_filter = (request.GET.get('empresa', '').strip() if request else '')
    asesor_filter = (request.GET.get('asesor', '').strip() if request else '')
    selected_asesor = None
    if asesor_filter.isdigit():
        selected_asesor = AsesorComercial.objects.filter(pk=int(asesor_filter), activo=True).first()

    creditos_activos = _base_admin_queryset().filter(
        estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]
    )
    creditos_activos = filter_creditos_by_asesor(creditos_activos, selected_asesor)
    if empresa_filter:
        creditos_activos = creditos_activos.filter(empresa_nombre=empresa_filter)

    kpis = creditos_activos.aggregate(
        saldo_cartera_total=Coalesce(Sum('saldo_pendiente'), Decimal('0.00'))
    )
    monto_total_en_mora = calcular_total_en_mora(creditos_activos)
    total_creditos = creditos_activos.count()
    proximos_vencer = creditos_activos.filter(fecha_proximo_pago__range=[today, proximos_15_dias]).count()

    creditos_por_linea_q = list(
        creditos_activos.values('linea')
        .annotate(
            linea_label=Case(
                When(linea=Credito.LineaCredito.EMPRENDIMIENTO, then=Value('Emprendimiento')),
                When(linea=Credito.LineaCredito.LIBRANZA, then=Value('Libranza')),
                When(linea=Credito.LineaCredito.ADELANTO_NOMINA, then=Value('Adelanto de nomina')),
                default=F('linea'),
                output_field=CharField(),
            ),
            count=Count('id'),
            saldo_total=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')),
        )
        .order_by('-saldo_total')
    )

    total_general_creditos_qs = _base_admin_queryset()
    total_general_creditos_qs = filter_creditos_by_asesor(total_general_creditos_qs, selected_asesor)
    if empresa_filter:
        total_general_creditos_qs = total_general_creditos_qs.filter(empresa_nombre=empresa_filter)

    total_general_creditos = total_general_creditos_qs.count()
    accounting_metrics = _build_accounting_metrics(total_general_creditos_qs)
    creditos_por_estado_q = total_general_creditos_qs.values('estado').annotate(count=Count('id')).order_by('-count')
    creditos_por_estado = [
        {
            'estado': item['estado'],
            'count': item['count'],
            'porcentaje': ((item['count'] / total_general_creditos) * 100) if total_general_creditos else 0,
        }
        for item in creditos_por_estado_q
    ]

    distribution_qs = (
        creditos_activos.values('empresa_nombre')
        .annotate(count=Count('id'))
        .order_by('-count', 'empresa_nombre')
    )
    distribution_labels = [item['empresa_nombre'] for item in distribution_qs]
    distribution_data = [item['count'] for item in distribution_qs]

    portfolio_labels = []
    for i in range(11, -1, -1):
        mes_fecha = today - relativedelta(months=i)
        portfolio_labels.append(mes_fecha.strftime('%b %Y'))

    emprendimiento_data = []
    libranza_data = []
    adelanto_data = []
    base_historica = _base_admin_queryset()
    base_historica = filter_creditos_by_asesor(base_historica, selected_asesor)
    if empresa_filter:
        base_historica = base_historica.filter(empresa_nombre=empresa_filter)

    for label in portfolio_labels:
        mes_date = datetime.strptime(label, '%b %Y')
        primer_dia_mes = mes_date.replace(day=1).date()
        ultimo_dia_mes = primer_dia_mes + relativedelta(months=1) - timedelta(days=1)

        def saldo_linea(linea):
            return (
                base_historica.filter(
                    linea=linea,
                    estado__in=[
                        Credito.EstadoCredito.ACTIVO,
                        Credito.EstadoCredito.EN_MORA,
                        Credito.EstadoCredito.PAGADO,
                    ],
                    fecha_desembolso__date__lte=ultimo_dia_mes,
                ).aggregate(saldo=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')))['saldo']
                or Decimal('0.00')
            )

        emprendimiento_data.append(float(saldo_linea(Credito.LineaCredito.EMPRENDIMIENTO)))
        libranza_data.append(float(saldo_linea(Credito.LineaCredito.LIBRANZA)))
        adelanto_data.append(float(saldo_linea(Credito.LineaCredito.ADELANTO_NOMINA)))

    total_data = [e + l + a for e, l, a in zip(emprendimiento_data, libranza_data, adelanto_data)]

    empresas_choices = sorted(
        set(
            filter_creditos_by_asesor(_base_admin_queryset(), selected_asesor)
            .exclude(empresa_nombre='SIN EMPRESA')
            .values_list('empresa_nombre', flat=True)
        )
    )
    asesores_choices = list(
        AsesorComercial.objects.filter(activo=True)
        .order_by('nombre')
        .values('id', 'nombre')
    )
    empresas_presencia = Empresa.objects.all()
    if empresa_filter:
        empresas_presencia = empresas_presencia.filter(nombre=empresa_filter)
    presencia_empresas = obtener_presencia_empresas(empresas_presencia)

    return {
        'saldo_cartera_total': kpis['saldo_cartera_total'],
        'monto_total_en_mora': monto_total_en_mora,
        'total_creditos': total_creditos,
        'proximos_vencer': proximos_vencer,
        'creditos_por_linea': creditos_por_linea_q,
        'creditos_por_estado': creditos_por_estado,
        'creditos_por_empresa': distribution_qs,
        'distribution_labels': json.dumps(distribution_labels),
        'distribution_data': json.dumps(distribution_data),
        'portfolio_labels': json.dumps(portfolio_labels),
        'emprendimiento_data': json.dumps(emprendimiento_data),
        'libranza_data': json.dumps(libranza_data),
        'adelanto_data': json.dumps(adelanto_data),
        'total_data': json.dumps(total_data),
        'empresa_filter': empresa_filter,
        'asesor_filter': asesor_filter,
        'empresas_choices': empresas_choices,
        'asesores_choices': asesores_choices,
        'selected_asesor': selected_asesor,
        'total_recaudado': accounting_metrics['total_recaudado'],
        'capital_recuperado': accounting_metrics['capital_recuperado'],
        'interes_recuperado': accounting_metrics['interes_recuperado'],
        'comision_recuperada': accounting_metrics['comision_recuperada'],
        'iva_recuperado': accounting_metrics['iva_recuperado'],
        'rentabilidad_breakdown_supported': accounting_metrics['rentabilidad_breakdown_supported'],
        'creditos_con_trazabilidad_contable': accounting_metrics['creditos_con_trazabilidad_contable'],
        'pagos_con_trazabilidad_contable': accounting_metrics['pagos_con_trazabilidad_contable'],
        'presencia_empresas': presencia_empresas,
    }

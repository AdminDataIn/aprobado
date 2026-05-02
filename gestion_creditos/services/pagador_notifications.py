import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from gestion_creditos.email_service import (
    enviar_resumen_cuotas_pendientes_pagador,
    preparar_resumen_cuotas_pendientes_pagador,
)
from gestion_creditos.models import Credito, CuotaAmortizacion


logger = logging.getLogger(__name__)


def es_ultimo_dia_del_mes(fecha):
    return (fecha + timedelta(days=1)).month != fecha.month


def resolver_fecha_corte_resumen_pagador(fecha_referencia):
    if es_ultimo_dia_del_mes(fecha_referencia):
        return fecha_referencia, 'month_end'

    ayer = fecha_referencia - timedelta(days=1)
    if es_ultimo_dia_del_mes(ayer):
        return ayer, 'catchup_day_after'

    return None, 'not_month_end'


def preparar_lotes_resumen_pagador(
    *,
    fecha_referencia=None,
    exigir_ventana_mensual=True,
):
    fecha_referencia = fecha_referencia or timezone.localdate()

    if not getattr(settings, 'PAGADOR_MONTHLY_PENDING_NOTIFICATIONS_ENABLED', True):
        return {
            'status': 'skipped',
            'reason': 'disabled',
            'fecha_referencia': fecha_referencia,
            'batches': [],
        }

    fecha_corte, ventana = resolver_fecha_corte_resumen_pagador(fecha_referencia)
    if exigir_ventana_mensual and not fecha_corte:
        return {
            'status': 'skipped',
            'reason': 'not_month_end',
            'fecha_referencia': fecha_referencia,
            'batches': [],
        }

    fecha_corte = fecha_corte or fecha_referencia
    cuotas = (
        CuotaAmortizacion.objects.filter(
            pagada=False,
            fecha_vencimiento__lte=fecha_corte,
            credito__linea__in=[
                Credito.LineaCredito.LIBRANZA,
                Credito.LineaCredito.ADELANTO_NOMINA,
            ],
            credito__estado__in=[
                Credito.EstadoCredito.ACTIVO,
                Credito.EstadoCredito.EN_MORA,
            ],
        )
        .select_related(
            'credito__detalle_libranza__empresa',
            'credito__detalle_adelanto_nomina__vinculo_laboral__empresa',
            'credito__usuario',
        )
        .order_by('fecha_vencimiento', 'credito__numero_credito')
    )

    cuotas_por_empresa = {}
    cuotas_evaluadas = 0
    cuotas_omitidas_ya_enviadas = 0
    cuotas_sin_empresa = 0

    for cuota in cuotas:
        cuotas_evaluadas += 1
        ultima = cuota.fecha_ultimo_recordatorio_pagador
        if ultima:
            ultima_fecha = timezone.localtime(ultima).date()
            if ultima_fecha.month == fecha_corte.month and ultima_fecha.year == fecha_corte.year:
                cuotas_omitidas_ya_enviadas += 1
                continue

        empresa = cuota.credito.empresa_relacionada
        if not empresa:
            cuotas_sin_empresa += 1
            continue

        cuotas_por_empresa.setdefault(empresa.id, {'empresa': empresa, 'cuotas': []})
        cuotas_por_empresa[empresa.id]['cuotas'].append(cuota)

    batches = []
    empresas_sin_destinatarios = []
    for data in cuotas_por_empresa.values():
        payload = preparar_resumen_cuotas_pendientes_pagador(
            empresa=data['empresa'],
            cuotas=data['cuotas'],
            fecha_corte=fecha_corte,
        )
        if not payload['destinatarios']:
            empresas_sin_destinatarios.append(data['empresa'].nombre)
            continue
        batches.append(payload)

    logger.info(
        'Resumen pagador preparado | fecha_referencia=%s fecha_corte=%s ventana=%s cuotas=%s batches=%s sin_destinatarios=%s omitidas_ya_enviadas=%s sin_empresa=%s',
        fecha_referencia,
        fecha_corte,
        ventana,
        cuotas_evaluadas,
        len(batches),
        len(empresas_sin_destinatarios),
        cuotas_omitidas_ya_enviadas,
        cuotas_sin_empresa,
    )

    return {
        'status': 'ready',
        'fecha_referencia': fecha_referencia,
        'fecha_corte': fecha_corte,
        'window': ventana,
        'batches': batches,
        'diagnostics': {
            'cuotas_evaluadas': cuotas_evaluadas,
            'cuotas_omitidas_ya_enviadas': cuotas_omitidas_ya_enviadas,
            'cuotas_sin_empresa': cuotas_sin_empresa,
            'empresas_con_cuotas': len(cuotas_por_empresa),
            'empresas_con_envio': len(batches),
            'empresas_sin_destinatarios': empresas_sin_destinatarios,
        },
    }


def enviar_resumenes_pagador(
    *,
    fecha_referencia=None,
    exigir_ventana_mensual=True,
    destinatarios_override=None,
    include_internal_cc=True,
    marcar_enviado=False,
    empresa_id=None,
):
    prepared = preparar_lotes_resumen_pagador(
        fecha_referencia=fecha_referencia,
        exigir_ventana_mensual=exigir_ventana_mensual,
    )
    if prepared['status'] != 'ready':
        return prepared

    destinatarios_override = (
        list(destinatarios_override)
        if destinatarios_override is not None
        else None
    )
    enviados = 0
    empresas_filtradas = 0
    resultados = []

    for batch in prepared['batches']:
        empresa = batch['empresa']
        if empresa_id and empresa.id != empresa_id:
            continue

        empresas_filtradas += 1
        cc_override = None if include_internal_cc else []
        if destinatarios_override is not None:
            cc_override = batch['internos'] if include_internal_cc else []

        enviado = enviar_resumen_cuotas_pendientes_pagador(
            empresa=empresa,
            cuotas=batch['cuotas'],
            fecha_corte=prepared['fecha_corte'],
            destinatarios_override=destinatarios_override,
            cc_override=cc_override,
        )
        if enviado:
            enviados += 1
            if marcar_enviado:
                CuotaAmortizacion.objects.filter(
                    pk__in=[cuota.pk for cuota in batch['cuotas']]
                ).update(fecha_ultimo_recordatorio_pagador=timezone.now())

        resultados.append({
            'empresa_id': empresa.id,
            'empresa': empresa.nombre,
            'cuotas': len(batch['cuotas']),
            'total': batch['total'],
            'destinatarios': destinatarios_override or batch['destinatarios'],
            'cc': batch['internos'] if include_internal_cc else [],
            'enviado': enviado,
        })

    return {
        'status': 'success',
        'fecha_referencia': prepared['fecha_referencia'],
        'fecha_corte': prepared['fecha_corte'],
        'window': prepared['window'],
        'empresas_evaluadas': empresas_filtradas,
        'empresas_notificadas': enviados,
        'diagnostics': prepared['diagnostics'],
        'resultados': resultados,
    }

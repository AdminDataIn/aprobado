from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import Credito, CuotaAmortizacion


UMBRAL_AVISO_COLABORADOR_DIAS = 10

SEVERIDAD_IMPORTANTE = 'IMPORTANTE'
SEVERIDAD_ALTA = 'ALTA'
SEVERIDAD_CRITICA = 'CRITICA'

_PRESENTACION_SEVERIDAD = {
    SEVERIDAD_IMPORTANTE: {
        'severidad_etiqueta': 'Importante',
        'severidad_fondo': '#FEF3C7',
        'severidad_color': '#92400E',
        'severidad_borde': '#F59E0B',
    },
    SEVERIDAD_ALTA: {
        'severidad_etiqueta': 'Alta',
        'severidad_fondo': '#FFEDD5',
        'severidad_color': '#9A3412',
        'severidad_borde': '#F97316',
    },
    SEVERIDAD_CRITICA: {
        'severidad_etiqueta': 'Crítica',
        'severidad_fondo': '#FEE2E2',
        'severidad_color': '#991B1B',
        'severidad_borde': '#DC2626',
    },
}


def clasificar_severidad_mora(dias_mora):
    dias_mora = int(dias_mora)
    if dias_mora < UMBRAL_AVISO_COLABORADOR_DIAS:
        return None
    if dias_mora < 15:
        return SEVERIDAD_IMPORTANTE
    if dias_mora < 30:
        return SEVERIDAD_ALTA
    return SEVERIDAD_CRITICA


def preparar_alerta_mora_colaborador(*, dias_mora, numero_credito):
    dias_mora = int(dias_mora)
    severidad = clasificar_severidad_mora(dias_mora)
    if severidad is None:
        return None

    if severidad == SEVERIDAD_IMPORTANTE:
        asunto = 'Importante: tu crédito continúa pendiente de normalización'
    elif severidad == SEVERIDAD_ALTA:
        asunto = f'Alerta alta: tu crédito lleva {dias_mora} días en mora'
    else:
        asunto = f'Alerta crítica: tu crédito continúa en mora por {dias_mora} días'

    return {
        'asunto': f'{asunto} - {numero_credito}',
        'dias_mora': dias_mora,
        'severidad_codigo': severidad,
        **_PRESENTACION_SEVERIDAD[severidad],
    }


def procesar_alertas_mora_colaborador(*, instante_referencia=None, enviar_alerta=None):
    if enviar_alerta is None:
        from gestion_creditos.email_service import enviar_alerta_obligacion_pendiente_usuario

        enviar_alerta = enviar_alerta_obligacion_pendiente_usuario

    ahora = instante_referencia or timezone.now()
    hoy = timezone.localtime(ahora).date()
    fecha_limite = hoy - timedelta(days=UMBRAL_AVISO_COLABORADOR_DIAS)
    estados_vigentes = [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]
    lineas_permitidas = [
        Credito.LineaCredito.LIBRANZA,
        Credito.LineaCredito.ADELANTO_NOMINA,
    ]

    creditos_ids = list(
        CuotaAmortizacion.objects.filter(
            pagada=False,
            fecha_vencimiento__lte=fecha_limite,
            credito__linea__in=lineas_permitidas,
            credito__estado__in=estados_vigentes,
            fecha_ultimo_recordatorio_pagador__isnull=False,
        )
        .order_by()
        .values_list('credito_id', flat=True)
        .distinct()
    )

    enviados = 0
    omitidos_duplicado = 0
    for credito_id in creditos_ids:
        with transaction.atomic():
            credito = (
                Credito.objects.select_for_update(of=('self',))
                .select_related('usuario')
                .get(pk=credito_id)
            )
            if credito.estado not in estados_vigentes:
                continue

            if CuotaAmortizacion.objects.filter(
                credito=credito,
                fecha_ultimo_aviso_usuario_mora__date=hoy,
            ).exists():
                omitidos_duplicado += 1
                continue

            cuota = (
                CuotaAmortizacion.objects.select_for_update()
                .filter(
                    credito=credito,
                    pagada=False,
                    fecha_vencimiento__lte=fecha_limite,
                    fecha_ultimo_recordatorio_pagador__isnull=False,
                )
                .order_by('fecha_vencimiento', 'numero_cuota')
                .first()
            )
            if cuota is None:
                continue

            dias_mora = (hoy - cuota.fecha_vencimiento).days
            if clasificar_severidad_mora(dias_mora) is None:
                continue

            if enviar_alerta(
                credito=credito,
                cuota=cuota,
                dias_atraso=dias_mora,
            ):
                cuota.fecha_ultimo_aviso_usuario_mora = ahora
                cuota.save(update_fields=['fecha_ultimo_aviso_usuario_mora'])
                enviados += 1

    return {
        'alertas_enviadas': enviados,
        'creditos_evaluados': len(creditos_ids),
        'omitidos_duplicado': omitidos_duplicado,
        'fecha_referencia': hoy.isoformat(),
    }

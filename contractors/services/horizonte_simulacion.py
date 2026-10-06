from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal, InvalidOperation

from gestion_creditos.services.horizonte_contractual import calcular_horizonte_contractual
from contractors.services.politica_financiera_prestador import VERSION
from gestion_creditos.services.libranza_rules import (
    calcular_primera_fecha_pago_libranza,
    sumar_meses_con_dia_ancla,
)


def fecha_ultima_cuota_proyectada(corte, plazo):
    if not isinstance(plazo, int) or isinstance(plazo, bool) or plazo < 1:
        raise ValidationError('Plazo proyectado invalido.')
    primera = calcular_primera_fecha_pago_libranza(fecha_aprobacion=corte)
    return sumar_meses_con_dia_ancla(primera, plazo - 1, primera.day)


def plazo_maximo_respaldado(horizonte, plazo_maximo):
    if not horizonte.disponible or not horizonte.flujos_futuros:
        return 0
    limite = min(plazo_maximo, horizonte.horizonte_crediticio_disponible)
    # El core difiere la primera cuota despues del dia 14; contar flujos no basta.
    while limite > 0:
        if fecha_ultima_cuota_proyectada(horizonte.corte, limite) <= horizonte.fecha_ultimo_flujo_contractual:
            return limite
        limite -= 1
    return 0


def horizonte_para_solicitud(solicitud, configuracion, *, corte=None):
    if configuracion is None or configuracion.version != VERSION:
        return None
    extraido = (solicitud.metadata_analisis_contractual or {}).get('datos_sugeridos', {}).get('valor_pagado_estimado')
    try:
        pagado_fuente = Decimal(str(extraido)) if extraido is not None and extraido != '' else None
        if pagado_fuente is not None and (not pagado_fuente.is_finite() or pagado_fuente < 0):
            pagado_fuente = None
    except InvalidOperation:
        pagado_fuente = None
    # No promover el pagado declarado ni el extraido sin fecha a evidencia verificada.
    return calcular_horizonte_contractual(
        inicio=solicitud.fecha_inicio_contrato, fin=solicitud.fecha_fin_contrato,
        periodicidad=solicitud.forma_pago, valor_periodico=solicitud.valor_mensual_contractual,
        evidencia_calendario=solicitud.evidencia_forma_pago,
        duracion_meses=solicitud.duracion_contrato_meses,
        corte=corte or timezone.localdate(),
        valor_pagado_al_corte_documento=pagado_fuente,
        valor_pagado_actual_declarado=solicitud.valor_pagado_contrato,
    )


def validar_plazo_contractual(solicitud, configuracion, plazo, *, corte=None):
    horizonte = horizonte_para_solicitud(solicitud, configuracion, corte=corte)
    if horizonte is not None:
        if not horizonte.disponible:
            raise ValidationError(horizonte.motivo)
        if not 1 <= plazo <= plazo_maximo_respaldado(horizonte, min(8, configuracion.plazo_maximo_meses)):
            raise ValidationError('El plazo o su ultima cuota supera los flujos contractuales respaldantes.')
    return horizonte

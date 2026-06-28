from decimal import Decimal
import logging

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from gestion_creditos.models import Credito, CuotaAmortizacion
from gestion_creditos.services.libranza_rules import (
    obtener_dia_ancla_vencimiento,
    obtener_fecha_primera_cuota_credito,
    obtener_plazo_credito_aplicado,
    obtener_tasa_credito_aplicada,
    sumar_meses_con_dia_ancla,
)
from gestion_creditos.services.tasa_service import obtener_tasa_credito


logger = logging.getLogger(__name__)


def activar_credito(credito):
    """
    Activa un credito generando los calculos financieros y la tabla de amortizacion.

    Mantiene el comportamiento historico de ``gestion_creditos.credit_services``:
    actualiza el credito, fija fecha de desembolso si falta y crea cuotas.
    """
    logger.info(f"Iniciando activacion del credito {credito.numero_credito} (ID: {credito.id})")

    if not credito.monto_aprobado or not credito.plazo:
        error_msg = f"No se puede activar el credito {credito.numero_credito}: falta monto_aprobado o plazo"
        logger.error(error_msg)
        raise ValueError(error_msg)

    if credito.tasa_interes:
        tasa_interes = credito.tasa_interes
    else:
        tasa_interes = obtener_tasa_credito(credito.linea)

    plazo_aplicado = obtener_plazo_credito_aplicado(credito)
    tasa_interes = obtener_tasa_credito_aplicada(credito, tasa_interes)

    comision = credito.comision or (credito.monto_aprobado * Decimal('0.10'))
    iva_comision = credito.iva_comision or (comision * Decimal('0.19'))
    capital_financiado = credito.monto_aprobado + comision + iva_comision

    usar_condiciones_historicas = (
        credito.tipo_regla_credito == Credito.TipoReglaCredito.ESPECIAL
        and credito.valor_cuota is not None
        and credito.total_a_pagar is not None
    )

    tasa_mensual = tasa_interes / Decimal(100)
    if usar_condiciones_historicas:
        valor_cuota = credito.valor_cuota
        total_a_pagar = credito.total_a_pagar
    else:
        if tasa_mensual > 0:
            factor = (tasa_mensual * (1 + tasa_mensual) ** plazo_aplicado) / (
                ((1 + tasa_mensual) ** plazo_aplicado) - 1
            )
            valor_cuota = capital_financiado * factor
        else:
            valor_cuota = capital_financiado / plazo_aplicado

        total_a_pagar = valor_cuota * plazo_aplicado

    credito.tasa_interes = tasa_interes
    credito.plazo = plazo_aplicado
    credito.comision = comision
    credito.iva_comision = iva_comision
    credito.total_a_pagar = total_a_pagar
    credito.valor_cuota = valor_cuota
    credito.saldo_pendiente = capital_financiado
    credito.capital_pendiente = credito.monto_aprobado

    hoy = timezone.now().date()
    credito.fecha_proximo_pago = obtener_fecha_primera_cuota_credito(credito, hoy)

    if not credito.fecha_desembolso:
        credito.fecha_desembolso = timezone.now()
    credito.save()

    saldo_capital_restante = capital_financiado
    fecha_cuota = credito.fecha_proximo_pago
    dia_ancla = obtener_dia_ancla_vencimiento(credito, fecha_cuota)

    cuotas = []
    for i in range(1, plazo_aplicado + 1):
        interes_a_pagar = saldo_capital_restante * tasa_mensual
        capital_a_pagar = credito.valor_cuota - interes_a_pagar

        if i == plazo_aplicado:
            capital_a_pagar = saldo_capital_restante
            interes_a_pagar = credito.valor_cuota - capital_a_pagar
            if interes_a_pagar < 0:
                interes_a_pagar = Decimal('0.00')
                capital_a_pagar = credito.valor_cuota

        saldo_capital_restante -= capital_a_pagar

        if saldo_capital_restante < 0:
            saldo_capital_restante = Decimal('0.00')

        cuotas.append(
            CuotaAmortizacion(
                credito=credito,
                numero_cuota=i,
                fecha_vencimiento=fecha_cuota,
                capital_a_pagar=capital_a_pagar,
                interes_a_pagar=interes_a_pagar,
                valor_cuota=credito.valor_cuota,
                saldo_capital_pendiente=saldo_capital_restante,
            )
        )

        if credito.linea == Credito.LineaCredito.LIBRANZA:
            fecha_cuota = sumar_meses_con_dia_ancla(fecha_cuota, 1, dia_ancla)
        else:
            fecha_cuota += relativedelta(months=1)

    if cuotas:
        CuotaAmortizacion.objects.bulk_create(cuotas, ignore_conflicts=True)

    logger.info(
        f"Credito {credito.numero_credito} activado exitosamente. "
        f"Linea: {credito.get_linea_display()}, Tasa: {tasa_interes}% mensual, "
        f"Cuota: ${valor_cuota:,.2f}, Plazo: {credito.plazo} meses, "
        f"Total a pagar: ${total_a_pagar:,.2f}"
    )

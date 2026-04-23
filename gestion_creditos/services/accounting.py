from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Exists, OuterRef, Sum

from gestion_creditos.models import Credito, DetalleContablePago, HistorialEstado


TWOPLACES = Decimal('0.01')


def _q(value):
    return Decimal(value or '0.00').quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _split_capital_components(credito, capital_aplicado):
    capital_aplicado = _q(capital_aplicado)
    principal_total = _q(getattr(credito, 'monto_aprobado', Decimal('0.00')))
    comision_total = _q(getattr(credito, 'comision', Decimal('0.00')))
    iva_total = _q(getattr(credito, 'iva_comision', Decimal('0.00')))
    capital_financiado_total = principal_total + comision_total + iva_total

    if capital_aplicado <= Decimal('0.00') or capital_financiado_total <= Decimal('0.00'):
        return {
            'capital_principal_aplicado': Decimal('0.00'),
            'comision_aplicada': Decimal('0.00'),
            'iva_aplicado': Decimal('0.00'),
        }

    principal_aplicado = (capital_aplicado * (principal_total / capital_financiado_total)).quantize(
        TWOPLACES, rounding=ROUND_HALF_UP
    )
    comision_aplicada = (capital_aplicado * (comision_total / capital_financiado_total)).quantize(
        TWOPLACES, rounding=ROUND_HALF_UP
    )
    iva_aplicado = capital_aplicado - principal_aplicado - comision_aplicada
    if iva_aplicado < Decimal('0.00'):
        iva_aplicado = Decimal('0.00')
        principal_aplicado = capital_aplicado - comision_aplicada

    return {
        'capital_principal_aplicado': _q(principal_aplicado),
        'comision_aplicada': _q(comision_aplicada),
        'iva_aplicado': _q(iva_aplicado),
    }


def build_payment_accounting_entry(*, credito, cuota, monto_aplicado):
    monto_aplicado = _q(monto_aplicado)
    if monto_aplicado <= Decimal('0.00'):
        return None

    acumulado = cuota.detalles_contables_pago.aggregate(
        capital=Sum('capital_aplicado'),
        interes=Sum('interes_aplicado'),
    )
    capital_ya_aplicado = _q(acumulado.get('capital'))
    interes_ya_aplicado = _q(acumulado.get('interes'))

    interes_pendiente = max(_q(cuota.interes_a_pagar) - interes_ya_aplicado, Decimal('0.00'))
    capital_pendiente = max(_q(cuota.capital_a_pagar) - capital_ya_aplicado, Decimal('0.00'))

    interes_aplicado = min(monto_aplicado, interes_pendiente)
    capital_aplicado = monto_aplicado - interes_aplicado

    if capital_aplicado > capital_pendiente:
        excedente = capital_aplicado - capital_pendiente
        capital_aplicado = capital_pendiente
        interes_aplicado = min(interes_aplicado + excedente, interes_pendiente)

    capital_aplicado = _q(capital_aplicado)
    interes_aplicado = _q(interes_aplicado)
    componentes = _split_capital_components(credito, capital_aplicado)

    return {
        'credito': credito,
        'cuota': cuota,
        'monto_total_aplicado': monto_aplicado,
        'capital_aplicado': capital_aplicado,
        'interes_aplicado': interes_aplicado,
        **componentes,
        'metodologia_calculo': DetalleContablePago.MetodologiaCalculo.CUOTA_INTERES_PRIMERO,
    }


def registrar_detalle_contable_pago(*, pago, aplicaciones):
    detalles = []
    total_capital = Decimal('0.00')
    total_interes = Decimal('0.00')

    for secuencia, aplicacion in enumerate(aplicaciones, start=1):
        detalle = build_payment_accounting_entry(
            credito=aplicacion['credito'],
            cuota=aplicacion['cuota'],
            monto_aplicado=aplicacion['monto_aplicado'],
        )
        if not detalle:
            continue
        detalle['pago'] = pago
        detalle['fecha_aplicacion'] = pago.fecha_aplicacion
        detalle['secuencia_aplicacion'] = secuencia
        detalles.append(DetalleContablePago(**detalle))
        total_capital += detalle['capital_aplicado']
        total_interes += detalle['interes_aplicado']

    if detalles:
        DetalleContablePago.objects.bulk_create(detalles)

    pago.capital_abonado = _q(total_capital)
    pago.intereses_pagados = _q(total_interes)
    pago.save(update_fields=['capital_abonado', 'intereses_pagados'])
    return detalles


def registrar_detalle_contable_abono_capital(*, pago, credito, monto_aplicado):
    monto_aplicado = _q(monto_aplicado)
    if monto_aplicado <= Decimal('0.00'):
        pago.capital_abonado = Decimal('0.00')
        pago.intereses_pagados = Decimal('0.00')
        pago.save(update_fields=['capital_abonado', 'intereses_pagados'])
        return None

    componentes = _split_capital_components(credito, monto_aplicado)
    detalle = DetalleContablePago.objects.create(
        pago=pago,
        credito=credito,
        cuota=None,
        secuencia_aplicacion=1,
        fecha_aplicacion=pago.fecha_aplicacion,
        monto_total_aplicado=monto_aplicado,
        capital_aplicado=monto_aplicado,
        interes_aplicado=Decimal('0.00'),
        capital_principal_aplicado=componentes['capital_principal_aplicado'],
        comision_aplicada=componentes['comision_aplicada'],
        iva_aplicado=componentes['iva_aplicado'],
        metodologia_calculo=DetalleContablePago.MetodologiaCalculo.ABONO_CAPITAL_DIRECTO,
    )
    pago.capital_abonado = monto_aplicado
    pago.intereses_pagados = Decimal('0.00')
    pago.save(update_fields=['capital_abonado', 'intereses_pagados'])
    return detalle


def get_platform_disbursed_creditos_queryset(base_qs=None):
    base_qs = base_qs if base_qs is not None else Credito.objects.all()
    pendiente_transferencia = HistorialEstado.objects.filter(
        credito_id=OuterRef('pk'),
        estado_nuevo=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
    )
    desembolso_confirmado = HistorialEstado.objects.filter(
        credito_id=OuterRef('pk'),
        estado_nuevo=Credito.EstadoCredito.ACTIVO,
        comprobante_pago__isnull=False,
    )
    return (
        base_qs.filter(fecha_desembolso__isnull=False)
        .annotate(
            tiene_pendiente_transferencia=Exists(pendiente_transferencia),
            tiene_desembolso_confirmado=Exists(desembolso_confirmado),
        )
        .filter(
            tiene_pendiente_transferencia=True,
            tiene_desembolso_confirmado=True,
        )
    )


def get_accounting_summary_for_creditos(creditos_qs):
    detalles_qs = DetalleContablePago.objects.filter(credito__in=creditos_qs)
    agregados = detalles_qs.aggregate(
        total_recaudado=Sum('monto_total_aplicado'),
        capital_aplicado=Sum('capital_aplicado'),
        interes_aplicado=Sum('interes_aplicado'),
        capital_principal_aplicado=Sum('capital_principal_aplicado'),
        comision_aplicada=Sum('comision_aplicada'),
        iva_aplicado=Sum('iva_aplicado'),
    )
    resumen = {key: _q(value) for key, value in agregados.items()}
    resumen['supports_breakdown'] = detalles_qs.exists()
    resumen['creditos_con_trazabilidad'] = detalles_qs.values('credito_id').distinct().count()
    resumen['pagos_con_trazabilidad'] = detalles_qs.values('pago_id').distinct().count()
    return resumen


@transaction.atomic
def backfill_accounting_for_credito(*, credito, overwrite=False):
    if overwrite:
        DetalleContablePago.objects.filter(credito=credito).delete()
    elif DetalleContablePago.objects.filter(credito=credito).exists():
        return {
            'credito': credito,
            'pagos_procesados': 0,
            'detalles_creados': 0,
            'omitido': True,
        }

    cuotas_estado = [
        {
            'cuota': cuota,
            'restante': _q(cuota.valor_cuota),
        }
        for cuota in credito.tabla_amortizacion.order_by('numero_cuota')
    ]

    pagos = credito.historial_pagos.filter(
        estado=credito.historial_pagos.model.EstadoPago.EXITOSO
    ).order_by('fecha_aplicacion', 'fecha_pago', 'id')

    total_detalles = 0
    pagos_procesados = 0

    for pago in pagos:
        if not overwrite and pago.detalles_contables.exists():
            continue
        if overwrite:
            pago.detalles_contables.all().delete()

        monto_restante = _q(pago.monto)
        aplicaciones = []

        for cuota_estado in cuotas_estado:
            if monto_restante <= Decimal('0.00'):
                break

            restante_cuota = cuota_estado['restante']
            if restante_cuota <= Decimal('0.00'):
                continue

            monto_aplicado = min(monto_restante, restante_cuota)
            if monto_aplicado <= Decimal('0.00'):
                continue

            aplicaciones.append({
                'credito': credito,
                'cuota': cuota_estado['cuota'],
                'monto_aplicado': monto_aplicado,
            })
            cuota_estado['restante'] = _q(restante_cuota - monto_aplicado)
            monto_restante = _q(monto_restante - monto_aplicado)

        detalles = registrar_detalle_contable_pago(pago=pago, aplicaciones=aplicaciones)
        total_detalles += len(detalles)
        pagos_procesados += 1

    return {
        'credito': credito,
        'pagos_procesados': pagos_procesados,
        'detalles_creados': total_detalles,
        'omitido': False,
    }

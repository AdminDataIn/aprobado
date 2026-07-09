from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone


TASA_MENSUAL_PRELIMINAR_DEFAULT = Decimal('0.019')
PLAZO_MAXIMO_MESES_DEFAULT = 24
MONTO_MINIMO_DEFAULT = Decimal('1000000')
MONTO_MAXIMO_DEFAULT = Decimal('10000000')


@dataclass(frozen=True)
class ResultadoCapacidadContractualPreliminar:
    solicitud_id: int
    valor_total_contrato: Decimal | None
    valor_pendiente_cobrar: Decimal | None
    monto_solicitado: Decimal | None
    plazo_solicitado: int | None
    cuota_estimada_preliminar: Decimal | None
    porcentaje_compromiso_valor_pendiente: Decimal | None
    tasa_mensual_preliminar: Decimal
    documentos_completos: bool
    calculable: bool
    advertencias: list[str] = field(default_factory=list)
    bloqueos: list[str] = field(default_factory=list)
    fuente: str = 'simulacion_prestadores_read_only'


def evaluar_capacidad_contractual_preliminar(solicitud, documentos_completos=False):
    tasa_mensual = _decimal_setting(
        'CONTRACTORS_PRELIMINARY_MONTHLY_RATE',
        TASA_MENSUAL_PRELIMINAR_DEFAULT,
    )
    plazo_maximo = int(getattr(settings, 'CONTRACTORS_MAX_TERM_MONTHS', PLAZO_MAXIMO_MESES_DEFAULT))
    monto_minimo = _decimal_setting('CONTRACTORS_MIN_AMOUNT', MONTO_MINIMO_DEFAULT)
    monto_maximo = _decimal_setting('CONTRACTORS_MAX_AMOUNT', MONTO_MAXIMO_DEFAULT)

    advertencias = []
    bloqueos = []

    valor_total = solicitud.valor_total_contrato
    valor_pendiente = solicitud.valor_pendiente_cobrar
    monto = solicitud.monto_solicitado
    plazo = solicitud.plazo_meses

    if not documentos_completos:
        advertencias.append('Faltan documentos obligatorios para completar la evaluacion.')

    if valor_pendiente is None:
        bloqueos.append('Ingresa el valor pendiente por cobrar del contrato.')
    elif valor_pendiente <= 0:
        bloqueos.append('El valor pendiente por cobrar debe ser mayor a cero.')

    if monto is None:
        bloqueos.append('Ingresa el monto solicitado.')
    elif monto < monto_minimo:
        advertencias.append('El monto solicitado esta por debajo del minimo preliminar configurado.')
    elif monto > monto_maximo:
        advertencias.append('El monto solicitado supera el maximo preliminar configurado.')

    if valor_pendiente is not None and monto is not None and monto > valor_pendiente:
        advertencias.append('El monto solicitado supera el valor pendiente por cobrar del contrato.')

    if plazo is None:
        bloqueos.append('Ingresa el plazo solicitado.')
    elif plazo <= 0:
        bloqueos.append('El plazo solicitado debe ser mayor a cero.')
    elif plazo > plazo_maximo:
        advertencias.append('El plazo solicitado supera el maximo preliminar configurado.')

    if solicitud.fecha_fin_contrato and solicitud.fecha_fin_contrato < timezone.localdate():
        advertencias.append('El contrato registrado se encuentra vencido.')

    cuota = None
    porcentaje = None
    calculable = not bloqueos and monto is not None and plazo is not None and plazo > 0
    if calculable:
        cuota = _calcular_cuota_estimada(monto, plazo, tasa_mensual)

    if valor_pendiente and valor_pendiente > 0 and monto is not None:
        porcentaje = _redondear((monto / valor_pendiente) * Decimal('100'))

    return ResultadoCapacidadContractualPreliminar(
        solicitud_id=solicitud.id,
        valor_total_contrato=valor_total,
        valor_pendiente_cobrar=valor_pendiente,
        monto_solicitado=monto,
        plazo_solicitado=plazo,
        cuota_estimada_preliminar=cuota,
        porcentaje_compromiso_valor_pendiente=porcentaje,
        tasa_mensual_preliminar=tasa_mensual,
        documentos_completos=documentos_completos,
        calculable=calculable,
        advertencias=advertencias,
        bloqueos=bloqueos,
    )


def _calcular_cuota_estimada(monto, plazo_meses, tasa_mensual):
    monto = Decimal(monto)
    tasa = Decimal(tasa_mensual)
    plazo = int(plazo_meses)
    if tasa == 0:
        return _redondear(monto / Decimal(plazo))
    factor = (Decimal('1') + tasa) ** plazo
    cuota = monto * ((tasa * factor) / (factor - Decimal('1')))
    return _redondear(cuota)


def _decimal_setting(nombre, default):
    valor = getattr(settings, nombre, default)
    return Decimal(str(valor))


def _redondear(valor):
    return Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

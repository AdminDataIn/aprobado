from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone


TASA_MENSUAL_PRELIMINAR_DEFAULT = Decimal('0.019')
PLAZO_MAXIMO_MESES_DEFAULT = 24
MONTO_MINIMO_DEFAULT = Decimal('1000000')
MONTO_MAXIMO_DEFAULT = Decimal('10000000')
TASA_ORIGINACION_DEFAULT = Decimal('0.10')
TASA_IVA_DEFAULT = Decimal('0.19')
TASA_SEGURO_VIDA_DEFAULT = Decimal('0.003711')
TASA_GARANTIA_DEFAULT = Decimal('0.02')


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


@dataclass(frozen=True)
class ResultadoSimulacionPrestadorInformativa:
    monto_solicitado: Decimal
    plazo_meses: int
    tasa_mensual: Decimal
    tasa_mensual_porcentaje: Decimal
    costo_originacion: Decimal
    iva_costo_originacion: Decimal
    seguro_vida: Decimal
    fondo_garantia: Decimal
    capital_total_financiado: Decimal
    intereses_estimados: Decimal
    total_a_pagar: Decimal
    cuota_mensual: Decimal
    fuente: str = 'simulacion_prestadores_informativa_v1'

    def como_dict(self):
        return {
            'monto_solicitado': self.monto_solicitado,
            'plazo_meses': self.plazo_meses,
            'tasa_mensual': self.tasa_mensual,
            'tasa_mensual_porcentaje': self.tasa_mensual_porcentaje,
            'costo_originacion': self.costo_originacion,
            'iva_costo_originacion': self.iva_costo_originacion,
            'seguro_vida': self.seguro_vida,
            'fondo_garantia': self.fondo_garantia,
            'capital_total_financiado': self.capital_total_financiado,
            'intereses_estimados': self.intereses_estimados,
            'total_a_pagar': self.total_a_pagar,
            'cuota_mensual': self.cuota_mensual,
            'fuente': self.fuente,
        }


def obtener_configuracion_simulador_prestador():
    from contractors.models import ConfiguracionSimuladorPrestador

    return ConfiguracionSimuladorPrestador.objects.filter(activo=True).first()


def obtener_configuracion_publica_simulador_prestador(configuracion=None):
    monto_minimo = Decimal(str(
        configuracion.monto_minimo
        if configuracion else getattr(settings, 'CONTRACTORS_MIN_AMOUNT', MONTO_MINIMO_DEFAULT)
    ))
    monto_maximo = Decimal(str(
        configuracion.monto_maximo
        if configuracion else getattr(settings, 'CONTRACTORS_MAX_AMOUNT', MONTO_MAXIMO_DEFAULT)
    ))
    plazo_minimo = int(
        configuracion.plazo_minimo_meses
        if configuracion else getattr(settings, 'CONTRACTORS_MIN_TERM_MONTHS', 3)
    )
    plazo_maximo = int(
        configuracion.plazo_maximo_meses
        if configuracion else getattr(settings, 'CONTRACTORS_MAX_TERM_MONTHS', PLAZO_MAXIMO_MESES_DEFAULT)
    )
    return {
        'monto_minimo': str(monto_minimo),
        'monto_maximo': str(monto_maximo),
        'plazo_minimo_meses': plazo_minimo,
        'plazo_maximo_meses': plazo_maximo,
        'tasa_mensual': str(_tasa_configurada(
            configuracion, 'tasa_mensual',
            'CONTRACTORS_PRELIMINARY_MONTHLY_RATE', TASA_MENSUAL_PRELIMINAR_DEFAULT,
        )),
        'tasa_originacion': str(_tasa_configurada(
            configuracion, 'porcentaje_originacion',
            'CONTRACTORS_PRELIMINARY_ORIGINATION_RATE', TASA_ORIGINACION_DEFAULT,
        )),
        'tasa_iva_originacion': str(_tasa_configurada(
            configuracion, 'porcentaje_iva_originacion',
            'CONTRACTORS_PRELIMINARY_VAT_RATE', TASA_IVA_DEFAULT,
        )),
        'tasa_fondo_garantia': str(_tasa_configurada(
            configuracion, 'porcentaje_fondo_garantia',
            'CONTRACTORS_PRELIMINARY_GUARANTEE_RATE', TASA_GARANTIA_DEFAULT,
        )),
        'tasa_seguro_vida': str(_tasa_configurada(
            configuracion, 'porcentaje_seguro_vida_primera_cuota',
            'CONTRACTORS_PRELIMINARY_LIFE_INSURANCE_RATE', TASA_SEGURO_VIDA_DEFAULT,
        )),
    }


def simular_credito_prestador_informativo(*, monto, plazo_meses, configuracion=None):
    monto = _decimal_or_none(monto)
    plazo_meses = int(plazo_meses)
    if monto is None or monto <= 0 or plazo_meses <= 0:
        raise ValueError('Monto y plazo deben ser mayores a cero.')

    tasa_mensual = _tasa_configurada(
        configuracion, 'tasa_mensual',
        'CONTRACTORS_PRELIMINARY_MONTHLY_RATE', TASA_MENSUAL_PRELIMINAR_DEFAULT,
    )
    tasa_originacion = _tasa_configurada(
        configuracion, 'porcentaje_originacion',
        'CONTRACTORS_PRELIMINARY_ORIGINATION_RATE', TASA_ORIGINACION_DEFAULT,
    )
    tasa_iva = _tasa_configurada(
        configuracion, 'porcentaje_iva_originacion',
        'CONTRACTORS_PRELIMINARY_VAT_RATE', TASA_IVA_DEFAULT,
    )
    tasa_seguro = _tasa_configurada(
        configuracion, 'porcentaje_seguro_vida_primera_cuota',
        'CONTRACTORS_PRELIMINARY_LIFE_INSURANCE_RATE', TASA_SEGURO_VIDA_DEFAULT,
    )
    tasa_garantia = _tasa_configurada(
        configuracion, 'porcentaje_fondo_garantia',
        'CONTRACTORS_PRELIMINARY_GUARANTEE_RATE', TASA_GARANTIA_DEFAULT,
    )

    costo_originacion = _redondear(monto * tasa_originacion)
    iva_originacion = _redondear(costo_originacion * tasa_iva)
    seguro_vida = _redondear(monto * tasa_seguro)
    fondo_garantia = _redondear(monto * tasa_garantia)
    capital_total = _redondear(
        monto + costo_originacion + iva_originacion + seguro_vida + fondo_garantia
    )
    cuota = _calcular_cuota_estimada(capital_total, plazo_meses, tasa_mensual)
    total = _redondear(cuota * Decimal(plazo_meses))
    intereses = _redondear(max(Decimal('0'), total - capital_total))

    return ResultadoSimulacionPrestadorInformativa(
        monto_solicitado=monto,
        plazo_meses=plazo_meses,
        tasa_mensual=tasa_mensual,
        tasa_mensual_porcentaje=_redondear(tasa_mensual * Decimal('100')),
        costo_originacion=costo_originacion,
        iva_costo_originacion=iva_originacion,
        seguro_vida=seguro_vida,
        fondo_garantia=fondo_garantia,
        capital_total_financiado=capital_total,
        intereses_estimados=intereses,
        total_a_pagar=total,
        cuota_mensual=cuota,
    )


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

    valor_total = _decimal_or_none(solicitud.valor_total_contrato)
    valor_pendiente = _decimal_or_none(solicitud.valor_pendiente_cobrar)
    monto = _decimal_or_none(solicitud.monto_solicitado)
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


def _tasa_configurada(configuracion, campo, setting_name, default):
    if configuracion is not None:
        return Decimal(str(getattr(configuracion, campo))) / Decimal('100')
    return _decimal_setting(setting_name, default)


def _decimal_or_none(valor):
    if valor is None or valor == '':
        return None
    return Decimal(str(valor))


def _redondear(valor):
    return Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

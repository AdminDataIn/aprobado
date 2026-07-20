from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ConfiguracionSimuladorNoDisponible(ValidationError):
    pass


@dataclass(frozen=True)
class ResultadoCapacidadContractualPreliminar:
    solicitud_id: int
    valor_total_contrato: Decimal | None
    valor_pendiente_cobrar: Decimal | None
    monto_solicitado: Decimal | None
    plazo_solicitado: int | None
    cuota_estimada_preliminar: Decimal | None
    porcentaje_compromiso_valor_pendiente: Decimal | None
    tasa_mensual_preliminar: Decimal | None
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
    from contractors.models import ConfiguracionScorePrestador, ConfiguracionSimuladorPrestador

    hoy = timezone.localdate()
    politica = (
        ConfiguracionScorePrestador.objects.select_related('configuracion_financiera')
        .filter(activa=True, fecha_vigencia_desde__lte=hoy)
        .filter(
            models.Q(fecha_vigencia_hasta__isnull=True)
            | models.Q(fecha_vigencia_hasta__gte=hoy)
        )
        .first()
    )
    if politica:
        configuracion = politica.configuracion_financiera
        if configuracion and configuracion.activo and configuracion.version:
            return configuracion
        return None

    return ConfiguracionSimuladorPrestador.objects.filter(
        activo=True,
    ).exclude(version='').first()


def snapshot_configuracion_financiera(configuracion):
    if configuracion is None:
        return {
            'version': '',
            'tasa_mensual': None,
            'monto_maximo': None,
            'plazo_maximo_meses': None,
        }
    return {
        'version': str(configuracion.version or ''),
        'tasa_mensual': configuracion.tasa_mensual,
        'monto_maximo': configuracion.monto_maximo,
        'plazo_maximo_meses': configuracion.plazo_maximo_meses,
    }


def obtener_version_politica_simulador(configuracion):
    if configuracion is None:
        return ''
    hoy = timezone.localdate()
    politica = (
        configuracion.politicas_score.filter(
            activa=True,
            fecha_vigencia_desde__lte=hoy,
        )
        .filter(
            models.Q(fecha_vigencia_hasta__isnull=True)
            | models.Q(fecha_vigencia_hasta__gte=hoy)
        )
        .first()
    )
    return str(politica.version_politica or '') if politica else ''


def obtener_configuracion_publica_simulador_prestador(configuracion=None):
    if configuracion is None:
        return {'disponible': False}
    return {
        'disponible': True,
        'version': configuracion.version,
        'monto_minimo': str(configuracion.monto_minimo),
        'monto_maximo': str(configuracion.monto_maximo),
        'plazo_minimo_meses': configuracion.plazo_minimo_meses,
        'plazo_maximo_meses': configuracion.plazo_maximo_meses,
        'tasa_mensual': str(_porcentaje_como_tasa(configuracion.tasa_mensual)),
        'tasa_originacion': str(_porcentaje_como_tasa(configuracion.porcentaje_originacion)),
        'tasa_iva_originacion': str(
            _porcentaje_como_tasa(configuracion.porcentaje_iva_originacion)
        ),
        'tasa_fondo_garantia': str(
            _porcentaje_como_tasa(configuracion.porcentaje_fondo_garantia)
        ),
        'tasa_seguro_vida': str(
            _porcentaje_como_tasa(configuracion.porcentaje_seguro_vida_primera_cuota)
        ),
    }


def simular_credito_prestador_informativo(*, monto, plazo_meses, configuracion=None):
    if configuracion is None:
        raise ConfiguracionSimuladorNoDisponible(
            'La simulacion no esta disponible porque falta configuracion financiera activa.'
        )
    monto = _decimal_or_none(monto)
    plazo_meses = int(plazo_meses)
    if monto is None or monto <= 0 or plazo_meses <= 0:
        raise ValueError('Monto y plazo deben ser mayores a cero.')
    if monto < configuracion.monto_minimo or monto > configuracion.monto_maximo:
        raise ValueError('El monto esta fuera de la configuracion financiera activa.')
    if (
        plazo_meses < configuracion.plazo_minimo_meses
        or plazo_meses > configuracion.plazo_maximo_meses
    ):
        raise ValueError('El plazo esta fuera de la configuracion financiera activa.')

    tasa_mensual = _porcentaje_como_tasa(configuracion.tasa_mensual)
    tasa_originacion = _porcentaje_como_tasa(configuracion.porcentaje_originacion)
    tasa_iva = _porcentaje_como_tasa(configuracion.porcentaje_iva_originacion)
    tasa_seguro = _porcentaje_como_tasa(configuracion.porcentaje_seguro_vida_primera_cuota)
    tasa_garantia = _porcentaje_como_tasa(configuracion.porcentaje_fondo_garantia)

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


def evaluar_capacidad_contractual_preliminar(
    solicitud, documentos_completos=False, configuracion=None,
):
    configuracion = configuracion or obtener_configuracion_simulador_prestador()
    advertencias = []
    bloqueos = []

    if configuracion is None:
        bloqueos.append('La configuracion financiera del simulador no esta disponible.')
        tasa_mensual = None
        plazo_maximo = None
        monto_minimo = None
        monto_maximo = None
    else:
        tasa_mensual = _porcentaje_como_tasa(configuracion.tasa_mensual)
        plazo_maximo = configuracion.plazo_maximo_meses
        monto_minimo = configuracion.monto_minimo
        monto_maximo = configuracion.monto_maximo

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
    elif monto_minimo is not None and monto < monto_minimo:
        advertencias.append('El monto solicitado esta por debajo del minimo preliminar configurado.')
    elif monto_maximo is not None and monto > monto_maximo:
        advertencias.append('El monto solicitado supera el maximo preliminar configurado.')

    if valor_pendiente is not None and monto is not None and monto > valor_pendiente:
        advertencias.append('El monto solicitado supera el valor pendiente por cobrar del contrato.')

    if plazo is None:
        bloqueos.append('Ingresa el plazo solicitado.')
    elif plazo <= 0:
        bloqueos.append('El plazo solicitado debe ser mayor a cero.')
    elif plazo_maximo is not None and plazo > plazo_maximo:
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


def _porcentaje_como_tasa(valor):
    return Decimal(str(valor)) / Decimal('100')


def _decimal_or_none(valor):
    if valor is None or valor == '':
        return None
    return Decimal(str(valor))


def _redondear(valor):
    return Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

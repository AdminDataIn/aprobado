from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from contractors.services.validacion_contractual import validar_contrato_prestador


Q2 = Decimal('0.01')
VERSION_CALCULO_INGRESO = 'ingreso_contractual_mensual_v1'


@dataclass(frozen=True)
class ResultadoIngresoContractualPrestador:
    calculable: bool
    ingreso_mensual: Decimal | None
    metodo: str
    meses_restantes: int
    valor_total: Decimal | None
    saldo_pendiente: Decimal | None
    valor_mensual_explicito: Decimal | None
    requiere_revision_manual: bool
    alertas: tuple[str, ...]
    bloqueos: tuple[str, ...]
    version: str = VERSION_CALCULO_INGRESO

    def como_dict_seguro(self):
        return {
            'calculable': self.calculable,
            'ingreso_mensual': _texto(self.ingreso_mensual),
            'metodo': self.metodo,
            'meses_restantes': self.meses_restantes,
            'valor_total': _texto(self.valor_total),
            'saldo_pendiente': _texto(self.saldo_pendiente),
            'valor_mensual_explicito': _texto(self.valor_mensual_explicito),
            'requiere_revision_manual': self.requiere_revision_manual,
            'alertas': list(self.alertas),
            'bloqueos': list(self.bloqueos),
            'version': self.version,
        }


def calcular_ingreso_contractual_mensual(
    solicitud,
    *,
    tolerancia=Decimal('0.15000'),
    validacion_contractual=None,
):
    validacion = validacion_contractual or validar_contrato_prestador(solicitud)
    tolerancia = _decimal(tolerancia)
    if tolerancia is None or tolerancia < 0 or tolerancia > 1:
        raise ValueError('tolerancia_ingreso_contractual_invalida')

    explicito = _decimal(solicitud.valor_mensual_contractual)
    pendiente = _decimal(solicitud.valor_pendiente_cobrar)
    total = _decimal(solicitud.valor_total_contrato)
    meses = int(validacion.meses_financiables or 0)
    derivado = None
    if pendiente is not None and pendiente > 0 and meses > 0:
        derivado = (pendiente / Decimal(meses)).quantize(Q2, rounding=ROUND_HALF_UP)

    alertas = []
    bloqueos = []
    metodo = ''
    ingreso = None
    if explicito is not None and explicito > 0:
        ingreso = explicito.quantize(Q2, rounding=ROUND_HALF_UP)
        metodo = 'VALOR_MENSUAL_EXPLICITO'
        if derivado is not None:
            diferencia = abs(explicito - derivado) / max(explicito, derivado)
            if diferencia > tolerancia:
                alertas.append('contrato:discrepancia_ingreso_mensual')
    elif derivado is not None:
        ingreso = derivado
        metodo = 'SALDO_PENDIENTE_DIVIDIDO_MESES'
    elif total is not None and total > 0 and pendiente is None:
        bloqueos.append('contrato:saldo_pendiente_sin_evidencia')
    else:
        bloqueos.append('contrato:ingreso_mensual_no_calculable')

    if meses < 1:
        bloqueos.append('contrato:menos_de_un_mes_financiable')
    if validacion.bloqueos:
        bloqueos.extend(validacion.bloqueos)
    if validacion.requiere_revision_manual:
        alertas.extend(validacion.alertas)

    bloqueos = list(dict.fromkeys(bloqueos))
    alertas = list(dict.fromkeys(alertas))
    return ResultadoIngresoContractualPrestador(
        calculable=bool(ingreso is not None and ingreso > 0 and not bloqueos),
        ingreso_mensual=ingreso,
        metodo=metodo,
        meses_restantes=meses,
        valor_total=total,
        saldo_pendiente=pendiente,
        valor_mensual_explicito=explicito,
        requiere_revision_manual=bool(alertas),
        alertas=tuple(alertas),
        bloqueos=tuple(bloqueos),
    )


def _decimal(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _texto(valor):
    return format(valor, 'f') if valor is not None else None

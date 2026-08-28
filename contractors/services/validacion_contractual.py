from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from contractors.models import ContractorApplication


SOLICITAR_VALIDACION_EMPRESA = 'SOLICITAR_VALIDACION_EMPRESA'
COINCIDENCIAS_EMPRESA_EXACTAS = {'NIT_EXACTO', 'NOMBRE_EXACTO', 'SLUG_EXACTO'}


@dataclass(frozen=True)
class ResultadoValidacionContractualPrestador:
    estado: str
    fecha_fin_efectiva: date | None
    meses_financiables: int
    capacidad_automatica: bool
    obligacion_monetaria: bool
    forma_pago: str
    forma_pago_mensual: bool
    requiere_revision_manual: bool
    requiere_validacion_empresa: bool
    razones: tuple[str, ...]
    alertas: tuple[str, ...]
    bloqueos: tuple[str, ...]


def validar_contrato_prestador(solicitud, *, fecha_corte=None):
    fecha_corte = fecha_corte or date.today()
    metadata = solicitud.metadata_analisis_contractual or {}
    razones = []
    alertas = []
    bloqueos = []
    requiere_validacion_empresa = False

    identidad = metadata.get('identidad') or {}
    documento_coincide = identidad.get('documento_coincide')
    if documento_coincide is False:
        bloqueos.append('identidad:documento_contrato_no_coincide')
    elif documento_coincide is not True:
        alertas.append('identidad:no_determinable')

    empresa = solicitud.empresa
    if not empresa.convenio_activo:
        bloqueos.append('empresa:convenio_no_activo')
    empresa_detectada = metadata.get('empresa_sugerida') or {}
    empresa_detectada_id = empresa_detectada.get('empresa_sugerida_id')
    coincidencia = str(empresa_detectada.get('tipo_coincidencia') or '').upper()
    empresa_coincide = str(empresa_detectada_id or '') == str(solicitud.empresa_id)
    if empresa_detectada_id and not empresa_coincide:
        bloqueos.append('empresa:contrato_no_coincide_con_seleccion')
    elif not empresa_coincide or coincidencia not in COINCIDENCIAS_EMPRESA_EXACTAS:
        requiere_validacion_empresa = True
        alertas.append(SOLICITAR_VALIDACION_EMPRESA)

    total = _decimal(solicitud.valor_total_contrato)
    pagado = _decimal(solicitud.valor_pagado_contrato)
    pendiente = _decimal(solicitud.valor_pendiente_cobrar)
    if total is None or total <= 0:
        bloqueos.append('contrato:valor_total_invalido')
    if pagado is None or pagado < 0 or (total is not None and pagado > total):
        bloqueos.append('contrato:valor_pagado_invalido')
    if pendiente is None or pendiente <= 0:
        bloqueos.append('contrato:sin_obligacion_monetaria_pendiente')
    if total is not None and pagado is not None and pendiente is not None:
        if pagado + pendiente > total:
            bloqueos.append('contrato:valores_financieros_incoherentes')
    obligacion_monetaria = pendiente is not None and pendiente > 0

    forma_pago = str(
        solicitud.forma_pago or ContractorApplication.FormaPago.NO_IDENTIFICADA
    )
    if forma_pago == ContractorApplication.FormaPago.NO_IDENTIFICADA:
        alertas.append('contrato:forma_pago_no_identificada')
    elif forma_pago != ContractorApplication.FormaPago.MENSUAL:
        bloqueos.append('contrato:forma_pago_no_mensual')
    forma_pago_mensual = forma_pago == ContractorApplication.FormaPago.MENSUAL

    fecha_fin = solicitud.fecha_fin_contrato
    if fecha_fin is None and solicitud.fecha_inicio_contrato and solicitud.duracion_contrato_meses:
        fecha_fin = solicitud.fecha_inicio_contrato + relativedelta(
            months=solicitud.duracion_contrato_meses
        )

    estado_declarado = solicitud.estado_contractual_declarado
    if estado_declarado in {
        ContractorApplication.EstadoContrato.VENCIDO,
        ContractorApplication.EstadoContrato.TERMINADO,
        ContractorApplication.EstadoContrato.LIQUIDADO,
    }:
        estado = estado_declarado
        bloqueos.append(f'contrato:{estado.lower()}')
    elif estado_declarado == ContractorApplication.EstadoContrato.SUSPENDIDO:
        estado = estado_declarado
        alertas.append('contrato:suspendido')
    elif fecha_fin is None:
        estado = ContractorApplication.EstadoContrato.NO_DETERMINABLE
        alertas.append('contrato:fecha_fin_no_determinable')
    elif fecha_fin < fecha_corte:
        estado = ContractorApplication.EstadoContrato.VENCIDO
        bloqueos.append('contrato:vencido')
    else:
        estado = ContractorApplication.EstadoContrato.VIGENTE

    meses_financiables = _meses_completos_restantes(fecha_corte, fecha_fin)
    if estado == ContractorApplication.EstadoContrato.VIGENTE:
        if meses_financiables < 1:
            bloqueos.append('contrato:menos_de_un_mes_financiable')
        if solicitud.plazo_meses and solicitud.plazo_meses > meses_financiables:
            bloqueos.append('contrato:plazo_supera_meses_financiables')

    if bloqueos:
        razones.append('El contrato no habilita capacidad automatica.')
    elif alertas:
        razones.append('El contrato requiere validacion manual antes de calcular capacidad.')
    else:
        razones.append('Contrato vigente, coherente y con obligacion monetaria verificable.')

    capacidad_automatica = (
        estado == ContractorApplication.EstadoContrato.VIGENTE
        and documento_coincide is True
        and not requiere_validacion_empresa
        and obligacion_monetaria
        and forma_pago_mensual
        and meses_financiables > 0
        and not bloqueos
    )
    return ResultadoValidacionContractualPrestador(
        estado=estado,
        fecha_fin_efectiva=fecha_fin,
        meses_financiables=meses_financiables,
        capacidad_automatica=capacidad_automatica,
        obligacion_monetaria=obligacion_monetaria,
        forma_pago=forma_pago,
        forma_pago_mensual=forma_pago_mensual,
        requiere_revision_manual=bool(alertas) and not bool(bloqueos),
        requiere_validacion_empresa=requiere_validacion_empresa,
        razones=tuple(dict.fromkeys(razones)),
        alertas=tuple(dict.fromkeys(alertas)),
        bloqueos=tuple(dict.fromkeys(bloqueos)),
    )


def _meses_completos_restantes(inicio, fin):
    if not inicio or not fin or fin <= inicio:
        return 0
    diferencia = relativedelta(fin, inicio)
    return max(0, diferencia.years * 12 + diferencia.months)


def _decimal(valor):
    return Decimal(str(valor)) if valor is not None else None

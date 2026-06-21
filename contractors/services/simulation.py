from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    InformacionLaboralSolicitudContratista,
    SimulacionPrestador,
)
from contractors.services.timeline import registrar_evento_timeline_prestador


DOS_DECIMALES = Decimal('0.01')
CUATRO_DECIMALES = Decimal('0.0001')
SEIS_DECIMALES = Decimal('0.000001')
VERSION_POLITICA_PRESTADORES_V2 = 'prestadores_v2'


class ErrorSimulacionContratista(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoSimulacionCreditoContratista:
    organizacion_id: int | None
    configuracion_producto_id: int | None
    tipo_producto: str
    monto_solicitado: Decimal
    plazo_meses: int
    tasa_mensual: Decimal
    comision: Decimal
    iva_comision: Decimal
    capital_financiado: Decimal
    cuota_mensual: Decimal
    total_a_pagar: Decimal
    interes_estimado: Decimal
    configuracion_portal_id: int | None = None
    tea_calculada: Decimal = Decimal('0.0000')
    costo_originacion: Decimal = Decimal('0.00')
    iva_costo_originacion: Decimal = Decimal('0.00')
    fondo_garantia_total: Decimal = Decimal('0.00')
    fondo_garantia_base: Decimal = Decimal('0.00')
    fondo_garantia_iva: Decimal = Decimal('0.00')
    seguro_vida: Decimal = Decimal('0.00')
    capital_total_financiado: Decimal = Decimal('0.00')
    cuota_mensual_v2: Decimal = Decimal('0.00')
    intereses_estimados: Decimal = Decimal('0.00')
    total_estimado: Decimal = Decimal('0.00')
    version_politica: str = ''
    advertencias: tuple[str, ...] = ()

    def como_dict(self):
        return {
            'organizacion_id': self.organizacion_id,
            'configuracion_producto_id': self.configuracion_producto_id,
            'configuracion_portal_id': self.configuracion_portal_id,
            'tipo_producto': self.tipo_producto,
            'monto_solicitado': self.monto_solicitado,
            'plazo_meses': self.plazo_meses,
            'tasa_mensual': self.tasa_mensual,
            'comision': self.comision,
            'iva_comision': self.iva_comision,
            'capital_financiado': self.capital_financiado,
            'cuota_mensual': self.cuota_mensual,
            'total_a_pagar': self.total_a_pagar,
            'interes_estimado': self.interes_estimado,
            'tea_calculada': self.tea_calculada,
            'costo_originacion': self.costo_originacion,
            'iva_costo_originacion': self.iva_costo_originacion,
            'fondo_garantia_total': self.fondo_garantia_total,
            'fondo_garantia_base': self.fondo_garantia_base,
            'fondo_garantia_iva': self.fondo_garantia_iva,
            'seguro_vida': self.seguro_vida,
            'capital_total_financiado': self.capital_total_financiado,
            'intereses_estimados': self.intereses_estimados,
            'total_estimado': self.total_estimado,
            'version_politica': self.version_politica,
            'advertencias': list(self.advertencias),
        }

    def as_dict(self):
        return {
            'organization_id': self.organizacion_id,
            'product_config_id': self.configuracion_producto_id,
            'product_type': self.tipo_producto,
            'requested_amount': self.monto_solicitado,
            'term_months': self.plazo_meses,
            'monthly_rate': self.tasa_mensual,
            'commission_amount': self.comision,
            'vat_amount': self.iva_comision,
            'principal_financed': self.capital_financiado,
            'monthly_payment': self.cuota_mensual,
            'total_to_pay': self.total_a_pagar,
            'estimated_interest': self.interes_estimado,
            'origination_cost': self.costo_originacion,
            'origination_vat': self.iva_costo_originacion,
            'guarantee_fund_total': self.fondo_garantia_total,
            'life_insurance': self.seguro_vida,
            'policy_version': self.version_politica,
        }

    @property
    def organization_id(self):
        return self.organizacion_id

    @property
    def product_config_id(self):
        return self.configuracion_producto_id

    @property
    def product_type(self):
        return self.tipo_producto

    @property
    def requested_amount(self):
        return self.monto_solicitado

    @property
    def term_months(self):
        return self.plazo_meses

    @property
    def monthly_rate(self):
        return self.tasa_mensual

    @property
    def commission_amount(self):
        return self.comision

    @property
    def vat_amount(self):
        return self.iva_comision

    @property
    def principal_financed(self):
        return self.capital_financiado

    @property
    def monthly_payment(self):
        return self.cuota_mensual

    @property
    def total_to_pay(self):
        return self.total_a_pagar

    @property
    def estimated_interest(self):
        return self.interes_estimado


def simular_credito_contratista(*, organizacion, configuracion_producto, monto, plazo_meses):
    if organizacion is None:
        raise ErrorSimulacionContratista('organizacion_requerida')
    if configuracion_producto is None:
        raise ErrorSimulacionContratista('configuracion_producto_requerida')
    if configuracion_producto.organization_id != organizacion.id:
        raise ErrorSimulacionContratista('configuracion_no_pertenece_a_organizacion')
    if not configuracion_producto.is_active or not organizacion.is_active:
        raise ErrorSimulacionContratista('organizacion_o_configuracion_inactiva')

    monto = _dinero(monto)
    plazo_meses = int(plazo_meses)
    _validar_limites(configuracion_producto=configuracion_producto, monto=monto, plazo_meses=plazo_meses)

    tasa_mensual = _tasa(configuracion_producto.monthly_rate)
    comision_porcentual = (
        monto * (_tasa(configuracion_producto.commission_rate) / Decimal('100'))
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    comision_fija = _dinero(configuracion_producto.commission_amount)
    comision = (comision_porcentual + comision_fija).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    iva_comision = (comision * (_tasa(configuracion_producto.vat_rate) / Decimal('100'))).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    capital_financiado = (monto + comision + iva_comision).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    cuota_mensual = _calcular_cuota_mensual(
        capital_financiado=capital_financiado,
        tasa_mensual=tasa_mensual,
        plazo_meses=plazo_meses,
    )
    total_a_pagar = (cuota_mensual * plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    interes_estimado = max(Decimal('0.00'), total_a_pagar - capital_financiado).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )

    return ResultadoSimulacionCreditoContratista(
        organizacion_id=organizacion.id,
        configuracion_producto_id=configuracion_producto.id,
        tipo_producto=configuracion_producto.product_type,
        monto_solicitado=monto,
        plazo_meses=plazo_meses,
        tasa_mensual=tasa_mensual,
        comision=comision,
        iva_comision=iva_comision,
        capital_financiado=capital_financiado,
        cuota_mensual=cuota_mensual,
        total_a_pagar=total_a_pagar,
        interes_estimado=interes_estimado,
    )


def simular_credito_portal_contratistas(*, configuracion_portal, monto, plazo_meses):
    if configuracion_portal is None:
        raise ErrorSimulacionContratista('configuracion_portal_requerida')
    if not configuracion_portal.activo:
        raise ErrorSimulacionContratista('configuracion_portal_inactiva')

    monto = _dinero(monto)
    plazo_meses = int(plazo_meses)
    _validar_limites(configuracion_producto=configuracion_portal, monto=monto, plazo_meses=plazo_meses)

    tasa_mensual = _tasa(configuracion_portal.tasa_mensual)
    costo_originacion_porcentual = (
        monto * (_tasa(configuracion_portal.tasa_comision) / Decimal('100'))
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    costo_originacion_fijo = _dinero(configuracion_portal.comision_fija)
    costo_originacion = (costo_originacion_porcentual + costo_originacion_fijo).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    iva_costo_originacion = (costo_originacion * (_tasa(configuracion_portal.tasa_iva) / Decimal('100'))).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    fondo_garantia_total = (
        monto * (_tasa(configuracion_portal.tasa_fondo_garantia) / Decimal('100'))
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    iva_fondo_ratio = _tasa(configuracion_portal.iva_fondo_garantia) / Decimal('100')
    if configuracion_portal.fondo_garantia_incluye_iva and iva_fondo_ratio > 0:
        fondo_garantia_base = (fondo_garantia_total / (Decimal('1') + iva_fondo_ratio)).quantize(
            DOS_DECIMALES,
            rounding=ROUND_HALF_UP,
        )
        fondo_garantia_iva = (fondo_garantia_total - fondo_garantia_base).quantize(
            DOS_DECIMALES,
            rounding=ROUND_HALF_UP,
        )
    else:
        fondo_garantia_base = fondo_garantia_total
        fondo_garantia_iva = (fondo_garantia_total * iva_fondo_ratio).quantize(
            DOS_DECIMALES,
            rounding=ROUND_HALF_UP,
        )
        fondo_garantia_total = (fondo_garantia_base + fondo_garantia_iva).quantize(
            DOS_DECIMALES,
            rounding=ROUND_HALF_UP,
        )
    seguro_vida = (monto * _factor(configuracion_portal.factor_seguro_vida)).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    seguro_financiado = seguro_vida if configuracion_portal.seguro_vida_financiado else Decimal('0.00')
    capital_financiado = (
        monto
        + costo_originacion
        + iva_costo_originacion
        + fondo_garantia_total
        + seguro_financiado
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    cuota_mensual = _calcular_cuota_mensual(
        capital_financiado=capital_financiado,
        tasa_mensual=tasa_mensual,
        plazo_meses=plazo_meses,
    )
    total_a_pagar = (cuota_mensual * plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    interes_estimado = max(Decimal('0.00'), total_a_pagar - capital_financiado).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    tea_calculada = _calcular_tea(tasa_mensual)

    return ResultadoSimulacionCreditoContratista(
        organizacion_id=None,
        configuracion_producto_id=None,
        configuracion_portal_id=configuracion_portal.id,
        tipo_producto='credito_contratista',
        monto_solicitado=monto,
        plazo_meses=plazo_meses,
        tasa_mensual=tasa_mensual,
        comision=costo_originacion,
        iva_comision=iva_costo_originacion,
        capital_financiado=capital_financiado,
        cuota_mensual=cuota_mensual,
        total_a_pagar=total_a_pagar,
        interes_estimado=interes_estimado,
        tea_calculada=tea_calculada,
        costo_originacion=costo_originacion,
        iva_costo_originacion=iva_costo_originacion,
        fondo_garantia_total=fondo_garantia_total,
        fondo_garantia_base=fondo_garantia_base,
        fondo_garantia_iva=fondo_garantia_iva,
        seguro_vida=seguro_vida,
        capital_total_financiado=capital_financiado,
        cuota_mensual_v2=cuota_mensual,
        intereses_estimados=interes_estimado,
        total_estimado=total_a_pagar,
        version_politica=VERSION_POLITICA_PRESTADORES_V2,
    )


def aceptar_simulacion_prestador(*, solicitud, monto, plazo_meses, usuario=None, request=None):
    if solicitud is None:
        raise ErrorSimulacionContratista('solicitud_requerida')
    with transaction.atomic():
        solicitud = (
            ContractorApplication.objects
            .select_for_update()
            .select_related('configuracion_portal', 'usuario')
            .get(pk=solicitud.pk)
        )
        _validar_solicitud_puede_simular(solicitud)
        if SimulacionPrestador.objects.filter(solicitud=solicitud, aceptada=True).exists():
            raise ErrorSimulacionContratista('simulacion_ya_aceptada')

        resultado = simular_credito_portal_contratistas(
            configuracion_portal=solicitud.configuracion_portal,
            monto=monto,
            plazo_meses=plazo_meses,
        )
        simulacion = SimulacionPrestador.objects.create(
            solicitud=solicitud,
            monto_solicitado=resultado.monto_solicitado,
            plazo_meses=resultado.plazo_meses,
            tasa_mensual=resultado.tasa_mensual,
            tea=resultado.tea_calculada,
            costo_originacion=resultado.costo_originacion,
            iva_costo_originacion=resultado.iva_costo_originacion,
            fondo_garantia_total=resultado.fondo_garantia_total,
            fondo_garantia_base=resultado.fondo_garantia_base,
            fondo_garantia_iva=resultado.fondo_garantia_iva,
            seguro_vida=resultado.seguro_vida,
            capital_total_financiado=resultado.capital_total_financiado,
            cuota_mensual=resultado.cuota_mensual,
            intereses_estimados=resultado.intereses_estimados,
            total_estimado=resultado.total_estimado,
            version_politica=resultado.version_politica,
            aceptada=True,
            aceptada_por=usuario if getattr(usuario, 'is_authenticated', True) else None,
            accepted_at=timezone.now(),
        )
        payload_actual = solicitud.simulation_payload or {}
        payload_actual.update(
            {
                'simulacion_aceptada': True,
                'lista_evaluacion_formal': True,
                'simulacion_prestador_id': simulacion.id,
                **_payload_resultado_seguro(resultado),
            },
        )
        solicitud.requested_amount = resultado.monto_solicitado
        solicitud.term_months = resultado.plazo_meses
        solicitud.estimated_monthly_payment = resultado.cuota_mensual
        solicitud.simulation_payload = payload_actual
        solicitud.save(
            update_fields=[
                'requested_amount',
                'term_months',
                'estimated_monthly_payment',
                'simulation_payload',
                'updated_at',
            ],
        )

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='SIMULACION_ACEPTADA',
        titulo='Simulacion aceptada por prestador',
        descripcion='El prestador acepto una simulacion financiera calculada por backend.',
        estado_resultante='LISTA_EVALUACION_FORMAL',
        metadata={
            'simulacion_id': simulacion.id,
            'monto_solicitado': resultado.monto_solicitado,
            'plazo_meses': resultado.plazo_meses,
            'cuota_mensual': resultado.cuota_mensual,
            'version_politica': resultado.version_politica,
        },
        usuario=usuario,
        request=request,
    )
    return simulacion


def validar_solicitud_lista_para_simular(solicitud):
    _validar_solicitud_puede_simular(solicitud)
    return True


def _validar_limites(*, configuracion_producto, monto, plazo_meses):
    if monto < configuracion_producto.min_amount:
        raise ErrorSimulacionContratista('monto_menor_al_minimo')
    if monto > configuracion_producto.max_amount:
        raise ErrorSimulacionContratista('monto_supera_maximo')
    if plazo_meses < configuracion_producto.min_term_months:
        raise ErrorSimulacionContratista('plazo_menor_al_minimo')
    if plazo_meses > configuracion_producto.max_term_months:
        raise ErrorSimulacionContratista('plazo_supera_maximo')


def _calcular_cuota_mensual(*, capital_financiado, tasa_mensual, plazo_meses):
    tasa = tasa_mensual / Decimal('100')
    if tasa <= 0:
        return (capital_financiado / plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)

    factor = (tasa * (Decimal('1') + tasa) ** plazo_meses) / (
        ((Decimal('1') + tasa) ** plazo_meses) - Decimal('1')
    )
    return (capital_financiado * factor).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _dinero(value):
    return Decimal(str(value)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _tasa(value):
    return Decimal(str(value)).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)


def _factor(value):
    return Decimal(str(value)).quantize(SEIS_DECIMALES, rounding=ROUND_HALF_UP)


def _calcular_tea(tasa_mensual):
    tasa = tasa_mensual / Decimal('100')
    tea = ((Decimal('1') + tasa) ** 12 - Decimal('1')) * Decimal('100')
    return tea.quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)


def _validar_solicitud_puede_simular(solicitud):
    if solicitud.status == ContractorApplication.Estado.CONVERTIDA:
        raise ErrorSimulacionContratista('solicitud_convertida_no_permite_simulacion')
    if not solicitud.configuracion_portal_id:
        raise ErrorSimulacionContratista('configuracion_portal_requerida')
    if not solicitud.configuracion_portal.activo:
        raise ErrorSimulacionContratista('configuracion_portal_inactiva')
    if not InformacionLaboralSolicitudContratista.objects.filter(solicitud=solicitud, empresa__isnull=False).exists():
        raise ErrorSimulacionContratista('datos_contractuales_empresa_requeridos')

    documentos_requeridos = {
        ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
        ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
        ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
        ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
    }
    documentos_existentes = set(
        ContractorApplicationDocument.objects
        .filter(application=solicitud, document_type__in=documentos_requeridos)
        .values_list('document_type', flat=True),
    )
    faltantes = documentos_requeridos - documentos_existentes
    if faltantes:
        raise ErrorSimulacionContratista('documentos_requeridos_incompletos')


def _payload_resultado_seguro(resultado):
    return {llave: str(valor) for llave, valor in resultado.como_dict().items()}


# Aliases temporales de compatibilidad.
MONEY = DOS_DECIMALES
RATE_PLACES = CUATRO_DECIMALES
ContractorSimulationError = ErrorSimulacionContratista
ContractorCreditSimulationResult = ResultadoSimulacionCreditoContratista


def simulate_contractor_credit(*, organization, product_config, amount, term_months):
    return simular_credito_contratista(
        organizacion=organization,
        configuracion_producto=product_config,
        monto=amount,
        plazo_meses=term_months,
    )

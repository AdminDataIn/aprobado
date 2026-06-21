from dataclasses import dataclass, field
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from contractors.selectors import obtener_datos_contractuales_solicitud


@dataclass(frozen=True)
class ResultadoCapacidadContractualContratista:
    solicitud_id: int | None
    elegible: bool
    razon: str
    razones: tuple[str, ...] = field(default_factory=tuple)
    valor_pendiente_cobrar: Decimal = Decimal('0.00')
    meses_restantes_contrato: int = 0
    monto_solicitado: Decimal = Decimal('0.00')
    plazo_solicitado: int = 0
    capacidad_maxima_estimada: Decimal = Decimal('0.00')
    requiere_revision_manual: bool = False
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    bloqueos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def application_id(self):
        return self.solicitud_id

    @property
    def eligible(self):
        return self.elegible

    @property
    def reason(self):
        return self.razon

    @property
    def reasons(self):
        return self.razones

    def como_dict(self):
        return {
            'application_id': self.solicitud_id,
            'eligible': self.elegible,
            'reason': self.razon,
            'reasons': list(self.razones),
            'valor_pendiente_cobrar': self.valor_pendiente_cobrar,
            'meses_restantes_contrato': self.meses_restantes_contrato,
            'monto_solicitado': self.monto_solicitado,
            'plazo_solicitado': self.plazo_solicitado,
            'capacidad_maxima_estimada': self.capacidad_maxima_estimada,
            'requiere_revision_manual': self.requiere_revision_manual,
            'advertencias': list(self.advertencias),
            'bloqueos': list(self.bloqueos),
        }


def evaluar_capacidad_contractual_contratista(solicitud):
    razones = []

    if solicitud is None:
        return _resultado(
            solicitud_id=None,
            razones=['solicitud_requerida'],
        )

    datos_contractuales = obtener_datos_contractuales_solicitud(solicitud)
    if datos_contractuales is None:
        return _resultado(
            solicitud_id=solicitud.id,
            razones=['datos_contractuales_requeridos'],
            monto_solicitado=solicitud.requested_amount,
            plazo_solicitado=solicitud.term_months,
        )

    hoy = timezone.localdate()
    meses_restantes = calcular_meses_restantes_contrato(datos_contractuales.fecha_fin_contrato, fecha_base=hoy)
    valor_pendiente = datos_contractuales.valor_pendiente_cobrar or Decimal('0.00')
    monto_solicitado = solicitud.requested_amount or Decimal('0.00')
    plazo_solicitado = int(solicitud.term_months or 0)
    metadata_contractual = _metadata_contractual_segura(solicitud)
    advertencias = []
    bloqueos = []
    requiere_revision_manual = False

    if solicitud.requested_amount is None:
        razones.append('monto_solicitado_pendiente_simulacion')

    if solicitud.term_months is None:
        razones.append('plazo_solicitado_pendiente_simulacion')

    if not datos_contractuales.empresa_id:
        razones.append('empresa_requerida')
    elif not datos_contractuales.empresa.permite_libranza:
        razones.append('empresa_no_elegible_libranza')

    if datos_contractuales.fecha_fin_contrato < hoy:
        razones.append('contrato_vencido')
        bloqueos.append('contrato_vencido')
        requiere_revision_manual = True
        valor_pendiente = Decimal('0.00')

    if valor_pendiente <= Decimal('0.00'):
        razones.append('valor_pendiente_cobrar_insuficiente')
        requiere_revision_manual = True

    if monto_solicitado > valor_pendiente:
        razones.append('monto_supera_valor_pendiente_cobrar')

    if plazo_solicitado > meses_restantes:
        razones.append('plazo_supera_meses_restantes_contrato')

    if metadata_contractual.get('valor_pendiente_no_determinado'):
        advertencias.append('valor_pendiente_requiere_revision_manual')
        requiere_revision_manual = True
    if metadata_contractual.get('valor_pendiente_fuente') in {
        'calculada_total_menos_pagado',
        'inferida_mensualidad_vigencia',
        'contrato_vencido',
    }:
        advertencias.append('valor_pendiente_inferido_requiere_confirmacion')
        requiere_revision_manual = True
    if metadata_contractual.get('fecha_fin_fuente') == 'inferida_duracion_meses':
        advertencias.append('fecha_fin_inferida_requiere_confirmacion')
        requiere_revision_manual = True

    return _resultado(
        solicitud_id=solicitud.id,
        razones=razones,
        valor_pendiente_cobrar=valor_pendiente,
        meses_restantes_contrato=meses_restantes,
        monto_solicitado=monto_solicitado,
        plazo_solicitado=plazo_solicitado,
        capacidad_maxima_estimada=max(Decimal('0.00'), valor_pendiente),
        requiere_revision_manual=requiere_revision_manual,
        advertencias=advertencias,
        bloqueos=bloqueos,
    )


def calcular_meses_restantes_contrato(fecha_fin_contrato, fecha_base=None):
    fecha_base = fecha_base or timezone.localdate()
    if fecha_fin_contrato < fecha_base:
        return 0

    diferencia = relativedelta(fecha_fin_contrato, fecha_base)
    meses = diferencia.years * 12 + diferencia.months
    if diferencia.days > 0 or meses == 0:
        meses += 1
    return max(0, meses)


def _resultado(
    *,
    solicitud_id,
    razones,
    valor_pendiente_cobrar=Decimal('0.00'),
    meses_restantes_contrato=0,
    monto_solicitado=Decimal('0.00'),
    plazo_solicitado=0,
    capacidad_maxima_estimada=Decimal('0.00'),
    requiere_revision_manual=False,
    advertencias=(),
    bloqueos=(),
):
    razones = tuple(razones)
    elegible = not razones
    razon = 'capacidad_contractual_suficiente' if elegible else razones[0]
    return ResultadoCapacidadContractualContratista(
        solicitud_id=solicitud_id,
        elegible=elegible,
        razon=razon,
        razones=razones,
        valor_pendiente_cobrar=valor_pendiente_cobrar,
        meses_restantes_contrato=meses_restantes_contrato,
        monto_solicitado=monto_solicitado,
        plazo_solicitado=plazo_solicitado,
        capacidad_maxima_estimada=capacidad_maxima_estimada,
        requiere_revision_manual=requiere_revision_manual,
        advertencias=tuple(advertencias),
        bloqueos=tuple(bloqueos),
    )


def _metadata_contractual_segura(solicitud):
    payload = getattr(solicitud, 'simulation_payload', None) or {}
    analisis = payload.get('analisis_contractual_seguro') or {}
    if not isinstance(analisis, dict):
        return {}
    metadata = analisis.get('metadata') or {}
    return metadata if isinstance(metadata, dict) else {}

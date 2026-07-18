from dataclasses import asdict, dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from contractors.models import AprobacionInternaPrestador
from contractors.services.evaluacion_versionado import construir_version_datos


@dataclass(frozen=True)
class ExpedienteOriginacionPrestadorDTO:
    solicitud_id: int
    aprobacion_interna_id: int
    auditoria_predecision_id: int
    usuario_id: int
    empresa_id: int
    tipo_documento: str
    numero_documento: str
    nombres: str
    apellidos: str
    correo: str
    celular: str
    direccion: str
    escenario_credito: str
    monto_autorizado: Decimal
    plazo_autorizado: int
    tasa_mensual: Decimal
    version_politica: str
    version_configuracion_financiera: str
    cargo: str
    tipo_contrato: str
    fecha_inicio_contrato: object
    fecha_fin_contrato: object
    valor_total_contrato: Decimal
    valor_pagado_contrato: Decimal
    valor_pendiente_cobrar: Decimal

    def como_dict(self):
        return asdict(self)


def construir_expediente_originacion_prestador(gate):
    gate = AprobacionInternaPrestador.objects.select_related(
        'solicitud', 'auditoria_predecision'
    ).get(pk=gate.pk)
    if gate.estado != AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
        raise ValidationError('La solicitud no esta aprobada internamente para originar.')
    solicitud = gate.solicitud
    version_actual, _ = construir_version_datos(solicitud)
    if version_actual != gate.version_datos:
        raise ValidationError('Los datos cambiaron despues de la aprobacion interna.')
    if gate.auditoria_predecision.version_datos != gate.version_datos:
        raise ValidationError('La aprobacion no coincide con su auditoria de predecision.')
    return ExpedienteOriginacionPrestadorDTO(
        solicitud_id=solicitud.id,
        aprobacion_interna_id=gate.id,
        auditoria_predecision_id=gate.auditoria_predecision_id,
        usuario_id=solicitud.usuario_id,
        empresa_id=solicitud.empresa_id,
        tipo_documento=solicitud.tipo_documento,
        numero_documento=solicitud.numero_documento,
        nombres=solicitud.nombres,
        apellidos=solicitud.apellidos,
        correo=solicitud.correo,
        celular=solicitud.celular,
        direccion=solicitud.direccion,
        escenario_credito=solicitud.escenario_credito,
        monto_autorizado=gate.monto_autorizado,
        plazo_autorizado=gate.plazo_autorizado,
        tasa_mensual=gate.tasa_mensual_snapshot,
        version_politica=gate.version_politica,
        version_configuracion_financiera=gate.version_configuracion_financiera,
        cargo=solicitud.cargo,
        tipo_contrato=solicitud.tipo_contrato,
        fecha_inicio_contrato=solicitud.fecha_inicio_contrato,
        fecha_fin_contrato=solicitud.fecha_fin_contrato,
        valor_total_contrato=solicitud.valor_total_contrato,
        valor_pagado_contrato=solicitud.valor_pagado_contrato,
        valor_pendiente_cobrar=solicitud.valor_pendiente_cobrar,
    )

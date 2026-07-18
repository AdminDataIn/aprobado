from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    OrigenCreditoPrestador,
)


class ExpedienteOriginacionLibranza(Protocol):
    aprobacion_interna_id: int
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
    monto_solicitado: Decimal
    plazo_solicitado: int
    monto_autorizado: Decimal
    plazo_autorizado: int
    tasa_mensual: Decimal
    version_datos: str
    cargo: str
    tipo_contrato: str
    fecha_inicio_contrato: object
    fecha_fin_contrato: object
    valor_total_contrato: Decimal
    valor_pagado_contrato: Decimal
    valor_pendiente_cobrar: Decimal
    cedula_frontal_nombre: str
    cedula_trasera_nombre: str
    contrato_nombre: str
    certificado_bancario_nombre: str


@dataclass(frozen=True)
class ResultadoOriginacionLibranza:
    origen: OrigenCreditoPrestador
    credito: Credito
    credito_libranza: CreditoLibranza
    reutilizado: bool


class OriginacionEnProceso(ValidationError):
    pass


def construir_clave_idempotencia_prestador(dto):
    return f'prestador:{dto.aprobacion_interna_id}:{dto.version_datos}'


@transaction.atomic
def originar_libranza_desde_expediente(
    dto: ExpedienteOriginacionLibranza,
    clave_idempotencia: str,
    actor,
) -> ResultadoOriginacionLibranza:
    """Crea una libranza EN_REVISION sin activar formalizacion ni pagos."""
    _exigir_actor_originacion(actor)
    _validar_expediente(dto, clave_idempotencia)

    origen = _obtener_o_crear_origen_bloqueado(
        gate_id=dto.aprobacion_interna_id,
        gate_version=dto.version_datos,
        clave_idempotencia=clave_idempotencia,
        actor=actor,
    )
    if origen.estado == OrigenCreditoPrestador.Estado.COMPLETADO:
        if not origen.credito_id or not origen.credito_libranza_id:
            raise ValidationError('El origen completado no tiene enlaces financieros validos.')
        return ResultadoOriginacionLibranza(
            origen=origen,
            credito=origen.credito,
            credito_libranza=origen.credito_libranza,
            reutilizado=True,
        )
    if origen.credito_id or origen.credito_libranza_id:
        raise OriginacionEnProceso('La originacion tiene enlaces parciales y requiere revision.')
    if origen.estado != OrigenCreditoPrestador.Estado.EN_PROCESO:
        raise ValidationError('El origen no esta disponible para ser procesado.')

    credito = Credito.objects.create(
        usuario_id=dto.usuario_id,
        linea=Credito.LineaCredito.LIBRANZA,
        estado=Credito.EstadoCredito.EN_REVISION,
        monto_solicitado=dto.monto_solicitado,
        plazo_solicitado=dto.plazo_solicitado,
        monto_aprobado=dto.monto_autorizado,
        plazo=dto.plazo_autorizado,
        tasa_interes=dto.tasa_mensual,
    )
    detalle = CreditoLibranza.objects.create(
        credito=credito,
        nombres=dto.nombres,
        apellidos=dto.apellidos,
        cedula=dto.numero_documento,
        direccion=dto.direccion,
        telefono=dto.celular,
        correo_electronico=dto.correo,
        empresa_id=dto.empresa_id,
        ingresos_mensuales=None,
        es_prestador_servicios=True,
        tipo_documento=dto.tipo_documento,
        cargo_actividad_contractual=dto.cargo,
        tipo_contrato=dto.tipo_contrato,
        fecha_inicio_contrato=dto.fecha_inicio_contrato,
        fecha_fin_contrato=dto.fecha_fin_contrato,
        valor_total_contrato=dto.valor_total_contrato,
        valor_pagado_contrato=dto.valor_pagado_contrato,
        valor_pendiente_contrato=dto.valor_pendiente_cobrar,
        escenario_credito=dto.escenario_credito,
        cedula_frontal=dto.cedula_frontal_nombre,
        cedula_trasera=dto.cedula_trasera_nombre,
        contrato_prestacion_servicios=dto.contrato_nombre,
        certificado_bancario=dto.certificado_bancario_nombre,
    )
    origen.credito = credito
    origen.credito_libranza = detalle
    origen.estado = OrigenCreditoPrestador.Estado.COMPLETADO
    origen.save(update_fields=[
        'credito', 'credito_libranza', 'estado', 'updated_at',
    ])
    return ResultadoOriginacionLibranza(
        origen=origen,
        credito=credito,
        credito_libranza=detalle,
        reutilizado=False,
    )


def _obtener_o_crear_origen_bloqueado(
    *, gate_id, gate_version, clave_idempotencia, actor
):
    existente = OrigenCreditoPrestador.objects.select_for_update().filter(
        clave_idempotencia=clave_idempotencia
    ).first()
    if existente:
        _validar_coincidencia_origen(existente, gate_id, gate_version, clave_idempotencia)
        return existente

    por_gate = OrigenCreditoPrestador.objects.select_for_update().filter(
        gate_id=gate_id
    ).first()
    if por_gate:
        _validar_coincidencia_origen(por_gate, gate_id, gate_version, clave_idempotencia)
        return por_gate

    try:
        with transaction.atomic():
            return OrigenCreditoPrestador.objects.create(
                gate_id=gate_id,
                gate_version=gate_version,
                clave_idempotencia=clave_idempotencia,
                estado=OrigenCreditoPrestador.Estado.EN_PROCESO,
                created_by=actor,
            )
    except IntegrityError:
        existente = OrigenCreditoPrestador.objects.select_for_update().filter(
            gate_id=gate_id
        ).first()
        if existente is None:
            existente = OrigenCreditoPrestador.objects.select_for_update().get(
                clave_idempotencia=clave_idempotencia
            )
        _validar_coincidencia_origen(
            existente, gate_id, gate_version, clave_idempotencia
        )
        return existente


def _validar_coincidencia_origen(origen, gate_id, gate_version, clave_idempotencia):
    if origen.gate_id != gate_id:
        raise ValidationError('La clave idempotente pertenece a otro gate.')
    if origen.clave_idempotencia != clave_idempotencia:
        raise ValidationError('El gate ya fue usado con otra clave idempotente.')
    if origen.gate_version != gate_version:
        raise ValidationError('La version del gate no coincide con el origen existente.')


def _validar_expediente(dto, clave_idempotencia):
    if not str(clave_idempotencia or '').strip():
        raise ValidationError('La clave idempotente es obligatoria.')
    if len(clave_idempotencia) > 180:
        raise ValidationError('La clave idempotente supera la longitud permitida.')
    if not dto.aprobacion_interna_id or not dto.version_datos:
        raise ValidationError('El expediente no identifica un gate versionado.')
    if dto.monto_solicitado <= 0 or dto.monto_autorizado <= 0:
        raise ValidationError('Los montos del expediente deben ser positivos.')
    if dto.monto_autorizado > dto.monto_solicitado:
        raise ValidationError('El monto autorizado no puede superar el solicitado.')
    if dto.plazo_solicitado <= 0 or dto.plazo_autorizado <= 0:
        raise ValidationError('Los plazos del expediente deben ser positivos.')
    if dto.plazo_autorizado > dto.plazo_solicitado:
        raise ValidationError('El plazo autorizado no puede superar el solicitado.')
    if dto.tasa_mensual <= 0:
        raise ValidationError('La tasa mensual del expediente debe ser positiva.')
    requeridos = (
        dto.cedula_frontal_nombre,
        dto.cedula_trasera_nombre,
        dto.contrato_nombre,
        dto.certificado_bancario_nombre,
    )
    if not all(str(valor or '').strip() for valor in requeridos):
        raise ValidationError('El expediente no contiene todos los documentos requeridos.')


def _exigir_actor_originacion(actor):
    if (
        actor is None
        or not actor.is_authenticated
        or not actor.is_staff
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm('contractors.can_originate_contractor_credit')
    ):
        raise PermissionDenied('No tienes permiso para originar este credito.')

from dataclasses import asdict, dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from contractors.models import (
    AprobacionInternaPrestador,
    ConfiguracionSimuladorPrestador,
)
from contractors.services.aprobacion_pagador import validar_aprobacion_pagador_vigente
from contractors.services.evaluacion_versionado import construir_version_datos
from gestion_creditos.services.condiciones_financieras import (
    ComponentesFinancierosCredito,
    calcular_componentes_financieros,
)


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
    monto_solicitado: Decimal
    plazo_solicitado: int
    monto_autorizado: Decimal
    plazo_autorizado: int
    tasa_mensual: Decimal
    version_datos: str
    version_politica: str
    version_configuracion_financiera: str
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
    componentes_financieros: ComponentesFinancierosCredito

    def como_dict(self):
        return asdict(self)


def construir_expediente_originacion_prestador(gate):
    gate = AprobacionInternaPrestador.objects.select_related(
        'solicitud', 'auditoria_predecision', 'aprobacion_pagador'
    ).get(pk=gate.pk)
    if gate.estado != AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
        raise ValidationError('La solicitud no esta aprobada internamente para originar.')
    solicitud = gate.solicitud
    version_actual, _ = construir_version_datos(solicitud)
    if version_actual != gate.version_datos:
        raise ValidationError('Los datos cambiaron despues de la aprobacion interna.')
    if gate.auditoria_predecision.version_datos != gate.version_datos:
        raise ValidationError('La aprobacion no coincide con su auditoria de predecision.')
    validar_aprobacion_pagador_vigente(gate)
    configuracion = ConfiguracionSimuladorPrestador.objects.filter(
        version=gate.version_configuracion_financiera,
    ).first()
    if configuracion is None:
        raise ValidationError(
            'No existe la version financiera exacta usada por la aprobacion.'
        )
    if configuracion.tasa_mensual != gate.tasa_mensual_snapshot:
        raise ValidationError('La tasa aprobada no coincide con la configuracion versionada.')
    if not (
        configuracion.monto_minimo <= gate.monto_autorizado <= configuracion.monto_maximo
    ):
        raise ValidationError('El monto autorizado no pertenece a la configuracion versionada.')
    if not (
        configuracion.plazo_minimo_meses
        <= gate.plazo_autorizado
        <= configuracion.plazo_maximo_meses
    ):
        raise ValidationError('El plazo autorizado no pertenece a la configuracion versionada.')
    componentes_financieros = calcular_componentes_financieros(
        monto_base=gate.monto_autorizado,
        porcentaje_comision=configuracion.porcentaje_originacion,
        porcentaje_iva=configuracion.porcentaje_iva_originacion,
        porcentaje_seguro=configuracion.porcentaje_seguro_vida_primera_cuota,
        porcentaje_fondo=configuracion.porcentaje_fondo_garantia,
        tasa_mensual=configuracion.tasa_mensual,
        plazo=gate.plazo_autorizado,
        version_configuracion=configuracion.version,
        version_score=gate.auditoria_predecision.version_score,
        version_politica=gate.version_politica,
    )
    documentos = {
        documento.tipo_documento: documento
        for documento in solicitud.documentos.all()
        if documento.archivo
    }
    requeridos = {
        'CEDULA_FRONTAL', 'CEDULA_TRASERA', 'CONTRATO', 'CERTIFICADO_BANCARIO'
    }
    if not requeridos.issubset(documentos):
        raise ValidationError('Los documentos requeridos para originar no estan completos.')
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
        monto_solicitado=gate.monto_solicitado_snapshot,
        plazo_solicitado=gate.plazo_solicitado_snapshot,
        monto_autorizado=gate.monto_autorizado,
        plazo_autorizado=gate.plazo_autorizado,
        tasa_mensual=gate.tasa_mensual_snapshot,
        version_datos=gate.version_datos,
        version_politica=gate.version_politica,
        version_configuracion_financiera=gate.version_configuracion_financiera,
        cargo=solicitud.cargo,
        tipo_contrato=solicitud.tipo_contrato,
        fecha_inicio_contrato=solicitud.fecha_inicio_contrato,
        fecha_fin_contrato=solicitud.fecha_fin_contrato,
        valor_total_contrato=solicitud.valor_total_contrato,
        valor_pagado_contrato=solicitud.valor_pagado_contrato,
        valor_pendiente_cobrar=solicitud.valor_pendiente_cobrar,
        cedula_frontal_nombre=documentos['CEDULA_FRONTAL'].archivo.name,
        cedula_trasera_nombre=documentos['CEDULA_TRASERA'].archivo.name,
        contrato_nombre=documentos['CONTRATO'].archivo.name,
        certificado_bancario_nombre=documentos['CERTIFICADO_BANCARIO'].archivo.name,
        componentes_financieros=componentes_financieros,
    )

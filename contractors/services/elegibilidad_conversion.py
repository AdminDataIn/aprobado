from dataclasses import dataclass, field

from contractors.models import ContractorApplication, ContractorApplicationDocument
from contractors.selectors import obtener_ultimo_documento_por_tipo


TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION = (
    ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
    ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
    ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
    ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
)


@dataclass(frozen=True)
class ResultadoElegibilidadConversionContratista:
    solicitud_id: int | None
    elegible: bool
    razon: str
    razones: tuple[str, ...] = field(default_factory=tuple)
    documentos_faltantes: tuple[str, ...] = field(default_factory=tuple)
    documentos_rechazados: tuple[str, ...] = field(default_factory=tuple)

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

    @property
    def missing_documents(self):
        return self.documentos_faltantes

    @property
    def rejected_documents(self):
        return self.documentos_rechazados

    def como_dict(self):
        return {
            'application_id': self.solicitud_id,
            'eligible': self.elegible,
            'reason': self.razon,
            'reasons': list(self.razones),
            'missing_documents': list(self.documentos_faltantes),
            'rejected_documents': list(self.documentos_rechazados),
        }


def evaluar_elegibilidad_conversion_contratista(solicitud):
    razones = []
    documentos_faltantes = []
    documentos_rechazados = []

    if solicitud is None:
        return _resultado(
            solicitud_id=None,
            razones=['solicitud_requerida'],
            documentos_faltantes=documentos_faltantes,
            documentos_rechazados=documentos_rechazados,
        )

    _evaluar_estado_solicitud(solicitud, razones)
    _evaluar_base_solicitud(solicitud, razones)
    _evaluar_configuracion_vigente(solicitud, razones)
    _evaluar_documentos_requeridos(solicitud, razones, documentos_faltantes, documentos_rechazados)

    return _resultado(
        solicitud_id=solicitud.id,
        razones=razones,
        documentos_faltantes=documentos_faltantes,
        documentos_rechazados=documentos_rechazados,
    )


def _evaluar_estado_solicitud(solicitud, razones):
    if solicitud.status == ContractorApplication.Estado.RECHAZADA:
        razones.append('solicitud_rechazada')
        return

    if solicitud.status == ContractorApplication.Estado.CONVERTIDA:
        razones.append('solicitud_convertida')
        return

    if solicitud.status != ContractorApplication.Estado.EN_REVISION:
        razones.append('solicitud_no_esta_en_revision')


def _evaluar_base_solicitud(solicitud, razones):
    if solicitud.credito_id:
        razones.append('solicitud_ya_tiene_credito')

    if not solicitud.accepted_terms:
        razones.append('terminos_no_aceptados')

    tiene_organizacion_activa = solicitud.organization_id and solicitud.organization.is_active
    tiene_portal_activo = solicitud.configuracion_portal_id and solicitud.configuracion_portal.activo
    if not tiene_organizacion_activa and not tiene_portal_activo:
        razones.append('organizacion_inactiva')


def _evaluar_configuracion_vigente(solicitud, razones):
    configuracion = solicitud.product_config or solicitud.configuracion_portal

    if configuracion is None:
        razones.append('configuracion_producto_requerida')
        return

    if not configuracion.is_active:
        razones.append('configuracion_producto_inactiva')

    if solicitud.product_config_id and configuracion.organization_id != solicitud.organization_id:
        razones.append('configuracion_producto_no_pertenece_a_organizacion')

    if solicitud.requested_amount is None:
        razones.append('monto_solicitado_pendiente_simulacion')
    elif solicitud.requested_amount < configuracion.min_amount or solicitud.requested_amount > configuracion.max_amount:
        razones.append('monto_fuera_de_configuracion_vigente')

    if solicitud.term_months is None:
        razones.append('plazo_solicitado_pendiente_simulacion')
    elif solicitud.term_months < configuracion.min_term_months or solicitud.term_months > configuracion.max_term_months:
        razones.append('plazo_fuera_de_configuracion_vigente')


def _evaluar_documentos_requeridos(solicitud, razones, documentos_faltantes, documentos_rechazados):
    for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
        documento = obtener_ultimo_documento_por_tipo(solicitud, tipo_documento)

        if documento is None:
            documentos_faltantes.append(tipo_documento)
            razones.append(f'documento_faltante:{tipo_documento}')
            continue

        if documento.status == ContractorApplicationDocument.Estado.RECHAZADO:
            documentos_rechazados.append(tipo_documento)
            razones.append(f'documento_rechazado:{tipo_documento}')
            continue

        if documento.status != ContractorApplicationDocument.Estado.APROBADO:
            razones.append(f'documento_no_aprobado:{tipo_documento}')


def _resultado(*, solicitud_id, razones, documentos_faltantes, documentos_rechazados):
    razones = tuple(razones)
    elegible = not razones
    razon = 'elegible' if elegible else razones[0]
    return ResultadoElegibilidadConversionContratista(
        solicitud_id=solicitud_id,
        elegible=elegible,
        razon=razon,
        razones=razones,
        documentos_faltantes=tuple(documentos_faltantes),
        documentos_rechazados=tuple(documentos_rechazados),
    )

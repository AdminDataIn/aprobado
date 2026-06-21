from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from contractors.models import ContractorApplication
from contractors.services.timeline import registrar_evento_timeline_prestador
from libranza.escenarios_credito import NUEVO_CREDITO


class ErrorSolicitudContratista(ValueError):
    pass


@dataclass(frozen=True)
class DatosSolicitudContratista:
    tipo_documento: str
    numero_documento: str
    nombres: str
    apellidos: str
    celular: str
    correo: str
    monto_solicitado: Decimal | None = None
    plazo_meses: int | None = None
    escenario_credito: str = NUEVO_CREDITO
    direccion: str = ''
    terminos_aceptados: bool = False
    cuota_mensual_estimada: Decimal = Decimal('0.00')
    payload_simulacion: dict = field(default_factory=dict)
    subdominio_origen: str = ''
    ip_address: str | None = None
    user_agent: str = ''


@dataclass(frozen=True)
class ResultadoSolicitudContratista:
    solicitud: ContractorApplication

    @property
    def solicitud_id(self):
        return self.solicitud.id

    @property
    def estado(self):
        return self.solicitud.status


def crear_solicitud_contratista(
    *,
    organizacion=None,
    configuracion_producto=None,
    configuracion_portal=None,
    datos=None,
    usuario=None,
):
    if organizacion is None and configuracion_portal is None:
        raise ErrorSolicitudContratista('organizacion_o_configuracion_portal_requerida')
    if configuracion_producto is None and configuracion_portal is None:
        raise ErrorSolicitudContratista('configuracion_producto_requerida')
    if not isinstance(datos, DatosSolicitudContratista):
        raise ErrorSolicitudContratista('datos_solicitud_invalidos')

    solicitud = ContractorApplication(
        organization=organizacion,
        configuracion_portal=configuracion_portal,
        product_config=configuracion_producto,
        usuario=usuario,
        status=ContractorApplication.Estado.RECIBIDA,
        escenario_credito=datos.escenario_credito,
        requested_amount=datos.monto_solicitado,
        term_months=datos.plazo_meses,
        estimated_monthly_payment=datos.cuota_mensual_estimada,
        simulation_payload=datos.payload_simulacion or {},
        document_type=datos.tipo_documento,
        document_number=datos.numero_documento,
        first_name=datos.nombres,
        last_name=datos.apellidos,
        phone=datos.celular,
        email=datos.correo,
        address=datos.direccion,
        accepted_terms=datos.terminos_aceptados,
        source_subdomain=datos.subdominio_origen or getattr(organizacion, 'subdomain', '') or getattr(configuracion_portal, 'slug', ''),
        ip_address=datos.ip_address,
        user_agent=datos.user_agent,
    )

    try:
        solicitud.full_clean()
    except ValidationError:
        raise

    with transaction.atomic():
        solicitud.save()

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='SOLICITUD_CREADA',
        titulo='Solicitud de prestador creada',
        descripcion='Se registró la solicitud inicial de prestador de servicios.',
        estado_resultante=solicitud.status,
        metadata={
            'solicitud_id': solicitud.id,
            'escenario_credito': solicitud.escenario_credito,
            'monto_solicitado_definido': solicitud.requested_amount is not None,
            'plazo_meses_definido': solicitud.term_months is not None,
            'configuracion_portal_id': solicitud.configuracion_portal_id,
        },
        usuario=usuario,
    )

    return ResultadoSolicitudContratista(solicitud=solicitud)

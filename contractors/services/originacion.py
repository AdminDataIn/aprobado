from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
)
from contractors.selectors import obtener_ultimo_documento_por_tipo
from contractors.services.timeline import registrar_evento_timeline_prestador
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado
from gestion_creditos.services.name_normalization import normalize_name_upper


PERMISO_ORIGINAR_CREDITO_PRESTADOR = 'contractors.can_originate_contractor_credit'


class ErrorOriginacionPrestador(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoOriginacionPrestador:
    credito: Credito
    detalle: CreditoLibranza
    auditoria: PredecisionPrestadorAudit
    solicitud: ContractorApplication


def puede_originar_credito_prestador(auditoria):
    if not auditoria:
        return False
    solicitud = auditoria.solicitud
    return (
        auditoria.decision == 'PREAPROBADO_READ_ONLY'
        and auditoria.eligible
        and solicitud.status != ContractorApplication.Estado.CONVERTIDA
        and not solicitud.credito_id
    )


@transaction.atomic
def originar_credito_prestador_desde_auditoria(auditoria, usuario=None, request=None):
    usuario = _resolver_usuario(usuario=usuario, request=request)
    _validar_permiso(usuario)

    auditoria_bloqueada = (
        PredecisionPrestadorAudit.objects
        .select_for_update()
        .select_related('solicitud')
        .get(pk=auditoria.pk)
    )
    solicitud = (
        ContractorApplication.objects
        .select_for_update()
        .select_related('usuario', 'configuracion_portal')
        .get(pk=auditoria_bloqueada.solicitud_id)
    )
    auditoria_bloqueada.solicitud = solicitud

    _validar_auditoria(auditoria_bloqueada)
    _validar_solicitud(solicitud)
    datos_contractuales = _obtener_datos_contractuales(solicitud)
    empresa = datos_contractuales.empresa
    documentos = _obtener_documentos_origen(solicitud)

    monto_final = _menor_decimal(solicitud.requested_amount, auditoria_bloqueada.monto_maximo_sugerido)
    plazo_final = min(int(solicitud.term_months or 0), int(auditoria_bloqueada.plazo_maximo_sugerido or 0))
    if monto_final <= Decimal('0.00'):
        raise ErrorOriginacionPrestador('monto_final_invalido')
    if plazo_final <= 0:
        raise ErrorOriginacionPrestador('plazo_final_invalido')

    usuario_credito = solicitud.usuario or _obtener_o_crear_usuario_solicitud(solicitud)

    credito = Credito.objects.create(
        usuario=usuario_credito,
        linea=Credito.LineaCredito.LIBRANZA,
        estado=Credito.EstadoCredito.EN_REVISION,
        monto_solicitado=monto_final,
        plazo_solicitado=plazo_final,
        monto_aprobado=monto_final,
        plazo=plazo_final,
        tipo_regla_credito=Credito.TipoReglaCredito.NORMAL,
    )

    detalle = CreditoLibranza.objects.create(
        credito=credito,
        nombres=normalize_name_upper(solicitud.first_name),
        apellidos=normalize_name_upper(solicitud.last_name),
        cedula=solicitud.document_number,
        direccion=solicitud.address,
        telefono=solicitud.phone,
        correo_electronico=solicitud.email,
        empresa=empresa,
        ingresos_mensuales=None,
        cedula_frontal=documentos['cedula_frontal'].file.name,
        cedula_trasera=documentos['cedula_trasera'].file.name,
        certificado_laboral=documentos['contrato_actual'].file.name,
        certificado_bancario=documentos['certificado_bancario'].file.name,
        certificado_bancario_metadata={
            'origen': 'prestador_contratista',
            'solicitud_id': solicitud.id,
            'predecision_audit_id': auditoria_bloqueada.id,
            'escenario_credito': solicitud.escenario_credito,
            'score_final': str(auditoria_bloqueada.score_final) if auditoria_bloqueada.score_final is not None else None,
            'score_banda': auditoria_bloqueada.score_banda,
            'datacredito_status': auditoria_bloqueada.datacredito_status,
            'estado': 'pendiente',
        },
    )

    solicitud.credito = credito
    solicitud.status = ContractorApplication.Estado.CONVERTIDA
    payload = dict(solicitud.simulation_payload or {})
    payload['originacion_prestador'] = {
        'credito_id': credito.id,
        'numero_credito': credito.numero_credito,
        'predecision_audit_id': auditoria_bloqueada.id,
        'originado_por_id': usuario.id if usuario else None,
        'monto_final': str(monto_final),
        'plazo_final': plazo_final,
    }
    solicitud.simulation_payload = payload
    solicitud.save(update_fields=['credito', 'status', 'simulation_payload', 'updated_at'])

    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=None,
        estado_nuevo=Credito.EstadoCredito.EN_REVISION,
        usuario_modificacion=usuario,
        motivo=f'Credito de prestador originado desde predecision auditada #{auditoria_bloqueada.id}.',
    )

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        credito=credito,
        tipo_evento='ORIGINADO_EN_REVISION',
        titulo='Crédito originado en revisión',
        descripcion='Se creó crédito de libranza para prestador en estado EN_REVISION.',
        estado_resultante=credito.estado,
        metadata={
            'credito_id': credito.id,
            'numero_credito': credito.numero_credito,
            'auditoria_id': auditoria_bloqueada.id,
            'monto_final': monto_final,
            'plazo_final': plazo_final,
        },
        usuario=usuario,
        request=request,
    )

    return ResultadoOriginacionPrestador(
        credito=credito,
        detalle=detalle,
        auditoria=auditoria_bloqueada,
        solicitud=solicitud,
    )


def _resolver_usuario(*, usuario, request):
    if usuario is not None:
        return usuario if getattr(usuario, 'is_authenticated', True) else None
    if request is None:
        return None
    usuario_request = getattr(request, 'user', None)
    if usuario_request is not None and getattr(usuario_request, 'is_authenticated', False):
        return usuario_request
    return None


def _validar_permiso(usuario):
    if not getattr(usuario, 'is_authenticated', False) or not usuario.has_perm(PERMISO_ORIGINAR_CREDITO_PRESTADOR):
        raise PermissionDenied('No tiene permiso para originar credito de prestador.')


def _validar_auditoria(auditoria):
    if auditoria.decision != 'PREAPROBADO_READ_ONLY':
        raise ErrorOriginacionPrestador('auditoria_no_preaprobada')
    if not auditoria.eligible:
        raise ErrorOriginacionPrestador('auditoria_no_elegible')
    if not auditoria.solicitud_id:
        raise ErrorOriginacionPrestador('auditoria_sin_solicitud')


def _validar_solicitud(solicitud):
    if solicitud.status == ContractorApplication.Estado.CONVERTIDA or solicitud.credito_id:
        raise ErrorOriginacionPrestador('solicitud_ya_convertida')
    if not solicitud.accepted_terms:
        raise ErrorOriginacionPrestador('solicitud_sin_terminos')
    if not solicitud.usuario_id:
        raise ErrorOriginacionPrestador('solicitud_sin_usuario')


def _obtener_datos_contractuales(solicitud):
    try:
        datos = solicitud.informacion_laboral
    except AttributeError as exc:
        raise ErrorOriginacionPrestador('solicitud_sin_datos_contractuales') from exc
    if not datos.empresa_id:
        raise ErrorOriginacionPrestador('solicitud_sin_empresa')
    if not datos.empresa.permite_libranza:
        raise ErrorOriginacionPrestador('empresa_no_permite_libranza')
    return datos


def _obtener_documentos_origen(solicitud):
    mapa = {
        'cedula_frontal': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
        'cedula_trasera': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
        'contrato_actual': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
        'certificado_bancario': ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    }
    documentos = {}
    for clave, tipo in mapa.items():
        documento = obtener_ultimo_documento_por_tipo(solicitud, tipo)
        if not documento:
            raise ErrorOriginacionPrestador(f'documento_faltante:{tipo}')
        documentos[clave] = documento
    return documentos


def _menor_decimal(valor_a, valor_b):
    return min(Decimal(str(valor_a or '0')), Decimal(str(valor_b or '0'))).quantize(Decimal('0.01'))


def _obtener_o_crear_usuario_solicitud(solicitud):
    User = get_user_model()
    email = str(solicitud.email or '').strip().lower()
    usuario = User.objects.filter(email__iexact=email).first()
    if usuario:
        return usuario
    username = email or f'prestador-{solicitud.document_number}'
    usuario = User.objects.create(
        username=username,
        email=email,
        first_name=normalize_name_upper(solicitud.first_name)[:150],
        last_name=normalize_name_upper(solicitud.last_name)[:150],
        is_active=True,
    )
    usuario.set_unusable_password()
    usuario.save(update_fields=['password'])
    return usuario

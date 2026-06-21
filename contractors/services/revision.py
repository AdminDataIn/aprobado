from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from contractors.models import ContractorApplication, ContractorApplicationDocument


PERMISO_REVISAR_SOLICITUD = 'contractors.can_review_contractor_application'
PERMISO_REVISAR_DOCUMENTO = 'contractors.can_review_contractor_document'


def marcar_solicitud_en_revision(solicitud, usuario, observacion=None):
    _validar_permiso(usuario, PERMISO_REVISAR_SOLICITUD)
    _validar_solicitud_modificable(solicitud)

    if solicitud.status != ContractorApplication.Estado.RECIBIDA:
        raise ValidationError({'status': 'Solo una solicitud recibida puede pasar a en revision.'})

    solicitud.status = ContractorApplication.Estado.EN_REVISION
    _registrar_revision_solicitud(solicitud, usuario, observacion)
    solicitud.save(update_fields=['status', 'revisado_en', 'revisado_por', 'notas_revision', 'updated_at'])
    return solicitud


def rechazar_solicitud_contratista(solicitud, usuario, motivo):
    _validar_permiso(usuario, PERMISO_REVISAR_SOLICITUD)
    _validar_solicitud_modificable(solicitud)

    if solicitud.status not in {
        ContractorApplication.Estado.RECIBIDA,
        ContractorApplication.Estado.EN_REVISION,
    }:
        raise ValidationError({'status': 'La solicitud no puede pasar a rechazada desde su estado actual.'})
    if not motivo:
        raise ValidationError({'motivo': 'El motivo de rechazo es obligatorio.'})

    solicitud.status = ContractorApplication.Estado.RECHAZADA
    _registrar_revision_solicitud(solicitud, usuario, motivo)
    solicitud.save(update_fields=['status', 'revisado_en', 'revisado_por', 'notas_revision', 'updated_at'])
    return solicitud


def aprobar_documento_solicitud(documento, usuario, observacion=None):
    _validar_permiso(usuario, PERMISO_REVISAR_DOCUMENTO)
    _validar_documento_modificable(documento)

    documento.status = ContractorApplicationDocument.Estado.APROBADO
    _registrar_revision_documento(documento, usuario, observacion)
    documento.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'review_notes'])
    return documento


def rechazar_documento_solicitud(documento, usuario, motivo):
    _validar_permiso(usuario, PERMISO_REVISAR_DOCUMENTO)
    _validar_documento_modificable(documento)
    if not motivo:
        raise ValidationError({'motivo': 'El motivo de rechazo es obligatorio.'})

    documento.status = ContractorApplicationDocument.Estado.RECHAZADO
    _registrar_revision_documento(documento, usuario, motivo)
    documento.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'review_notes'])
    return documento


def _validar_permiso(usuario, permiso):
    if not getattr(usuario, 'is_authenticated', False) or not usuario.has_perm(permiso):
        raise PermissionDenied('No tiene permiso para ejecutar esta revision.')


def _validar_solicitud_modificable(solicitud):
    if solicitud is None:
        raise ValidationError({'solicitud': 'La solicitud contratista es obligatoria.'})
    if solicitud.status == ContractorApplication.Estado.CONVERTIDA:
        raise ValidationError({'status': 'Una solicitud convertida no puede modificarse por revision.'})


def _validar_documento_modificable(documento):
    if documento is None:
        raise ValidationError({'documento': 'El documento contratista es obligatorio.'})
    if documento.status != ContractorApplicationDocument.Estado.RECIBIDO:
        raise ValidationError({'status': 'Solo un documento recibido puede revisarse.'})
    if documento.application.status == ContractorApplication.Estado.CONVERTIDA:
        raise ValidationError({'application': 'No se puede revisar un documento de una solicitud convertida.'})
    if documento.application.status == ContractorApplication.Estado.RECHAZADA:
        raise ValidationError({'application': 'No se puede revisar un documento de una solicitud rechazada.'})


def _registrar_revision_solicitud(solicitud, usuario, observacion):
    solicitud.revisado_en = timezone.now()
    solicitud.revisado_por = usuario
    solicitud.notas_revision = observacion or ''


def _registrar_revision_documento(documento, usuario, observacion):
    documento.reviewed_at = timezone.now()
    documento.reviewed_by = usuario
    documento.review_notes = observacion or ''

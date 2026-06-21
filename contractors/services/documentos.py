from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from contractors.models import ContractorApplicationDocument
from contractors.services.timeline import registrar_evento_timeline_prestador


@dataclass(frozen=True)
class DatosDocumentoSolicitudContratista:
    tipo_documento: str
    archivo: object
    nombre_original: str = ''
    content_type: str = ''
    tamano_archivo: int | None = None


@dataclass(frozen=True)
class ResultadoDocumentoSolicitudContratista:
    documento: ContractorApplicationDocument

    @property
    def documento_id(self):
        return self.documento.id

    @property
    def estado(self):
        return self.documento.status


def registrar_documento_solicitud_contratista(*, solicitud, datos):
    if solicitud is None:
        raise ValidationError({'application': 'La solicitud contratista es obligatoria.'})
    if not isinstance(datos, DatosDocumentoSolicitudContratista):
        raise ValidationError({'documento': 'Los datos del documento son invalidos.'})

    archivo = datos.archivo
    nombre_original = datos.nombre_original
    content_type = datos.content_type
    tamano_archivo = datos.tamano_archivo
    if tamano_archivo is None:
        tamano_archivo = getattr(archivo, 'size', 0)
    tamano_real = getattr(archivo, 'size', None)
    if tamano_real is not None and tamano_archivo != tamano_real:
        raise ValidationError({'file_size': 'El tamano declarado no coincide con el archivo.'})

    documento = ContractorApplicationDocument(
        application=solicitud,
        document_type=datos.tipo_documento,
        file=archivo,
        original_filename=nombre_original,
        content_type=content_type,
        file_size=tamano_archivo,
        status=ContractorApplicationDocument.Estado.RECIBIDO,
    )
    documento.full_clean()

    with transaction.atomic():
        documento.save()

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='DOCUMENTOS_CARGADOS',
        titulo='Documento de prestador cargado',
        descripcion='Se registró un documento asociado a la solicitud.',
        estado_resultante=documento.status,
        metadata={
            'documento_id': documento.id,
            'tipo_documento': documento.document_type,
            'nombre_original': documento.original_filename,
            'content_type': documento.content_type,
            'file_size': documento.file_size,
        },
    )

    return ResultadoDocumentoSolicitudContratista(documento=documento)

from django.db import transaction

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    MAPA_CAMPOS_DOCUMENTOS_PRESTADOR,
)


@transaction.atomic
def guardar_documento_prestador(*, solicitud, tipo_documento, archivo, usuario):
    documento = (
        ContractorApplicationDocument.objects.select_for_update()
        .filter(solicitud=solicitud, tipo_documento=tipo_documento)
        .first()
    )
    archivo_anterior = documento.archivo.name if documento and documento.archivo else None
    storage = documento.archivo.storage if documento else ContractorApplicationDocument._meta.get_field('archivo').storage

    if documento is None:
        documento = ContractorApplicationDocument(
            solicitud=solicitud,
            tipo_documento=tipo_documento,
        )
    documento.archivo = archivo
    documento.uploaded_by = usuario
    documento.full_clean()
    documento.save()

    if archivo_anterior and archivo_anterior != documento.archivo.name:
        transaction.on_commit(lambda: storage.delete(archivo_anterior))
    return documento


def guardar_documentos_formulario(*, solicitud, cleaned_data, usuario):
    documentos = []
    for campo, tipo_documento in MAPA_CAMPOS_DOCUMENTOS_PRESTADOR.items():
        archivo = cleaned_data.get(campo)
        if archivo:
            documentos.append(
                guardar_documento_prestador(
                    solicitud=solicitud,
                    tipo_documento=tipo_documento,
                    archivo=archivo,
                    usuario=usuario,
                )
            )
    return documentos


def solicitud_tiene_documentos_obligatorios(solicitud):
    tipos_cargados = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    return set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(tipos_cargados)


def actualizar_estado_documental(solicitud):
    estado = (
        ContractorApplication.Estado.DOCUMENTOS_CARGADOS
        if solicitud_tiene_documentos_obligatorios(solicitud)
        else ContractorApplication.Estado.DOCUMENTOS_PENDIENTES
    )
    if solicitud.estado != estado:
        solicitud.estado = estado
        solicitud.save(update_fields=['estado', 'updated_at'])
    return estado

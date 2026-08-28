from django.db import transaction

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    MAPA_CAMPOS_DOCUMENTOS_PRESTADOR,
)
from contractors.services.evaluacion_audit import (
    ESTADOS_CON_EVALUACION,
    invalidar_evaluacion_si_cambiaron_datos,
)
from contractors.services.evaluacion_versionado import construir_version_datos


@transaction.atomic
def guardar_documento_prestador(
    *, solicitud, tipo_documento, archivo, usuario, metadata_captura=None,
    invalidar_evaluacion=True,
):
    debe_versionar = (
        invalidar_evaluacion
        and (
            solicitud.estado in ESTADOS_CON_EVALUACION
            or solicitud.auditorias_predecision.exists()
        )
    )
    version_anterior = construir_version_datos(solicitud)[0] if debe_versionar else ''
    documento = (
        ContractorApplicationDocument.objects.select_for_update()
        .filter(solicitud=solicitud, tipo_documento=tipo_documento)
        .first()
    )
    reemplaza_contrato = bool(
        documento is not None
        and tipo_documento == ContractorApplicationDocument.TipoDocumento.CONTRATO
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
    documento.metadata_captura = metadata_captura or {}
    documento.full_clean()
    documento.save()

    if reemplaza_contrato:
        solicitud.estado_analisis_contractual = (
            ContractorApplication.EstadoAnalisisContractual.NO_SOLICITADO
        )
        solicitud.metadata_analisis_contractual = {}
        solicitud.fecha_analisis_contractual = None
        solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
        solicitud.save(update_fields=[
            'estado_analisis_contractual', 'metadata_analisis_contractual',
            'fecha_analisis_contractual', 'estado', 'updated_at',
        ])

    if debe_versionar:
        invalidar_evaluacion_si_cambiaron_datos(
            solicitud,
            version_anterior=version_anterior,
            usuario=usuario,
            campos=['documentos', tipo_documento],
            motivo='documento_reemplazado',
        )

    if archivo_anterior and archivo_anterior != documento.archivo.name:
        transaction.on_commit(lambda: storage.delete(archivo_anterior))
    return documento


def guardar_documentos_formulario(
    *, solicitud, cleaned_data, usuario, metadata_documentos=None
):
    metadata_documentos = metadata_documentos or {}
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
                    metadata_captura=metadata_documentos.get(campo, {}),
                    invalidar_evaluacion=False,
                )
            )
    return documentos


def solicitud_tiene_documentos_obligatorios(solicitud):
    tipos_cargados = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    return set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(tipos_cargados)


def actualizar_estado_documental(solicitud):
    if solicitud.estado in ESTADOS_CON_EVALUACION:
        return solicitud.estado
    estado = (
        ContractorApplication.Estado.DOCUMENTOS_CARGADOS
        if solicitud_tiene_documentos_obligatorios(solicitud)
        else ContractorApplication.Estado.DOCUMENTOS_PENDIENTES
    )
    if solicitud.estado != estado:
        solicitud.estado = estado
        solicitud.save(update_fields=['estado', 'updated_at'])
    return estado

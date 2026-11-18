from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform

from contractors.models import ArchivoSolicitudPrestador, ContractorApplication, EvaluacionPrestadorJob
from gestion_creditos.models import Credito, CreditoLibranza, OrigenCreditoPrestador, ProcesamientoOCRDocumental
from integrations.models import ConsultaDatacreditoSnapshot


PERMISO_ARCHIVO = 'contractors.can_archive_contractor_application'


class SolicitudArchivada(ValidationError):
    def __init__(self):
        super().__init__('La solicitud esta archivada y solo esta disponible para consulta administrativa.',
                         code='solicitud_archivada')


def exigir_archivista(actor):
    if (not getattr(actor, 'is_authenticated', False) or not actor.is_active or not actor.is_staff
            or hasattr(actor, 'perfil_pagador') or not actor.has_perm(PERMISO_ARCHIVO)):
        raise PermissionDenied('Se requiere un administrador autorizado para archivar solicitudes.')


def exigir_solicitud_activa(solicitud_id):
    if ArchivoSolicitudPrestador.objects.filter(solicitud_id=solicitud_id).exists():
        raise SolicitudArchivada()


def bloquear_solicitud_activa(solicitud_id):
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud_id)
    exigir_solicitud_activa(solicitud_id)
    return solicitud


def bloqueos_archivo(solicitud):
    bloqueos = []
    if (Credito.objects.filter(usuario_id=solicitud.usuario_id).exists()
            or (solicitud.numero_documento and CreditoLibranza.objects.filter(cedula=solicitud.numero_documento).exists())
            or solicitud.estado in {solicitud.Estado.PENDIENTE_FIRMA, solicitud.Estado.FIRMADO}
            or OrigenCreditoPrestador.objects.filter(gate_id__in=solicitud.aprobaciones_internas.values('pk')).exists()
            or solicitud.timeline_operativo.filter(
                Q(tipo_evento__startswith='ORIGINACION_') | Q(metadata__has_key='origin_id')
                | Q(metadata__has_key='credito_id') | Q(metadata__has_key='formalizacion_id')
                | Q(metadata__has_key='pagare_id') | Q(metadata__has_key='desembolso_id')).exists()):
        bloqueos.append('REFERENCIA_FINANCIERA_O_FIRMA')
    if (solicitud.evaluacion_jobs.filter(estado__in=(
            EvaluacionPrestadorJob.Estado.PENDIENTE_PUBLICACION, EvaluacionPrestadorJob.Estado.ENCOLADA,
            EvaluacionPrestadorJob.Estado.EN_EJECUCION)).exists()
            or solicitud.auditorias_predecision.filter(estado_ejecucion='EN_PROCESO').exists()):
        bloqueos.append('EVALUACION_EN_CURSO')
    referencias = solicitud.autorizaciones_datacredito.annotate(ref=Cast('pk', CharField())).values('ref')
    if ConsultaDatacreditoSnapshot.objects.filter(autorizacion_referencia__in=referencias,
                                                   estado='EN_PROCESO').exists():
        bloqueos.append('CONSULTA_DATACREDITO_EN_CURSO')
    sesiones_documentales = solicitud.documentos.annotate(
        ref=KeyTextTransform('sesion_id', 'metadata_captura')).values('ref')
    if ProcesamientoOCRDocumental.objects.alias(ref=Cast('sesion_id', CharField())).filter(
        Q(sesion__solicitud=solicitud) | Q(ref__in=sesiones_documentales),
        estado__in=('PENDIENTE', 'PROCESANDO'),
    ).exists():
        bloqueos.append('OCR_EN_CURSO')
    return bloqueos


@transaction.atomic
def archivar_solicitud_prestador(*, solicitud_id, actor, motivo):
    exigir_archivista(actor)
    motivo = str(motivo or '').strip()
    if not motivo or len(motivo) > 500:
        raise ValidationError('Indica un motivo de archivado de 1 a 500 caracteres.')
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud_id)
    existente = ArchivoSolicitudPrestador.objects.filter(solicitud=solicitud).first()
    if existente:
        return existente, False
    bloqueos = bloqueos_archivo(solicitud)
    if bloqueos:
        raise ValidationError(bloqueos)
    return ArchivoSolicitudPrestador.objects.create(solicitud=solicitud, actor=actor, motivo=motivo), True

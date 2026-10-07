"""Applicant continuity guards; historical applications are never cancelled here."""
from django.contrib.auth import get_user_model

from contractors.models import ContractorApplication


ESTADOS_EN_PROCESO = tuple(
    estado for estado in ContractorApplication.Estado.values
    if estado not in ('NO_APROBADO', 'BORRADOR', 'DOCUMENTOS_PENDIENTES', 'DOCUMENTOS_CARGADOS')
)


def bloquear_solicitante(usuario):
    # Lock a stable parent, including when no application exists yet.
    get_user_model().objects.select_for_update().get(pk=usuario.pk)


def solicitud_en_proceso(usuario, *, excluir=None):
    return (ContractorApplication.objects.filter(usuario=usuario, estado__in=ESTADOS_EN_PROCESO)
            .exclude(pk=excluir).order_by('-created_at', '-pk').first())

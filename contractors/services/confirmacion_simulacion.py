"""One confirmation per persisted selection; optional durable dispatch after commit."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.forms import SimulacionPrestadorForm
from contractors.models import ContractorApplication
from contractors.services.capacidad_contractual import (
    obtener_configuracion_simulador_prestador,
    obtener_version_politica_simulador,
    simular_credito_prestador_informativo,
    snapshot_configuracion_financiera,
)
from contractors.services.continuidad_solicitud import bloquear_solicitante, solicitud_en_proceso
from contractors.services.evaluacion_audit import (
    ESTADOS_CON_EVALUACION, invalidar_evaluacion_si_cambiaron_datos, marcar_evaluacion_pendiente,
)
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from contractors.services.solicitud import solicitud_tiene_documentos_obligatorios


@dataclass(frozen=True)
class ConfirmacionSimulacion:
    reutilizada: bool


@transaction.atomic
def confirmar_condiciones_simulacion(*, solicitud, usuario, monto, plazo_meses):
    if not getattr(usuario, 'is_authenticated', False):
        raise PermissionDenied('Debes iniciar sesion para confirmar condiciones.')
    bloquear_solicitante(usuario)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.usuario_id != usuario.pk:
        raise PermissionDenied('No puedes confirmar otra solicitud.')
    monto = SimulacionPrestadorForm.base_fields['monto'].clean(monto)
    plazo = SimulacionPrestadorForm.base_fields['plazo_meses'].clean(plazo_meses)
    if not solicitud_tiene_documentos_obligatorios(solicitud):
        raise ValidationError('Completa los documentos obligatorios antes de registrar la solicitud.')
    if solicitud.estado_analisis_contractual not in {'COMPLETADO', 'CON_ADVERTENCIAS', 'NO_DISPONIBLE'}:
        raise ValidationError('El analisis contractual debe estar vigente antes de registrar la solicitud.')

    # A replay acknowledges the original evidence, not today's rates or calendar.
    if (solicitud.simulada_en is not None
            and solicitud.version_configuracion_financiera_simulacion
            and solicitud.monto_solicitado == solicitud.monto_simulado == monto
            and solicitud.plazo_meses == solicitud.plazo_simulado_meses == plazo):
        return ConfirmacionSimulacion(reutilizada=True)

    if solicitud.estado not in {
        solicitud.Estado.BORRADOR, solicitud.Estado.DOCUMENTOS_PENDIENTES,
        solicitud.Estado.DOCUMENTOS_CARGADOS, solicitud.Estado.EVALUACION_PENDIENTE,
        solicitud.Estado.EN_REVISION_MANUAL,
    } or solicitud.aprobaciones_internas.filter(
        estado__in=['PENDIENTE', 'EN_ANALISIS', 'APROBADA_PARA_ORIGINAR'],
    ).exists():
        raise ValidationError('La etapa actual no permite modificar las condiciones registradas.')
    if solicitud_en_proceso(usuario, excluir=solicitud.pk):
        raise ValidationError(
            'Ya tienes otra solicitud en proceso. Consulta su estado antes de continuar.',
            code='otra_solicitud_en_proceso',
        )
    configuracion = obtener_configuracion_simulador_prestador()
    form = SimulacionPrestadorForm(
        {'monto': monto, 'plazo_meses': plazo}, configuracion=configuracion,
        horizonte=horizonte_para_solicitud(solicitud, configuracion),
    )
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    resultado = simular_credito_prestador_informativo(
        monto=form.cleaned_data['monto'], plazo_meses=form.cleaned_data['plazo_meses'],
        configuracion=configuracion, solicitud=solicitud,
    )
    version_anterior, _ = construir_version_datos(solicitud)
    estado_anterior = solicitud.estado
    snapshot = snapshot_configuracion_financiera(configuracion)
    solicitud.monto_solicitado = solicitud.monto_simulado = resultado.monto_solicitado
    solicitud.plazo_meses = solicitud.plazo_simulado_meses = resultado.plazo_meses
    solicitud.version_configuracion_financiera_simulacion = snapshot['version']
    solicitud.version_politica_simulacion = obtener_version_politica_simulador(configuracion)
    solicitud.tasa_mensual_simulacion = snapshot['tasa_mensual']
    solicitud.monto_maximo_configuracion_simulacion = snapshot['monto_maximo']
    solicitud.plazo_maximo_configuracion_simulacion = snapshot['plazo_maximo_meses']
    solicitud.simulada_en = timezone.now()
    solicitud.metadata_analisis_contractual = {
        **solicitud.metadata_analisis_contractual,
        'simulacion_guardada': {
            **{k: str(v) if hasattr(v, 'as_tuple') else v for k, v in resultado.como_dict().items()},
            'version': snapshot['version'],
        },
    }
    solicitud.save(update_fields=[
        'monto_solicitado', 'plazo_meses', 'monto_simulado', 'plazo_simulado_meses',
        'version_configuracion_financiera_simulacion', 'version_politica_simulacion',
        'tasa_mensual_simulacion', 'monto_maximo_configuracion_simulacion',
        'plazo_maximo_configuracion_simulacion', 'simulada_en',
        'metadata_analisis_contractual', 'updated_at',
    ])
    if estado_anterior in ESTADOS_CON_EVALUACION:
        invalidar_evaluacion_si_cambiaron_datos(
            solicitud, version_anterior=version_anterior, usuario=usuario,
            campos=['monto_solicitado', 'plazo_meses'], motivo='simulacion_actualizada',
        )
    else:
        marcar_evaluacion_pendiente(solicitud, usuario=usuario, motivo='simulacion_registrada')
    from contractors.services.orquestacion_evaluacion import reservar_evaluacion_automatica
    reservar_evaluacion_automatica(solicitud)
    return ConfirmacionSimulacion(reutilizada=False)

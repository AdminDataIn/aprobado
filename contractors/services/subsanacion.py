from django.core.exceptions import ValidationError
from django.db import transaction

from contractors.models import (
    ContractorApplicationDocument,
    RequerimientoSubsanacionPrestador,
)
from contractors.services.evaluacion_audit import invalidar_evaluacion_si_cambiaron_datos
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.revision_manual import marcar_subsanacion_atendida
from contractors.services.solicitud import guardar_documento_prestador


@transaction.atomic
def atender_requerimiento_subsanacion(requerimiento, *, form, usuario):
    if requerimiento.solicitud.usuario_id != usuario.id:
        raise ValidationError('El requerimiento no pertenece al usuario autenticado.')
    if requerimiento.estado != RequerimientoSubsanacionPrestador.Estado.PENDIENTE:
        raise ValidationError('El requerimiento ya fue atendido o cerrado.')

    tipo = requerimiento.tipo
    if 'archivo' in form.cleaned_data:
        tipo_documento = _tipo_documento_para_requerimiento(tipo, form.cleaned_data)
        guardar_documento_prestador(
            solicitud=requerimiento.solicitud,
            tipo_documento=tipo_documento,
            archivo=form.cleaned_data['archivo'],
            usuario=usuario,
            metadata_captura={'source': 'subsanacion'},
        )
    elif form.campos_actualizables:
        _actualizar_campos_permitidos(requerimiento, form, usuario)
    else:
        raise ValidationError('El requerimiento no tiene una accion publica habilitada.')

    return marcar_subsanacion_atendida(requerimiento, usuario=usuario)


def _tipo_documento_para_requerimiento(tipo, cleaned_data):
    if tipo in {
        RequerimientoSubsanacionPrestador.Tipo.NUEVO_CONTRATO,
        RequerimientoSubsanacionPrestador.Tipo.ACTUALIZAR_CONTRATO,
        RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_CONTRACTUAL,
    }:
        return ContractorApplicationDocument.TipoDocumento.CONTRATO
    if tipo == RequerimientoSubsanacionPrestador.Tipo.CERTIFICACION_BANCARIA:
        return ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO
    if tipo == RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_IDENTIDAD:
        valor = cleaned_data.get('tipo_documento_carga')
        if valor not in {
            ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
            ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
        }:
            raise ValidationError('Selecciona una cara valida del documento de identidad.')
        return valor
    raise ValidationError('El tipo de documento solicitado no esta permitido.')


def _actualizar_campos_permitidos(requerimiento, form, usuario):
    solicitud = requerimiento.solicitud
    version_anterior = construir_version_datos(solicitud)[0]
    campos = []
    for nombre in form.campos_actualizables:
        if nombre not in form.cleaned_data:
            continue
        valor = form.cleaned_data[nombre]
        if valor in (None, ''):
            continue
        setattr(solicitud, nombre, valor)
        campos.append(nombre)
    if not campos:
        raise ValidationError('Registra al menos un dato para atender el requerimiento.')
    solicitud.full_clean(exclude=['usuario', 'empresa'])
    solicitud.save(update_fields=[*campos, 'updated_at'])
    invalidar_evaluacion_si_cambiaron_datos(
        solicitud,
        version_anterior=version_anterior,
        usuario=usuario,
        campos=campos,
        motivo='subsanacion_atendida',
    )

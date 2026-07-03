from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from gestion_creditos import credit_services
from gestion_creditos.models import AprobacionPagadorLibranza, Credito
from usuarios.models import PerfilPagador


ESTADOS_DECIDIBLES_PAGADOR_LIBRANZA = (
    Credito.EstadoCredito.SOLICITUD,
    Credito.EstadoCredito.EN_REVISION,
    Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
)


@dataclass(frozen=True)
class ResultadoDecisionPagadorLibranza:
    credito: Credito
    estado_resultante: str
    mensaje: str
    requiere_aprobacion_final: bool = False
    aprobacion: AprobacionPagadorLibranza | None = None


def _obtener_empresa_credito(credito):
    empresa = credito.empresa_relacionada
    if not empresa:
        raise ValidationError('El credito no tiene empresa relacionada.')
    return empresa


def _obtener_perfil_pagador(usuario, empresa):
    perfil = getattr(usuario, 'perfil_pagador', None)
    if not perfil or perfil.empresa_id != empresa.id or not perfil.es_pagador:
        raise PermissionDenied('Usuario pagador no autorizado para esta empresa.')
    return perfil


def _validar_nivel(perfil, nivel_requerido):
    nivel_usuario = perfil.nivel_aprobacion_libranza
    if nivel_usuario == PerfilPagador.NivelAprobacionLibranza.AMBOS:
        return
    if nivel_usuario != nivel_requerido:
        raise PermissionDenied('El usuario pagador no tiene el nivel requerido para esta decision.')


def _nivel_requerido_para_estado(credito):
    if credito.estado == Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL:
        return (
            PerfilPagador.NivelAprobacionLibranza.FINAL,
            AprobacionPagadorLibranza.Nivel.FINAL,
        )
    return (
        PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        AprobacionPagadorLibranza.Nivel.NIVEL_1,
    )


def puede_decidir_solicitud_libranza_por_pagador(credito, usuario):
    if credito.linea != Credito.LineaCredito.LIBRANZA:
        return False
    if credito.estado not in ESTADOS_DECIDIBLES_PAGADOR_LIBRANZA:
        return False

    try:
        empresa = _obtener_empresa_credito(credito)
        perfil = _obtener_perfil_pagador(usuario, empresa)
        _validar_sin_decision_final_o_rechazo(credito)
    except (PermissionDenied, ValidationError):
        return False

    if not empresa.requiere_doble_aprobacion_libranza:
        return True

    nivel_requerido, nivel_auditoria = _nivel_requerido_para_estado(credito)
    try:
        _validar_nivel(perfil, nivel_requerido)
    except PermissionDenied:
        return False

    if nivel_auditoria == AprobacionPagadorLibranza.Nivel.FINAL:
        aprobacion_nivel_1 = AprobacionPagadorLibranza.objects.filter(
            credito=credito,
            nivel=AprobacionPagadorLibranza.Nivel.NIVEL_1,
            decision=AprobacionPagadorLibranza.Decision.APROBADO,
        ).order_by('-created_at').first()
        if not aprobacion_nivel_1:
            return False
        if empresa.requiere_aprobadores_distintos_libranza and aprobacion_nivel_1.usuario_id == usuario.id:
            return False

    return True


def _asegurar_monto_y_plazo(credito):
    campos = []
    if credito.monto_aprobado is None:
        credito.monto_aprobado = credito.monto_solicitado
        campos.append('monto_aprobado')
    if credito.plazo is None:
        credito.plazo = credito.plazo_solicitado
        campos.append('plazo')
    if campos:
        credito.save(update_fields=campos)


def _registrar_auditoria(credito, empresa, usuario, nivel, decision, observacion):
    return AprobacionPagadorLibranza.objects.create(
        credito=credito,
        empresa=empresa,
        usuario=usuario,
        nivel=nivel,
        decision=decision,
        observacion=observacion or '',
    )


def _validar_sin_decision_final_o_rechazo(credito):
    if AprobacionPagadorLibranza.objects.filter(
        credito=credito,
        nivel=AprobacionPagadorLibranza.Nivel.FINAL,
        decision__in=[
            AprobacionPagadorLibranza.Decision.APROBADO,
            AprobacionPagadorLibranza.Decision.RECHAZADO,
        ],
    ).exists():
        raise ValidationError('La decision final del pagador ya fue registrada.')

    if AprobacionPagadorLibranza.objects.filter(
        credito=credito,
        decision=AprobacionPagadorLibranza.Decision.RECHAZADO,
    ).exists():
        raise ValidationError('La solicitud ya fue rechazada por pagador.')


def _aprobar_final_y_continuar_flujo_existente(credito, usuario, empresa, nivel, observacion, mensaje):
    _asegurar_monto_y_plazo(credito)
    aprobacion = _registrar_auditoria(
        credito,
        empresa,
        usuario,
        nivel,
        AprobacionPagadorLibranza.Decision.APROBADO,
        observacion,
    )
    motivo = observacion or 'Aprobado por pagador y enviado directamente a firma.'
    credit_services.gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.APROBADO_PAGADOR,
        usuario_modificacion=usuario,
        motivo=motivo,
    )
    credit_services.preparar_documento_para_firma(
        credito=credito,
        usuario_modificacion=usuario,
    )
    return ResultadoDecisionPagadorLibranza(
        credito=credito,
        estado_resultante=Credito.EstadoCredito.APROBADO_PAGADOR,
        mensaje=mensaje,
        aprobacion=aprobacion,
    )


def _rechazar(credito, usuario, empresa, nivel, observacion):
    aprobacion = _registrar_auditoria(
        credito,
        empresa,
        usuario,
        nivel,
        AprobacionPagadorLibranza.Decision.RECHAZADO,
        observacion,
    )
    motivo = observacion or 'Rechazado por pagador.'
    credit_services.gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.RECHAZADO,
        usuario_modificacion=usuario,
        motivo=motivo,
    )
    return ResultadoDecisionPagadorLibranza(
        credito=credito,
        estado_resultante=Credito.EstadoCredito.RECHAZADO,
        mensaje=f'Credito {credito.numero_credito} rechazado.',
        aprobacion=aprobacion,
    )


@transaction.atomic
def decidir_solicitud_libranza_por_pagador(credito, usuario, accion, observacion=''):
    if accion not in {'approve', 'reject'}:
        raise ValidationError('Accion no valida.')

    credito = (
        Credito.objects
        .select_for_update(of=('self',))
        .get(pk=credito.pk)
    )

    if credito.linea != Credito.LineaCredito.LIBRANZA:
        raise ValidationError('Este servicio solo aplica para creditos de libranza.')

    if credito.estado not in ESTADOS_DECIDIBLES_PAGADOR_LIBRANZA:
        raise ValidationError('Esta solicitud ya no admite decisiones del pagador.')

    empresa = _obtener_empresa_credito(credito)
    perfil = _obtener_perfil_pagador(usuario, empresa)
    _validar_sin_decision_final_o_rechazo(credito)

    if not empresa.requiere_doble_aprobacion_libranza:
        nivel = AprobacionPagadorLibranza.Nivel.FINAL
        if accion == 'reject':
            return _rechazar(credito, usuario, empresa, nivel, observacion)
        return _aprobar_final_y_continuar_flujo_existente(
            credito,
            usuario,
            empresa,
            nivel,
            observacion,
            f'Solicitud {credito.numero_credito} aprobada por pagador y enviada a firma.',
        )

    nivel_requerido, nivel_auditoria = _nivel_requerido_para_estado(credito)

    _validar_nivel(perfil, nivel_requerido)

    if accion == 'reject':
        return _rechazar(credito, usuario, empresa, nivel_auditoria, observacion)

    if nivel_auditoria == AprobacionPagadorLibranza.Nivel.NIVEL_1:
        if AprobacionPagadorLibranza.objects.filter(
            credito=credito,
            nivel=AprobacionPagadorLibranza.Nivel.NIVEL_1,
            decision=AprobacionPagadorLibranza.Decision.APROBADO,
        ).exists():
            raise ValidationError('La aprobacion de nivel 1 ya fue registrada.')

        aprobacion = _registrar_auditoria(
            credito,
            empresa,
            usuario,
            AprobacionPagadorLibranza.Nivel.NIVEL_1,
            AprobacionPagadorLibranza.Decision.APROBADO,
            observacion,
        )
        motivo = observacion or 'Aprobado por pagador nivel 1. Pendiente aprobacion final.'
        credit_services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
            usuario_modificacion=usuario,
            motivo=motivo,
        )
        return ResultadoDecisionPagadorLibranza(
            credito=credito,
            estado_resultante=Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
            mensaje=f'Solicitud {credito.numero_credito} aprobada en nivel 1. Queda pendiente aprobacion final.',
            requiere_aprobacion_final=True,
            aprobacion=aprobacion,
        )

    aprobacion_nivel_1 = AprobacionPagadorLibranza.objects.filter(
        credito=credito,
        nivel=AprobacionPagadorLibranza.Nivel.NIVEL_1,
        decision=AprobacionPagadorLibranza.Decision.APROBADO,
    ).order_by('-created_at').first()
    if not aprobacion_nivel_1:
        raise ValidationError('No existe aprobacion de nivel 1 para esta solicitud.')
    if empresa.requiere_aprobadores_distintos_libranza and aprobacion_nivel_1.usuario_id == usuario.id:
        raise PermissionDenied('La aprobacion final debe registrarla un usuario distinto al aprobador de nivel 1.')

    return _aprobar_final_y_continuar_flujo_existente(
        credito,
        usuario,
        empresa,
        AprobacionPagadorLibranza.Nivel.FINAL,
        observacion,
        f'Solicitud {credito.numero_credito} aprobada por pagador final y enviada a firma.',
    )

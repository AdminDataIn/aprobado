from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from gestion_creditos import credit_services
from gestion_creditos.models import AprobacionPagadorLibranza, Credito
from usuarios.models import PerfilPagador


ESTADOS_DECIDIBLES_PAGADOR = (
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


def _perfil_pagador(usuario, empresa):
    perfil = getattr(usuario, 'perfil_pagador', None)
    if not perfil or perfil.empresa_id != empresa.id or not perfil.es_pagador:
        raise PermissionDenied('Usuario pagador no autorizado para esta empresa.')
    return perfil


def _puede_aprobar_nivel_1(perfil):
    return perfil.nivel_aprobacion_libranza in {
        PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        PerfilPagador.NivelAprobacionLibranza.AMBOS,
    }


def _puede_aprobar_final(perfil):
    return perfil.nivel_aprobacion_libranza in {
        PerfilPagador.NivelAprobacionLibranza.FINAL,
        PerfilPagador.NivelAprobacionLibranza.AMBOS,
    }


def _registrar_auditoria(credito, empresa, usuario, nivel, decision, observacion):
    return AprobacionPagadorLibranza.objects.create(
        credito=credito,
        empresa=empresa,
        nivel=nivel,
        decision=decision,
        usuario=usuario,
        observacion=observacion or '',
    )


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


def decidir_solicitud_libranza_por_pagador(*, credito, empresa, usuario, accion, observacion=''):
    if accion not in {'approve', 'reject'}:
        raise ValidationError('Accion no valida.')
    if credito.linea != Credito.LineaCredito.LIBRANZA:
        raise ValidationError('La doble aprobacion aplica solo a libranza.')
    if credito.empresa_relacionada != empresa:
        raise PermissionDenied('No tiene permiso para gestionar este credito.')
    if credito.estado not in ESTADOS_DECIDIBLES_PAGADOR:
        raise ValidationError('Esta solicitud ya no admite decisiones del pagador.')

    perfil = _perfil_pagador(usuario, empresa)

    with transaction.atomic():
        credito = Credito.objects.select_for_update().get(id=credito.id)
        if credito.estado not in ESTADOS_DECIDIBLES_PAGADOR:
            raise ValidationError('Esta solicitud ya cambio de estado y no admite una nueva decision.')

        aprobacion_nivel_1 = (
            AprobacionPagadorLibranza.objects
            .filter(
                credito=credito,
                nivel=AprobacionPagadorLibranza.Nivel.NIVEL_1,
                decision=AprobacionPagadorLibranza.Decision.APROBADO,
            )
            .order_by('-created_at')
            .first()
        )
        decision_final = (
            AprobacionPagadorLibranza.objects
            .filter(credito=credito, nivel=AprobacionPagadorLibranza.Nivel.FINAL)
            .order_by('-created_at')
            .first()
        )
        if decision_final:
            raise ValidationError('La decision final del pagador ya fue registrada.')

        if not empresa.requiere_doble_aprobacion_libranza:
            if accion == 'reject':
                aprobacion = _registrar_auditoria(
                    credito,
                    empresa,
                    usuario,
                    AprobacionPagadorLibranza.Nivel.FINAL,
                    AprobacionPagadorLibranza.Decision.RECHAZADO,
                    observacion,
                )
                credit_services.gestionar_cambio_estado_credito(
                    credito=credito,
                    nuevo_estado=Credito.EstadoCredito.RECHAZADO,
                    usuario_modificacion=usuario,
                    motivo=observacion or 'Rechazado por pagador.',
                )
                return ResultadoDecisionPagadorLibranza(
                    credito=credito,
                    estado_resultante=Credito.EstadoCredito.RECHAZADO,
                    mensaje=f'Credito {credito.numero_credito} rechazado.',
                    aprobacion=aprobacion,
                )

            _asegurar_monto_y_plazo(credito)
            aprobacion = _registrar_auditoria(
                credito,
                empresa,
                usuario,
                AprobacionPagadorLibranza.Nivel.FINAL,
                AprobacionPagadorLibranza.Decision.APROBADO,
                observacion,
            )
            credit_services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.APROBADO_PAGADOR,
                usuario_modificacion=usuario,
                motivo=observacion or 'Aprobado por pagador y enviado directamente a firma.',
            )
            credit_services.preparar_documento_para_firma(credito=credito, usuario_modificacion=usuario)
            return ResultadoDecisionPagadorLibranza(
                credito=credito,
                estado_resultante=Credito.EstadoCredito.APROBADO_PAGADOR,
                mensaje=f'Solicitud {credito.numero_credito} aprobada por pagador y enviada a firma.',
                aprobacion=aprobacion,
            )

        if not aprobacion_nivel_1:
            if not _puede_aprobar_nivel_1(perfil):
                raise PermissionDenied('Usuario no autorizado para aprobacion nivel 1.')
            decision = (
                AprobacionPagadorLibranza.Decision.APROBADO
                if accion == 'approve'
                else AprobacionPagadorLibranza.Decision.RECHAZADO
            )
            aprobacion = _registrar_auditoria(
                credito,
                empresa,
                usuario,
                AprobacionPagadorLibranza.Nivel.NIVEL_1,
                decision,
                observacion,
            )
            if accion == 'reject':
                credit_services.gestionar_cambio_estado_credito(
                    credito=credito,
                    nuevo_estado=Credito.EstadoCredito.RECHAZADO,
                    usuario_modificacion=usuario,
                    motivo=observacion or 'Rechazado por pagador en aprobacion nivel 1.',
                )
                return ResultadoDecisionPagadorLibranza(
                    credito=credito,
                    estado_resultante=Credito.EstadoCredito.RECHAZADO,
                    mensaje=f'Credito {credito.numero_credito} rechazado en nivel 1.',
                    aprobacion=aprobacion,
                )

            credit_services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
                usuario_modificacion=usuario,
                motivo=observacion or 'Aprobacion nivel 1 registrada. Pendiente aprobacion final de empresa.',
            )
            return ResultadoDecisionPagadorLibranza(
                credito=credito,
                estado_resultante=Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL,
                mensaje=f'Solicitud {credito.numero_credito} aprobada en nivel 1. Pendiente aprobacion final.',
                requiere_aprobacion_final=True,
                aprobacion=aprobacion,
            )

        if not _puede_aprobar_final(perfil):
            raise PermissionDenied('Usuario no autorizado para aprobacion final.')
        if (
            empresa.requiere_aprobadores_distintos_libranza
            and aprobacion_nivel_1.usuario_id == usuario.id
        ):
            raise PermissionDenied('La aprobacion final debe realizarla un usuario diferente.')

        decision = (
            AprobacionPagadorLibranza.Decision.APROBADO
            if accion == 'approve'
            else AprobacionPagadorLibranza.Decision.RECHAZADO
        )
        aprobacion = _registrar_auditoria(
            credito,
            empresa,
            usuario,
            AprobacionPagadorLibranza.Nivel.FINAL,
            decision,
            observacion,
        )
        if accion == 'reject':
            credit_services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.RECHAZADO,
                usuario_modificacion=usuario,
                motivo=observacion or 'Rechazado por pagador en aprobacion final.',
            )
            return ResultadoDecisionPagadorLibranza(
                credito=credito,
                estado_resultante=Credito.EstadoCredito.RECHAZADO,
                mensaje=f'Credito {credito.numero_credito} rechazado en aprobacion final.',
                aprobacion=aprobacion,
            )

        _asegurar_monto_y_plazo(credito)
        credit_services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=Credito.EstadoCredito.APROBADO_PAGADOR,
            usuario_modificacion=usuario,
            motivo=observacion or 'Aprobacion final de empresa registrada.',
        )
        credit_services.preparar_documento_para_firma(credito=credito, usuario_modificacion=usuario)
        return ResultadoDecisionPagadorLibranza(
            credito=credito,
            estado_resultante=Credito.EstadoCredito.APROBADO_PAGADOR,
            mensaje=f'Solicitud {credito.numero_credito} aprobada definitivamente y enviada a firma.',
            aprobacion=aprobacion,
        )

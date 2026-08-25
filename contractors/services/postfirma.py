from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from contractors.models import FormalizacionCreditoPrestador
from gestion_creditos.credit_services import gestionar_cambio_estado_credito
from gestion_creditos.models import Credito, HistorialEstado, OrigenCreditoPrestador, Pagare


PERMISO_PREPARAR_TRANSFERENCIA = 'contractors.can_prepare_contractor_transfer'
PERMISO_CONFIRMAR_DESEMBOLSO = 'contractors.can_confirm_contractor_disbursement'


@dataclass(frozen=True)
class ResultadoPostfirmaPrestador:
    credito: Credito
    historial: HistorialEstado
    reutilizado: bool


@transaction.atomic
def preparar_transferencia_credito_prestador(credito, *, actor):
    _exigir_actor(actor, PERMISO_PREPARAR_TRANSFERENCIA)
    credito = Credito.objects.select_for_update(of=('self',)).get(pk=credito.pk)
    clave = f'prestador:{credito.pk}:pendiente-transferencia:v1'
    historial = HistorialEstado.objects.filter(clave_idempotencia=clave).first()
    if historial:
        if credito.estado not in {
            Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            Credito.EstadoCredito.ACTIVO,
        }:
            raise ValidationError('La operacion idempotente no coincide con el estado actual.')
        return ResultadoPostfirmaPrestador(credito, historial, True)
    _exigir_no_anulado(credito)
    if credito.estado != Credito.EstadoCredito.FIRMADO:
        raise ValidationError('Solo un credito FIRMADO puede prepararse para transferencia.')
    _validar_cierre_formalizacion(credito)
    _obtener_snapshot_valido(credito)
    historial = gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        motivo='Cierre postfirma de prestador preparado para transferencia.',
        usuario_modificacion=actor,
        clave_idempotencia=clave,
    )
    credito.refresh_from_db()
    return ResultadoPostfirmaPrestador(credito, historial, False)


@transaction.atomic
def confirmar_desembolso_credito_prestador(credito, *, comprobante, actor):
    _exigir_actor(actor, PERMISO_CONFIRMAR_DESEMBOLSO)
    if comprobante is None:
        raise ValidationError('El comprobante de desembolso es obligatorio.')
    credito = Credito.objects.select_for_update(of=('self',)).get(pk=credito.pk)
    clave = f'prestador:{credito.pk}:desembolso-confirmado:v1'
    historial = HistorialEstado.objects.filter(clave_idempotencia=clave).first()
    if historial:
        if credito.estado != Credito.EstadoCredito.ACTIVO:
            raise ValidationError('La operacion idempotente no coincide con el estado actual.')
        return ResultadoPostfirmaPrestador(credito, historial, True)
    _exigir_no_anulado(credito)
    if credito.estado != Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA:
        raise ValidationError(
            'Solo un credito PENDIENTE_TRANSFERENCIA puede desembolsarse.'
        )
    componentes = _obtener_snapshot_valido(credito)
    historial = gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.ACTIVO,
        motivo='Desembolso de prestador confirmado por el equipo financiero.',
        usuario_modificacion=actor,
        comprobante=comprobante,
        componentes_financieros=componentes,
        clave_idempotencia=clave,
    )
    credito.refresh_from_db()
    return ResultadoPostfirmaPrestador(credito, historial, False)


def _validar_cierre_formalizacion(credito):
    formalizacion = FormalizacionCreditoPrestador.objects.filter(
        credito=credito,
    ).first()
    if formalizacion is None or formalizacion.estado != FormalizacionCreditoPrestador.Estado.FIRMADO:
        raise ValidationError('La formalizacion del prestador no esta firmada.')
    pagare = Pagare.objects.filter(pk=formalizacion.pagare_id).first()
    if pagare is None or pagare.estado != Pagare.EstadoPagare.SIGNED:
        raise ValidationError('El pagare del prestador no esta firmado.')
    if (
        formalizacion.estado_identidad
        != FormalizacionCreditoPrestador.EstadoIdentidad.VALIDADA
        or formalizacion.identidad_usuario_id != credito.usuario_id
        or not formalizacion.identidad_selfie_validada
        or not formalizacion.identidad_documento_validada
        or not formalizacion.identidad_firmante_coincide
        or not formalizacion.identidad_evidencia_hash
    ):
        raise ValidationError('La formalizacion no tiene identidad valida y completa.')


def _obtener_snapshot_valido(credito):
    origen = OrigenCreditoPrestador.objects.filter(credito=credito).first()
    if origen is None or origen.estado != OrigenCreditoPrestador.Estado.COMPLETADO:
        raise ValidationError('El credito no tiene origen de prestador completado.')
    return origen.componentes_financieros(validar_hash=True)


def _exigir_no_anulado(credito):
    if credito.estado == Credito.EstadoCredito.ANULADO:
        raise ValidationError('Un credito anulado no admite operaciones postfirma.')


def _exigir_actor(actor, permiso):
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied('Se requiere un usuario autenticado.')
    if not getattr(actor, 'is_active', False) or not getattr(actor, 'is_staff', False):
        raise PermissionDenied('Se requiere un usuario staff activo.')
    if hasattr(actor, 'perfil_pagador'):
        raise PermissionDenied('Un perfil pagador no puede ejecutar el cierre financiero.')
    if not actor.has_perm(permiso):
        raise PermissionDenied('No tienes permiso para ejecutar esta operacion.')

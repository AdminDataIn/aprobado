import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from contractors.datacredito.adapter import consultar_proveedor_datacredito_prestador
from contractors.datacredito.dto import (
    ResultadoConsultaDatacreditoPrestador,
    ResultadoNormalizadoDatacreditoPrestador,
)
from contractors.models import TimelinePrestador
from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from integrations.datacredito.exceptions import (
    DatacreditoConfigError,
    DatacreditoProviderDisabled,
    DatacreditoProviderError,
    DatacreditoTimeoutError,
)
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot


REUTILIZAR_SI_VIGENTE = 'REUTILIZAR_SI_VIGENTE'
FORZAR_CONSULTA = 'FORZAR_CONSULTA'
SOLO_CACHE = 'SOLO_CACHE'
MODOS_VALIDOS = {REUTILIZAR_SI_VIGENTE, FORZAR_CONSULTA, SOLO_CACHE}

ESTADO_AUTORIZACION_REQUERIDA = 'AUTORIZACION_REQUERIDA'
ESTADO_NO_CONFIGURADO = 'NO_CONFIGURADO'
ESTADO_SIN_CACHE = 'SIN_CACHE'


def obtener_evaluacion_datacredito_prestador(
    solicitud,
    modo=REUTILIZAR_SI_VIGENTE,
    solicitado_por=None,
    justificacion=None,
    servicio=None,
):
    _validar_actor(solicitud, solicitado_por)
    _validar_modo(modo, solicitado_por, justificacion)
    autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)
    if autorizacion is None:
        return _resultado_controlado(
            estado=ESTADO_AUTORIZACION_REQUERIDA,
            servicio=servicio or '',
            error_codigo='autorizacion_vigente_requerida',
        )

    configuracion = obtener_configuracion_datacredito()
    servicio = str(servicio or configuracion.default_service or '').lower()
    error_configuracion = _validar_configuracion(configuracion, servicio)
    if error_configuracion:
        return _resultado_controlado(
            estado=ESTADO_NO_CONFIGURADO,
            servicio=servicio,
            error_codigo=error_configuracion,
        )

    documento_hash = _hmac_documento(
        solicitud.numero_documento,
        configuracion.document_hash_secret,
    )
    fingerprint = construir_fingerprint_datacredito(
        solicitud=solicitud,
        servicio=servicio,
        autorizacion=autorizacion,
        configuracion=configuracion,
        documento_hash=documento_hash,
    )
    if modo != FORZAR_CONSULTA:
        snapshot = _buscar_snapshot_reutilizable(fingerprint)
        if snapshot is not None:
            _registrar_timeline_snapshot(
                solicitud,
                snapshot,
                TimelinePrestador.TipoEvento.DATACREDITO_REUTILIZADO,
                solicitado_por,
                reutilizado=True,
            )
            return _resultado_desde_snapshot(snapshot, reutilizado=True)
    if modo == SOLO_CACHE:
        return _resultado_controlado(
            estado=ESTADO_SIN_CACHE,
            servicio=servicio,
            error_codigo='snapshot_vigente_no_encontrado',
        )

    snapshot_en_proceso = _reservar_consulta(
        solicitud=solicitud,
        autorizacion=autorizacion,
        configuracion=configuracion,
        servicio=servicio,
        documento_hash=documento_hash,
        fingerprint=fingerprint,
        solicitado_por=solicitado_por,
    )
    if snapshot_en_proceso.estado != ConsultaDatacreditoSnapshot.Estado.EN_PROCESO:
        return _resultado_desde_snapshot(snapshot_en_proceso, reutilizado=True)
    if getattr(snapshot_en_proceso, '_reserva_existente', False):
        return _resultado_desde_snapshot(snapshot_en_proceso, reutilizado=True)

    try:
        proveedor = consultar_proveedor_datacredito_prestador(
            solicitud,
            servicio=servicio,
        )
    except DatacreditoTimeoutError:
        return _finalizar_error(
            snapshot_en_proceso,
            solicitud=solicitud,
            usuario=solicitado_por,
            estado=ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO,
            codigo='timeout_proveedor',
            tipo='TIMEOUT',
        )
    except (DatacreditoConfigError, DatacreditoProviderDisabled):
        return _finalizar_error(
            snapshot_en_proceso,
            solicitud=solicitud,
            usuario=solicitado_por,
            estado=ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE,
            codigo='configuracion_proveedor_invalida',
            tipo='CONFIGURACION',
        )
    except DatacreditoProviderError as exc:
        transitorio = not exc.http_status or int(exc.http_status) >= 500
        return _finalizar_error(
            snapshot_en_proceso,
            solicitud=solicitud,
            usuario=solicitado_por,
            estado=(
                ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO
                if transitorio else ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE
            ),
            codigo='proveedor_no_disponible' if transitorio else 'peticion_rechazada',
            tipo=str(exc.error_tipo or exc.__class__.__name__)[:80],
        )
    except (TypeError, ValueError):
        return _finalizar_error(
            snapshot_en_proceso,
            solicitud=solicitud,
            usuario=solicitado_por,
            estado=ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE,
            codigo='respuesta_no_interpretable',
            tipo='RESPUESTA_INVALIDA',
        )

    if proveedor.estado_snapshot in {
        ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO,
        ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE,
    }:
        return _finalizar_error(
            snapshot_en_proceso,
            solicitud=solicitud,
            usuario=solicitado_por,
            estado=proveedor.estado_snapshot,
            codigo='respuesta_funcional_no_exitosa',
            tipo='RESPUESTA_FUNCIONAL',
        )

    ahora = timezone.now()
    snapshot_en_proceso.estado = proveedor.estado_snapshot
    snapshot_en_proceso.resultado_normalizado = proveedor.resultado_normalizado.como_dict()
    snapshot_en_proceso.codigo_http = proveedor.codigo_http
    snapshot_en_proceso.codigo_funcional = proveedor.codigo_funcional
    snapshot_en_proceso.consultado_en = ahora
    snapshot_en_proceso.vigente_hasta = ahora + timedelta(days=configuracion.reuse_days)
    snapshot_en_proceso.save(update_fields=[
        'estado',
        'resultado_normalizado',
        'codigo_http',
        'codigo_funcional',
        'consultado_en',
        'vigente_hasta',
        'updated_at',
    ])
    _registrar_timeline_snapshot(
        solicitud,
        snapshot_en_proceso,
        TimelinePrestador.TipoEvento.DATACREDITO_CONSULTADO,
        solicitado_por,
    )
    return _resultado_desde_snapshot(snapshot_en_proceso)


def construir_fingerprint_datacredito(
    *, solicitud, servicio, autorizacion, configuracion, documento_hash=None
):
    documento_hash = documento_hash or _hmac_documento(
        solicitud.numero_documento,
        configuracion.document_hash_secret,
    )
    parametros = {
        'ambiente': configuracion.environment,
        'servicio': servicio,
        'documento_hash': documento_hash,
        'autorizacion_version': autorizacion.version_texto,
        'autorizacion_texto_hash': autorizacion.texto_hash,
        'tipo_documento': solicitud.tipo_documento,
    }
    if servicio == ConsultaDatacreditoSnapshot.Servicio.HISTORIAL:
        parametros['product_id'] = configuracion.credenciales_servicio_historial.product_id
        parametros['info_account_type'] = (
            configuracion.credenciales_servicio_historial.info_account_type
        )
        parametros['parametros'] = list(configuracion.parametros_historial)
    serializado = json.dumps(
        parametros,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    )
    return hashlib.sha256(serializado.encode('utf-8')).hexdigest()


def _validar_actor(solicitud, usuario):
    if usuario is None:
        return
    if not getattr(usuario, 'is_authenticated', False):
        raise PermissionDenied('Debes iniciar sesión para consultar la evaluación.')
    if usuario.is_staff or solicitud.usuario_id == usuario.id:
        return
    raise PermissionDenied('No puedes consultar una solicitud de otro usuario.')


def _validar_modo(modo, usuario, justificacion):
    if modo not in MODOS_VALIDOS:
        raise ValidationError('Modo de consulta DataCrédito inválido.')
    if modo != FORZAR_CONSULTA:
        return
    if (
        usuario is None
        or not getattr(usuario, 'is_staff', False)
        or not usuario.has_perm('integrations.can_force_datacredito_refresh')
    ):
        raise PermissionDenied('No tienes permiso para forzar la consulta.')
    if not str(justificacion or '').strip():
        raise ValidationError('Debes indicar una justificación para forzar la consulta.')


def _validar_configuracion(configuracion, servicio):
    if not configuracion.real_enabled:
        return 'datacredito_deshabilitado'
    if not configuracion.document_hash_secret:
        return 'secreto_hash_documento_no_configurado'
    if servicio == ConsultaDatacreditoSnapshot.Servicio.DECISOR:
        faltantes = configuracion.credenciales_decisor.validar_para_token()
    elif servicio == ConsultaDatacreditoSnapshot.Servicio.HISTORIAL:
        faltantes = (
            configuracion.credenciales_historial.validar_para_token()
            + configuracion.credenciales_servicio_historial.validar_para_historial()
        )
    else:
        return 'servicio_datacredito_no_soportado'
    return 'credenciales_datacredito_incompletas' if faltantes else ''


def _buscar_snapshot_reutilizable(fingerprint):
    return (
        ConsultaDatacreditoSnapshot.objects.filter(
            fingerprint=fingerprint,
            estado__in=[
                ConsultaDatacreditoSnapshot.Estado.EXITOSO,
                ConsultaDatacreditoSnapshot.Estado.SIN_INFORMACION,
            ],
            vigente_hasta__gt=timezone.now(),
        )
        .order_by('-consultado_en', '-created_at')
        .first()
    )


def _reservar_consulta(
    *, solicitud, autorizacion, configuracion, servicio, documento_hash,
    fingerprint, solicitado_por,
):
    ahora = timezone.now()
    limite_proceso = ahora + timedelta(
        minutes=max(int(getattr(settings, 'DATACREDITO_IN_PROGRESS_MINUTES', 5) or 5), 1)
    )
    with transaction.atomic():
        procesos = ConsultaDatacreditoSnapshot.objects.select_for_update().filter(
            fingerprint=fingerprint,
            estado=ConsultaDatacreditoSnapshot.Estado.EN_PROCESO,
        )
        vigente = procesos.filter(vigente_hasta__gt=ahora).first()
        if vigente is not None:
            vigente._reserva_existente = True
            return vigente
        for obsoleto in procesos:
            obsoleto.estado = ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO
            obsoleto.error_codigo = 'consulta_en_proceso_expirada'
            obsoleto.error_tipo = 'PROCESO_EXPIRADO'
            obsoleto.vigente_hasta = ahora
            obsoleto.save(update_fields=[
                'estado', 'error_codigo', 'error_tipo', 'vigente_hasta', 'updated_at'
            ])
        try:
            with transaction.atomic():
                return ConsultaDatacreditoSnapshot.objects.create(
                    ambiente=configuracion.environment,
                    servicio=servicio,
                    documento_hash=documento_hash,
                    documento_enmascarado=_enmascarar_documento(solicitud.numero_documento),
                    fingerprint=fingerprint,
                    estado=ConsultaDatacreditoSnapshot.Estado.EN_PROCESO,
                    consultado_en=ahora,
                    vigente_hasta=limite_proceso,
                    autorizacion_referencia=str(autorizacion.pk),
                    creado_por=(
                        solicitado_por
                        if getattr(solicitado_por, 'is_authenticated', False) else None
                    ),
                )
        except IntegrityError:
            existente = procesos.filter(vigente_hasta__gt=ahora).first()
            if existente is None:
                raise
            existente._reserva_existente = True
            return existente


def _finalizar_error(snapshot, *, solicitud, usuario, estado, codigo, tipo):
    ahora = timezone.now()
    snapshot.estado = estado
    snapshot.error_codigo = codigo
    snapshot.error_tipo = tipo
    snapshot.resultado_normalizado = {}
    snapshot.consultado_en = ahora
    snapshot.vigente_hasta = ahora
    snapshot.save(update_fields=[
        'estado', 'error_codigo', 'error_tipo', 'resultado_normalizado',
        'consultado_en', 'vigente_hasta', 'updated_at',
    ])
    _registrar_timeline_snapshot(
        solicitud,
        snapshot,
        TimelinePrestador.TipoEvento.DATACREDITO_ERROR,
        usuario,
    )
    return _resultado_desde_snapshot(snapshot)


def _resultado_desde_snapshot(snapshot, reutilizado=False):
    normalizado = (
        ResultadoNormalizadoDatacreditoPrestador.desde_dict(snapshot.resultado_normalizado)
        if snapshot.resultado_normalizado else None
    )
    return ResultadoConsultaDatacreditoPrestador(
        estado=snapshot.estado,
        reutilizado=reutilizado,
        snapshot_id=str(snapshot.pk),
        servicio=snapshot.servicio,
        consultado_en=snapshot.consultado_en.isoformat() if snapshot.consultado_en else None,
        vigente_hasta=snapshot.vigente_hasta.isoformat() if snapshot.vigente_hasta else None,
        resultado_normalizado=normalizado,
        error_codigo=snapshot.error_codigo or None,
        requiere_revision_manual=True,
        diagnostico_seguro={
            'ambiente': snapshot.ambiente,
            'estado': snapshot.estado,
        } if settings.DEBUG else {},
    )


def _resultado_controlado(*, estado, servicio, error_codigo):
    return ResultadoConsultaDatacreditoPrestador(
        estado=estado,
        servicio=servicio,
        error_codigo=error_codigo,
        requiere_revision_manual=True,
    )


def _registrar_timeline_snapshot(
    solicitud, snapshot, tipo_evento, usuario, reutilizado=False
):
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=tipo_evento,
        titulo=dict(TimelinePrestador.TipoEvento.choices).get(tipo_evento, tipo_evento),
        descripcion='Se registró un resultado técnico controlado de DataCrédito.',
        metadata={
            'snapshot_id': str(snapshot.pk),
            'servicio': snapshot.servicio,
            'estado': snapshot.estado,
            'reutilizado': reutilizado,
            'error_codigo': snapshot.error_codigo or '',
        },
        usuario=usuario,
    )


def _hmac_documento(documento, secreto):
    normalizado = ''.join(
        caracter for caracter in str(documento or '') if caracter.isalnum()
    ).upper()
    return hmac.new(
        str(secreto).encode('utf-8'),
        normalizado.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _enmascarar_documento(documento):
    texto = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum())
    return f"{'*' * max(0, len(texto) - 4)}{texto[-4:]}" if texto else ''

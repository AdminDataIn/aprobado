from dataclasses import dataclass, field

from django.conf import settings

from integrations.datacredito.auth import SERVICIO_DECISOR, SERVICIO_HISTORIAL
from integrations.datacredito.dto import (
    ESTADO_ERROR_TECNICO,
    FUENTE_NO_CONFIGURADO,
    ResultadoDatacreditoNormalizado,
)
from integrations.datacredito.snapshots import (
    buscar_snapshot_datacredito_vigente,
    obtener_o_consultar_datacredito,
    resultado_desde_snapshot,
)
from contractors.services.autorizacion_datacredito import (
    metadata_autorizacion_datacredito,
    obtener_autorizacion_datacredito_vigente,
    puede_reutilizar_snapshot_sin_autorizacion_en_uat,
    snapshot_tiene_autorizacion_valida,
)


MODO_NO_CONSULTAR = 'NO_CONSULTAR'
MODO_REUTILIZAR_SNAPSHOT = 'REUTILIZAR_SNAPSHOT'
MODO_CONSULTAR_SI_NO_EXISTE = 'CONSULTAR_SI_NO_EXISTE'
MODO_FORZAR_CONSULTA = 'FORZAR_CONSULTA'

MODOS_DATACREDITO = {
    MODO_NO_CONSULTAR,
    MODO_REUTILIZAR_SNAPSHOT,
    MODO_CONSULTAR_SI_NO_EXISTE,
    MODO_FORZAR_CONSULTA,
}


@dataclass(frozen=True)
class ResultadoDatacreditoSolicitudPrestador:
    resultado_decisor: object | None = None
    resultado_historial: object | None = None
    snapshot_decisor: object | None = None
    snapshot_historial: object | None = None
    reutilizado_decisor: bool = False
    reutilizado_historial: bool = False
    consultado_decisor: bool = False
    consultado_historial: bool = False
    autorizacion_datacredito: object | None = None
    autorizacion_estado: str = ''
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    errores_seguros: tuple[str, ...] = field(default_factory=tuple)
    modo: str = MODO_NO_CONSULTAR
    justificacion: str = ''

    @property
    def tiene_resultados_para_predecision(self):
        return self.resultado_decisor is not None and self.resultado_historial is not None


def resolver_datacredito_para_solicitud_prestador(
    *,
    solicitud,
    modo,
    usuario=None,
    request=None,
    justificacion=None,
):
    modo = normalizar_modo_datacredito(modo)
    if modo == MODO_NO_CONSULTAR:
        return ResultadoDatacreditoSolicitudPrestador(modo=modo)

    if modo == MODO_FORZAR_CONSULTA and not str(justificacion or '').strip():
        return ResultadoDatacreditoSolicitudPrestador(
            modo=modo,
            errores_seguros=('justificacion_consulta_forzada_requerida',),
            justificacion='',
        )

    estado_autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)

    decisor = _resolver_servicio(
        solicitud=solicitud,
        servicio=SERVICIO_DECISOR,
        modo=modo,
        usuario=usuario,
        request=request,
        estado_autorizacion=estado_autorizacion,
    )
    historial = _resolver_servicio(
        solicitud=solicitud,
        servicio=SERVICIO_HISTORIAL,
        modo=modo,
        usuario=usuario,
        request=request,
        estado_autorizacion=estado_autorizacion,
    )
    return ResultadoDatacreditoSolicitudPrestador(
        resultado_decisor=decisor.resultado_normalizado,
        resultado_historial=historial.resultado_normalizado,
        snapshot_decisor=decisor.snapshot,
        snapshot_historial=historial.snapshot,
        reutilizado_decisor=decisor.reutilizado,
        reutilizado_historial=historial.reutilizado,
        consultado_decisor=decisor.consultado_proveedor,
        consultado_historial=historial.consultado_proveedor,
        autorizacion_datacredito=estado_autorizacion.autorizacion,
        autorizacion_estado=estado_autorizacion.estado,
        advertencias=tuple(decisor.advertencias + historial.advertencias),
        errores_seguros=tuple(decisor.errores + historial.errores),
        modo=modo,
        justificacion=str(justificacion or '').strip() if modo == MODO_FORZAR_CONSULTA else '',
    )


def normalizar_modo_datacredito(modo):
    modo = str(modo or MODO_NO_CONSULTAR).strip().upper()
    if modo not in MODOS_DATACREDITO:
        return MODO_NO_CONSULTAR
    return modo


def solicitud_tiene_autorizacion_datacredito(solicitud):
    return obtener_autorizacion_datacredito_vigente(solicitud).vigente


@dataclass(frozen=True)
class _ResultadoServicioDatacredito:
    resultado_normalizado: object | None = None
    snapshot: object | None = None
    reutilizado: bool = False
    consultado_proveedor: bool = False
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    errores: tuple[str, ...] = field(default_factory=tuple)


def _resolver_servicio(*, solicitud, servicio, modo, usuario, request, estado_autorizacion):
    snapshot = buscar_snapshot_datacredito_vigente(
        servicio=servicio,
        tipo_documento=solicitud.document_type,
        numero_documento=solicitud.document_number,
        apellido=solicitud.last_name,
    )
    if snapshot and modo != MODO_FORZAR_CONSULTA:
        if snapshot_tiene_autorizacion_valida(snapshot, estado_autorizacion.autorizacion):
            return _ResultadoServicioDatacredito(
                resultado_normalizado=resultado_desde_snapshot(snapshot),
                snapshot=snapshot,
                reutilizado=True,
                advertencias=(f'{servicio}:snapshot_reutilizado',),
            )
        if puede_reutilizar_snapshot_sin_autorizacion_en_uat():
            return _ResultadoServicioDatacredito(
                resultado_normalizado=resultado_desde_snapshot(snapshot),
                snapshot=snapshot,
                reutilizado=True,
                advertencias=(f'{servicio}:snapshot_legacy_sin_autorizacion_reutilizado_uat',),
            )
        _registrar_bloqueo_sin_autorizacion(
            solicitud=solicitud,
            servicio=servicio,
            estado_autorizacion=estado_autorizacion,
            usuario=usuario,
            request=request,
        )
        return _faltante(servicio, 'snapshot_sin_autorizacion_datacredito')

    if modo == MODO_REUTILIZAR_SNAPSHOT:
        return _faltante(servicio, 'snapshot_no_disponible')

    if not estado_autorizacion.vigente:
        _registrar_bloqueo_sin_autorizacion(
            solicitud=solicitud,
            servicio=servicio,
            estado_autorizacion=estado_autorizacion,
            usuario=usuario,
            request=request,
        )
        return _faltante(servicio, estado_autorizacion.razon or 'autorizacion_datacredito_pendiente')

    if not getattr(settings, 'DATACREDITO_REAL_ENABLED', False):
        return _faltante(servicio, 'datacredito_real_deshabilitado')

    resultado = obtener_o_consultar_datacredito(
        servicio=servicio,
        tipo_documento=solicitud.document_type,
        numero_documento=solicitud.document_number,
        apellido=solicitud.last_name,
        usuario=usuario,
        request=request,
        forzar_consulta=modo == MODO_FORZAR_CONSULTA,
        autorizacion_datacredito=estado_autorizacion.autorizacion,
    )
    return _ResultadoServicioDatacredito(
        resultado_normalizado=resultado.resultado_normalizado,
        snapshot=resultado.snapshot,
        reutilizado=resultado.reutilizado,
        consultado_proveedor=resultado.consultado_proveedor,
        advertencias=(f'{servicio}:consulta_realizada',) if resultado.consultado_proveedor else (),
    )


def _faltante(servicio, razon):
    return _ResultadoServicioDatacredito(
        resultado_normalizado=_resultado_no_disponible(servicio, razon),
        errores=(f'{servicio}:{razon}',),
    )


def _resultado_no_disponible(servicio, error_tipo):
    return ResultadoDatacreditoNormalizado(
        disponible=False,
        fuente=FUENTE_NO_CONFIGURADO,
        servicio=servicio,
        estado=ESTADO_ERROR_TECNICO,
        requiere_revision_manual=True,
        error_tipo=error_tipo,
        metadata_segura={
            'servicio': servicio,
            'error_tipo': error_tipo,
            'resultado_operativo': 'sin_snapshot_o_consulta',
        },
    )


def construir_metadata_timeline_datacredito(resultado_datacredito):
    return {
        'modo': resultado_datacredito.modo,
        'autorizacion': {
            'estado': resultado_datacredito.autorizacion_estado,
            **metadata_autorizacion_datacredito(resultado_datacredito.autorizacion_datacredito),
        },
        'decisor': _metadata_servicio(
            servicio=SERVICIO_DECISOR,
            snapshot=resultado_datacredito.snapshot_decisor,
            reutilizado=resultado_datacredito.reutilizado_decisor,
            consultado=resultado_datacredito.consultado_decisor,
        ),
        'historial': _metadata_servicio(
            servicio=SERVICIO_HISTORIAL,
            snapshot=resultado_datacredito.snapshot_historial,
            reutilizado=resultado_datacredito.reutilizado_historial,
            consultado=resultado_datacredito.consultado_historial,
        ),
        'advertencias': list(resultado_datacredito.advertencias),
        'errores_seguros': list(resultado_datacredito.errores_seguros),
    }


def _registrar_bloqueo_sin_autorizacion(*, solicitud, servicio, estado_autorizacion, usuario, request):
    from contractors.services.timeline import registrar_evento_timeline_prestador

    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION',
        titulo='Consulta DataCredito bloqueada sin autorizacion',
        descripcion='Se bloqueo consulta real por falta de autorizacion especifica vigente.',
        estado_resultante=estado_autorizacion.estado,
        metadata={
            'servicio': servicio,
            'estado': estado_autorizacion.estado,
            'razon': estado_autorizacion.razon,
        },
        usuario=usuario,
        request=request,
    )


def _metadata_servicio(*, servicio, snapshot, reutilizado, consultado):
    return {
        'servicio': servicio,
        'snapshot_id': str(snapshot.id) if snapshot else None,
        'reutilizado': bool(reutilizado),
        'consultado_proveedor': bool(consultado),
        'estado_normalizado': snapshot.estado_normalizado if snapshot else None,
        'http_status': snapshot.http_status if snapshot else None,
        'codigo_funcional': snapshot.codigo_funcional if snapshot else None,
    }

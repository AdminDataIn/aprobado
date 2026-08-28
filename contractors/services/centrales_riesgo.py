from contractors.datacredito.dto import ResultadoCentralesPrestador
from contractors.models import ConfiguracionScorePrestador
from contractors.services.datacredito_evaluacion import (
    REUTILIZAR_SI_VIGENTE,
    obtener_evaluacion_datacredito_prestador,
)
from integrations.models import ConsultaDatacreditoSnapshot


ESTADO_COMPLETA = 'COMPLETA'
ESTADO_PARCIAL = 'PARCIAL'
ESTADO_REVISION_MANUAL = 'REQUIERE_REVISION_MANUAL'
ESTADO_NO_EVALUABLE = 'NO_EVALUABLE'


def obtener_evaluacion_centrales_prestador(
    solicitud,
    *,
    politica,
    modo=REUTILIZAR_SI_VIGENTE,
    solicitado_por=None,
    justificacion=None,
):
    """Coordina fuentes requeridas sin duplicar HTTP ni persistir respuestas crudas."""
    decisor = _consultar_si_aplica(
        solicitud,
        servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
        aplica=bool(politica.requiere_midecisor or (politica.peso_midecisor or 0) > 0),
        modo=modo,
        solicitado_por=solicitado_por,
        justificacion=justificacion,
        vigencia_dias=politica.vigencia_midecisor_dias,
    )
    historial = _consultar_si_aplica(
        solicitud,
        servicio=ConsultaDatacreditoSnapshot.Servicio.HISTORIAL,
        aplica=bool(politica.requiere_hdcplus or (politica.peso_hdcplus or 0) > 0),
        modo=modo,
        solicitado_por=solicitado_por,
        justificacion=justificacion,
        vigencia_dias=politica.vigencia_hdcplus_dias,
    )

    evaluaciones = []
    if decisor is not None:
        evaluaciones.append((
            'decisor',
            decisor,
            politica.requiere_midecisor,
            politica.permite_evaluar_sin_midecisor,
        ))
    if historial is not None:
        evaluaciones.append((
            'historial',
            historial,
            politica.requiere_hdcplus,
            politica.permite_evaluar_sin_hdc,
        ))

    acciones = []
    errores = []
    alertas = []
    for nombre, resultado, requerido, permite_sin_fuente in evaluaciones:
        accion = _accion_resultado(
            resultado,
            politica=politica,
            requerido=requerido,
            permite_sin_fuente=permite_sin_fuente,
        )
        acciones.append(accion)
        if resultado.estado != ConsultaDatacreditoSnapshot.Estado.EXITOSO:
            alertas.append(f'{nombre}:{resultado.estado.lower()}')
        if resultado.error_codigo:
            errores.append(f'{nombre}:{str(resultado.error_codigo)[:80]}')

    if ConfiguracionScorePrestador.AccionDisponibilidadCentrales.NO_EVALUABLE in acciones:
        estado_global = ESTADO_NO_EVALUABLE
    elif ConfiguracionScorePrestador.AccionDisponibilidadCentrales.REVISION_MANUAL in acciones:
        estado_global = ESTADO_REVISION_MANUAL
    elif ConfiguracionScorePrestador.AccionDisponibilidadCentrales.PERMITIR_PARCIAL in acciones:
        estado_global = ESTADO_PARCIAL
    else:
        estado_global = ESTADO_COMPLETA

    contradiccion = _detectar_senales_contradictorias(decisor, historial)
    if contradiccion:
        alertas.append(contradiccion)

    completa = bool(evaluaciones) and all(
        resultado.estado == ConsultaDatacreditoSnapshot.Estado.EXITOSO
        for _nombre, resultado, _requerido, _permite in evaluaciones
    )
    return ResultadoCentralesPrestador(
        decisor=decisor,
        historial=historial,
        estado_global=estado_global,
        completa=completa,
        requiere_revision_manual=estado_global != ESTADO_COMPLETA,
        errores=tuple(dict.fromkeys(errores)),
        alertas=tuple(dict.fromkeys(alertas)),
        snapshot_ids={
            nombre: resultado.snapshot_id
            for nombre, resultado in (('decisor', decisor), ('historial', historial))
            if resultado is not None and resultado.snapshot_id
        },
    )


def _consultar_si_aplica(
    solicitud, *, servicio, aplica, modo, solicitado_por, justificacion, vigencia_dias
):
    if not aplica:
        return None
    return obtener_evaluacion_datacredito_prestador(
        solicitud,
        modo=modo,
        solicitado_por=solicitado_por,
        justificacion=justificacion,
        servicio=servicio,
        vigencia_dias=vigencia_dias,
    )


def _accion_resultado(resultado, *, politica, requerido, permite_sin_fuente):
    if resultado.estado == ConsultaDatacreditoSnapshot.Estado.EXITOSO:
        return None
    if resultado.estado == ConsultaDatacreditoSnapshot.Estado.SIN_INFORMACION:
        accion = politica.accion_sin_informacion_centrales
    elif resultado.estado == ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO:
        accion = politica.accion_error_transitorio_centrales
    elif resultado.estado == ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE:
        accion = politica.accion_error_permanente_centrales
    else:
        accion = ConfiguracionScorePrestador.AccionDisponibilidadCentrales.NO_EVALUABLE

    if (
        not requerido
        and permite_sin_fuente
        and accion == ConfiguracionScorePrestador.AccionDisponibilidadCentrales.NO_EVALUABLE
    ):
        return ConfiguracionScorePrestador.AccionDisponibilidadCentrales.PERMITIR_PARCIAL
    return accion


def _detectar_senales_contradictorias(decisor, historial):
    if decisor is None or historial is None:
        return ''
    normalizado_decisor = decisor.resultado_normalizado
    normalizado_historial = historial.resultado_normalizado
    if normalizado_decisor is None or normalizado_historial is None:
        return ''
    mora_decisor = normalizado_decisor.mora_actual
    mora_historial = normalizado_historial.mora_actual
    if mora_decisor is not None and mora_historial is not None and mora_decisor != mora_historial:
        return 'centrales:senales_mora_contradictorias'
    return ''

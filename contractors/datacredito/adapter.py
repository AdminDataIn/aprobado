from django.utils import timezone

from contractors.datacredito.dto import (
    ResultadoNormalizadoDatacreditoPrestador,
    ResultadoProveedorDatacreditoPrestador,
)
from integrations.datacredito.decisor_client import consultar_midecisor_persona_natural
from integrations.datacredito.dto import (
    ESTADO_EXITOSA_CON_INFORMACION,
    ESTADO_EXITOSA_SIN_INFORMACION,
    ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    EntradaHistorialCredito,
    EntradaMiDecisor,
)
from integrations.datacredito.historial_client import consultar_historial_credito
from integrations.datacredito.normalizadores import (
    normalizar_historial_credito,
    normalizar_midecisor_pn,
)
from integrations.models import ConsultaDatacreditoSnapshot


def consultar_proveedor_datacredito_prestador(solicitud, *, servicio):
    apellido = str(solicitud.apellidos or '').strip().split()[0].upper()
    if servicio == ConsultaDatacreditoSnapshot.Servicio.DECISOR:
        respuesta = consultar_midecisor_persona_natural(
            EntradaMiDecisor(
                tipo_identificacion=solicitud.tipo_documento,
                numero_identificacion=solicitud.numero_documento,
                apellido_razon_social=apellido,
            )
        )
        normalizado = normalizar_midecisor_pn(respuesta.raw_sanitizado)
        codigo_funcional = respuesta.codigo_funcional or respuesta.response_code or ''
    elif servicio == ConsultaDatacreditoSnapshot.Servicio.HISTORIAL:
        respuesta = consultar_historial_credito(
            EntradaHistorialCredito(
                tipo_identificacion=solicitud.tipo_documento,
                numero_identificacion=solicitud.numero_documento,
                apellido=apellido,
            )
        )
        normalizado = normalizar_historial_credito(respuesta.raw_sanitizado)
        codigo_funcional = respuesta.response_code or ''
    else:
        raise ValueError('servicio_datacredito_no_soportado')

    if normalizado.estado == ESTADO_EXITOSA_CON_INFORMACION and normalizado.disponible:
        estado = ConsultaDatacreditoSnapshot.Estado.EXITOSO
    elif normalizado.estado in {
        ESTADO_EXITOSA_SIN_INFORMACION,
        ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    }:
        estado = ConsultaDatacreditoSnapshot.Estado.SIN_INFORMACION
    else:
        estado = ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE
    resultado = _proyectar_resultado_allowlist(normalizado, servicio=servicio)
    return ResultadoProveedorDatacreditoPrestador(
        estado_snapshot=estado,
        resultado_normalizado=resultado,
        codigo_http=respuesta.status_code,
        codigo_funcional=str(codigo_funcional)[:60],
    )


def _proyectar_resultado_allowlist(normalizado, *, servicio):
    resumen_hdc = (
        (normalizado.metadata_segura or {}).get('hdc_resumen') or {}
        if servicio == ConsultaDatacreditoSnapshot.Servicio.HISTORIAL
        else {}
    )
    vigentes = _entero_o_none(normalizado.creditos_vigentes)
    cerradas = _entero_o_none(normalizado.creditos_cerrados)
    if resumen_hdc:
        vigentes = _entero_o_none(resumen_hdc.get('liabilities_vigentes'))
        total_hdc = _entero_o_none(resumen_hdc.get('total_liabilities'))
        cerradas = (
            max(total_hdc - (vigentes or 0), 0)
            if total_hdc is not None else None
        )
    total = None if vigentes is None and cerradas is None else (vigentes or 0) + (cerradas or 0)
    score = normalizado.score_midecisor
    if score is None:
        score = normalizado.score
    return ResultadoNormalizadoDatacreditoPrestador(
        score_externo=_entero_o_none(score),
        rango_score=str(normalizado.nivel_riesgo or '') or None,
        total_obligaciones=total,
        saldo_total=_texto_decimal(normalizado.saldo_actual),
        cuota_mensual_total=_texto_decimal(normalizado.valor_cuota_total),
        obligaciones_vigentes=vigentes,
        obligaciones_cerradas=cerradas,
        obligaciones_en_mora=_entero_o_none(
            resumen_hdc.get('liabilities_en_mora')
        ),
        mora_maxima_dias=_entero_o_none(resumen_hdc.get('max_mora_dias')),
        consultas_recientes=_entero_o_none(
            resumen_hdc.get('huellas_ultimos_6_meses')
        ),
        saldo_mora=_texto_decimal(normalizado.saldo_mora),
        mora_actual=normalizado.mora_actual,
        mora_severa=normalizado.mora_severa,
        productos_activos=vigentes,
        creditos_activos=vigentes,
        comportamiento_pago=(
            'MORA_SEVERA' if normalizado.mora_severa
            else 'MORA_ACTUAL' if normalizado.mora_actual
            else 'SIN_MORA_REPORTADA' if normalizado.mora_actual is False
            else 'NO_DETERMINABLE'
        ),
        alertas=tuple(str(alerta)[:120] for alerta in normalizado.alertas_resumen),
        servicio_fuente=servicio,
        fecha_consulta=timezone.now().isoformat(),
    )


def _entero_o_none(valor):
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _texto_decimal(valor):
    return str(valor) if valor is not None else None

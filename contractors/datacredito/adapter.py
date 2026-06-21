from django.conf import settings
from django.utils import timezone

from integrations.datacredito import decisor_client, historial_client
from integrations.datacredito.dto import EntradaHistorialCredito, EntradaMiDecisor
from integrations.datacredito.exceptions import (
    DatacreditoConfigError,
    DatacreditoProviderDisabled,
    DatacreditoProviderError,
)
from integrations.datacredito.normalizadores import (
    detectar_mora_severa_desde_vector,
    normalizar_historial_credito,
    normalizar_midecisor_pn,
)

from contractors.datacredito.dto import (
    AlertaDatacreditoPrestador,
    FUENTE_DATACREDITO_REAL,
    FUENTE_NO_CONFIGURADO,
    NIVEL_RIESGO_NO_DISPONIBLE,
    EntradaConsultaDatacreditoPrestador,
    ResultadoDatacreditoPrestador,
)
from contractors.datacredito.mock import ESCENARIO_BUENO, consultar_datacredito_mock
from contractors.datacredito.normalizador import construir_metadata_segura
from contractors.datacredito.normalizador import enmascarar_documento


PROVEEDOR_MOCK = 'mock'
PROVEEDOR_REAL = 'real'
PROVEEDOR_NO_CONFIGURADO = 'no_configurado'


def consultar_datacredito_prestador(
    entrada: EntradaConsultaDatacreditoPrestador,
    *,
    resultado_decisor_resuelto=None,
    resultado_historial_resuelto=None,
):
    if resultado_decisor_resuelto is not None and resultado_historial_resuelto is not None:
        return _consolidar_resultado_real(
            entrada=entrada,
            decisor=_extraer_resultado_normalizado(resultado_decisor_resuelto),
            historial=_extraer_resultado_normalizado(resultado_historial_resuelto),
            raw_decisor={},
            raw_historial={},
            metadata_snapshot=_metadata_snapshot_resuelta(
                decisor=resultado_decisor_resuelto,
                historial=resultado_historial_resuelto,
            ),
        )

    if not getattr(settings, 'CONTRACTORS_DATACREDITO_ENABLED', False):
        return _resultado_no_configurado(entrada)

    proveedor = str(getattr(settings, 'CONTRACTORS_DATACREDITO_PROVIDER', PROVEEDOR_MOCK) or '').lower()
    if proveedor == PROVEEDOR_MOCK:
        escenario = getattr(settings, 'CONTRACTORS_DATACREDITO_MOCK_SCENARIO', ESCENARIO_BUENO)
        return consultar_datacredito_mock(entrada, escenario=escenario)

    if proveedor == PROVEEDOR_REAL:
        return _consultar_datacredito_real(entrada)

    if proveedor == PROVEEDOR_NO_CONFIGURADO:
        return _resultado_no_configurado(entrada)

    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_NO_CONFIGURADO,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo='proveedor_datacredito_no_valido',
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_NO_CONFIGURADO, proveedor=proveedor),
    )


def _consultar_datacredito_real(entrada):
    metadata = construir_metadata_segura(
        entrada,
        fuente=FUENTE_DATACREDITO_REAL,
        proveedor=PROVEEDOR_REAL,
    )
    if not getattr(settings, 'DATACREDITO_REAL_ENABLED', False):
        return ResultadoDatacreditoPrestador(
            disponible=False,
            fuente=FUENTE_NO_CONFIGURADO,
            nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
            requiere_revision_manual=True,
            error_tipo='datacredito_real_deshabilitado',
            metadata_segura=metadata,
        )

    try:
        raw_decisor = decisor_client.consultar_midecisor_persona_natural(_entrada_midecisor(entrada))
        raw_historial = historial_client.consultar_historial_credito(_entrada_historial(entrada))
    except DatacreditoProviderDisabled:
        return _resultado_error_real(entrada, 'datacredito_real_deshabilitado')
    except DatacreditoConfigError:
        return _resultado_error_real(entrada, 'credenciales_datacredito_incompletas')
    except DatacreditoProviderError:
        return _resultado_error_real(entrada, 'error_proveedor_datacredito')

    decisor = normalizar_midecisor_pn(raw_decisor)
    historial = normalizar_historial_credito(raw_historial)
    raw_decisor_seguro = _raw_seguro(raw_decisor)
    raw_historial_seguro = _raw_seguro(raw_historial)
    return _consolidar_resultado_real(
        entrada=entrada,
        decisor=decisor,
        historial=historial,
        raw_decisor=raw_decisor_seguro,
        raw_historial=raw_historial_seguro,
    )


def _resultado_no_configurado(entrada):
    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_NO_CONFIGURADO,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo='datacredito_no_configurado',
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_NO_CONFIGURADO),
    )


def _resultado_error_real(entrada, error_tipo):
    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_DATACREDITO_REAL,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo=error_tipo,
        metadata_segura=construir_metadata_segura(
            entrada,
            fuente=FUENTE_DATACREDITO_REAL,
            proveedor=PROVEEDOR_REAL,
        ),
    )


def _entrada_midecisor(entrada):
    return EntradaMiDecisor(
        tipo_identificacion=entrada.tipo_documento,
        numero_identificacion=entrada.numero_documento,
        apellido_razon_social=getattr(entrada, 'apellido_razon_social', '') or '',
    )


def _entrada_historial(entrada):
    import uuid

    return EntradaHistorialCredito(
        tipo_identificacion=entrada.tipo_documento,
        numero_identificacion=entrada.numero_documento,
        apellido=getattr(entrada, 'apellido_razon_social', '') or '',
        request_uuid=str(uuid.uuid4()),
        fecha_hora=timezone.now().isoformat(),
        user_ip_address=getattr(entrada, 'ip_address', None),
    )


def _consolidar_resultado_real(*, entrada, decisor, historial, raw_decisor, raw_historial, metadata_snapshot=None):
    score = decisor.score_midecisor if decisor.score_midecisor is not None else decisor.score
    score_normalizado = decisor.score_normalizado_0_1000
    saldo_mora = decisor.saldo_mora or _buscar_entero(raw_decisor, 'saldoMora', 'saldo_mora', 'saldoEnMora')
    mora_vector_decisor = detectar_mora_severa_desde_vector(
        _buscar_valor(raw_decisor, 'vectorComportamiento', 'comportamientoPago', 'vector')
    )
    mora_severa = bool(decisor.mora_severa or historial.mora_severa or mora_vector_decisor)
    mora_actual = bool(decisor.mora_actual or historial.mora_actual or (saldo_mora and saldo_mora > 0))
    obligaciones_en_mora = _buscar_entero(raw_historial, 'obligacionesEnMora', 'obligaciones_en_mora')
    obligaciones_abiertas = _buscar_entero(raw_historial, 'obligacionesAbiertas', 'obligaciones_abiertas')
    alertas_resumen = tuple(
        dict.fromkeys(
            list(decisor.alertas_resumen)
            + list(historial.alertas_resumen)
            + (['mora_severa_detectada'] if mora_severa else [])
            + (['saldo_mora_reportado'] if saldo_mora and saldo_mora > 0 else [])
        )
    )
    alertas = tuple(
        AlertaDatacreditoPrestador(codigo=alerta, nivel='ALTO' if 'mora' in alerta else 'MEDIO', mensaje=alerta)
        for alerta in alertas_resumen
    )

    return ResultadoDatacreditoPrestador(
        disponible=bool(decisor.disponible or historial.disponible),
        fuente=FUENTE_DATACREDITO_REAL,
        score_externo=score,
        score_normalizado_0_1000=score_normalizado,
        mora_severa=mora_severa,
        mora_actual=mora_actual,
        obligaciones_abiertas=obligaciones_abiertas,
        obligaciones_en_mora=obligaciones_en_mora,
        nivel_riesgo=decisor.nivel_riesgo if decisor.nivel_riesgo != NIVEL_RIESGO_NO_DISPONIBLE else historial.nivel_riesgo,
        saldo_mora=saldo_mora,
        valor_cuota_total=_buscar_entero(raw_decisor, 'valorCuotaTotal', 'valor_cuota_total'),
        porcentaje_cuota_vs_ingreso=_buscar_valor(raw_decisor, 'porcentajeCuotaVsIngreso', 'porcentaje_cuota_vs_ingreso'),
        ingreso_estimado=_buscar_entero(raw_decisor, 'ingresoEstimado', 'ingreso_estimado'),
        viabilidad=decisor.viable,
        rating_recaudos=_buscar_valor(raw_decisor, 'ratingRecaudos', 'rating_recaudos'),
        monto_sugerido_datacredito=decisor.monto_sugerido,
        alertas_resumen=alertas_resumen,
        alertas=alertas,
        requiere_revision_manual=score_normalizado is None or mora_severa or not bool(decisor.disponible or historial.disponible),
        metadata_segura={
            **construir_metadata_segura(entrada, fuente=FUENTE_DATACREDITO_REAL, proveedor=PROVEEDOR_REAL),
            **(metadata_snapshot or {}),
            'documento_enmascarado': enmascarar_documento(entrada.numero_documento),
            'score_fuente': 'decisor' if score is not None else None,
            'scores_hdc_detectados': len(getattr(historial, 'scores_hdc', ()) or ()),
            'decisor_response_code': decisor.response_code,
            'historial_response_code': historial.response_code,
        },
    )


def _raw_seguro(raw):
    if hasattr(raw, 'raw_sanitizado'):
        return raw.raw_sanitizado or {}
    if isinstance(raw, dict):
        return raw
    return {}


def _extraer_resultado_normalizado(resultado_resuelto):
    return getattr(resultado_resuelto, 'resultado_normalizado', resultado_resuelto)


def _metadata_snapshot_resuelta(*, decisor, historial):
    return {
        'snapshot_decisor_id': getattr(decisor, 'snapshot_id', None),
        'snapshot_historial_id': getattr(historial, 'snapshot_id', None),
        'reutilizado_decisor': bool(getattr(decisor, 'reutilizado', False)),
        'reutilizado_historial': bool(getattr(historial, 'reutilizado', False)),
    }


def _buscar_valor(datos, *claves):
    if not isinstance(datos, dict):
        return None
    for clave in claves:
        if clave in datos:
            return datos[clave]
    for valor in datos.values():
        if isinstance(valor, dict):
            encontrado = _buscar_valor(valor, *claves)
            if encontrado is not None:
                return encontrado
        elif isinstance(valor, list):
            for item in valor:
                encontrado = _buscar_valor(item, *claves)
                if encontrado is not None:
                    return encontrado
    return None


def _buscar_entero(datos, *claves):
    valor = _buscar_valor(datos, *claves)
    if valor is None:
        return None
    try:
        return int(float(str(valor).replace(',', '').strip()))
    except (TypeError, ValueError):
        return None

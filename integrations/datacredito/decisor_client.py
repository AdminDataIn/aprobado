import requests

from integrations.datacredito.auth import SERVICIO_DECISOR, obtener_token_cacheado, validar_consumo_real_habilitado
from integrations.datacredito.dto import EntradaMiDecisor, ResultadoMiDecisorRawSeguro
from integrations.datacredito.exceptions import DatacreditoProviderError, DatacreditoTimeoutError
from integrations.datacredito.settings import obtener_configuracion_datacredito


def consultar_midecisor_persona_natural(entrada: EntradaMiDecisor, session=None):
    return _consultar_midecisor(entrada, tipo_persona='persona_natural', session=session)


def consultar_midecisor_persona_juridica(entrada: EntradaMiDecisor, session=None):
    return _consultar_midecisor(entrada, tipo_persona='persona_juridica', session=session)


def _consultar_midecisor(entrada, tipo_persona, session=None):
    configuracion = validar_consumo_real_habilitado(obtener_configuracion_datacredito())
    token = obtener_token_cacheado(servicio=SERVICIO_DECISOR, session=session)
    cliente_http = session or requests
    headers = {
        'Authorization': token.authorization_header,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    payload = entrada.como_payload()

    try:
        respuesta = cliente_http.post(
            configuracion.midecisor_url,
            json=payload,
            headers=headers,
            timeout=configuracion.timeout_seconds,
        )
    except requests.exceptions.Timeout as exc:
        raise DatacreditoTimeoutError(
            'Timeout consultando MiDecisor DataCredito.',
            servicio='decisor',
            etapa='HTTP',
            error_tipo='TIMEOUT',
            causa_clase=exc.__class__.__name__,
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise DatacreditoProviderError(
            'Error de red consultando MiDecisor DataCredito.',
            servicio='decisor',
            etapa='HTTP',
            error_tipo='ERROR_HTTP',
            causa_clase=exc.__class__.__name__,
        ) from exc

    if respuesta.status_code >= 400:
        raise DatacreditoProviderError(
            f'Error MiDecisor DataCredito status={respuesta.status_code}.',
            servicio='decisor',
            etapa='HTTP',
            http_status=respuesta.status_code,
            error_tipo='HTTP_ERROR',
        )

    try:
        cuerpo = respuesta.json()
    except ValueError as exc:
        raise DatacreditoProviderError(
            'Respuesta MiDecisor DataCredito no es JSON valido.',
            servicio='decisor',
            etapa='PARSEO_JSON',
            http_status=respuesta.status_code,
            error_tipo='ERROR_PARSEO_JSON',
            causa_clase=exc.__class__.__name__,
        ) from exc
    response_code = _buscar_response_code(cuerpo)
    return ResultadoMiDecisorRawSeguro(
        status_code=respuesta.status_code,
        response_code=response_code,
        codigo_funcional=_codigo_funcional_midecisor(cuerpo),
        raw_sanitizado=_sanitizar_raw(cuerpo),
        metadata_segura={
            'tipo_persona': tipo_persona,
            'endpoint': 'midecisor',
            'status_code': respuesta.status_code,
            'codigo_funcional': _codigo_funcional_midecisor(cuerpo),
            'documento_enmascarado': _enmascarar_documento(entrada.numero_identificacion),
        },
    )


def _sanitizar_raw(cuerpo):
    if not isinstance(cuerpo, dict):
        return {}
    prohibidas = {
        'numeroIdentificacion',
        'identificacion',
        'documento',
        'numeroIdDigitado',
        'apellidoDigitado',
        'access_token',
        'client_secret',
        'password',
    }
    return _sanitizar_valor(cuerpo, prohibidas)


def _sanitizar_valor(valor, prohibidas):
    if isinstance(valor, dict):
        return {
            clave: _sanitizar_valor(subvalor, prohibidas)
            for clave, subvalor in valor.items()
            if clave not in prohibidas
        }
    if isinstance(valor, list):
        return [_sanitizar_valor(item, prohibidas) for item in valor]
    return valor


def _buscar_response_code(cuerpo):
    if not isinstance(cuerpo, dict):
        return None
    codigo_hc = _codigo_midecisor(cuerpo, 'HC')
    if codigo_hc is not None:
        return str(codigo_hc)
    for clave in ('responseCode', 'response_code', 'codigoRespuesta'):
        if clave in cuerpo:
            return str(cuerpo[clave])
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            encontrado = _buscar_response_code(valor)
            if encontrado is not None:
                return encontrado
    return None


def _codigo_funcional_midecisor(cuerpo):
    codigo_hc = _codigo_midecisor(cuerpo, 'HC')
    codigo_tx = _codigo_midecisor(cuerpo, 'TX')
    if codigo_hc and codigo_tx:
        return f'HC{codigo_hc}_TX{codigo_tx}'
    if codigo_hc:
        return f'HC{codigo_hc}'
    return None


def _codigo_midecisor(cuerpo, clave_buscada):
    if not isinstance(cuerpo, dict):
        return None
    codigos = (
        cuerpo.get('content', {})
        .get('infoTransaccion', {})
        .get('codigosRespuesta', [])
    )
    if isinstance(codigos, list):
        for item in codigos:
            if isinstance(item, dict) and str(item.get('clave', '')).upper() == clave_buscada:
                return item.get('valor')
    return None


def _enmascarar_documento(documento):
    texto = str(documento or '')
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"

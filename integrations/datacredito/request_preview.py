from integrations.datacredito.auth import SERVICIO_DECISOR, SERVICIO_HISTORIAL
from integrations.datacredito.dto import EntradaHistorialCredito, EntradaMiDecisor, enmascarar_valor
from integrations.datacredito.exceptions import DatacreditoConfigError
from integrations.datacredito.historial_client import _construir_payload_historial
from integrations.datacredito.settings import obtener_configuracion_datacredito, obtener_credenciales_oauth


CLAVES_SECRETAS = {
    'authorization',
    'access_token',
    'refresh_token',
    'client_secret',
    'password',
    'user',
    'username',
}
CLAVES_DOCUMENTO = {
    'numeroidentificacion',
    'personidnumber',
    'numeroiddigitado',
}
CLAVES_TEXTO_PERSONAL = {
    'apellidorazonsocial',
    'personlastname',
    'apellidodigitado',
}


def construir_request_sanitizado_datacredito(*, servicio, tipo_documento, numero_documento, apellido):
    servicio = str(servicio or '').strip().lower()
    if servicio not in {SERVICIO_DECISOR, SERVICIO_HISTORIAL}:
        raise DatacreditoConfigError('Servicio DataCredito invalido.')

    configuracion = obtener_configuracion_datacredito()
    credenciales_token = obtener_credenciales_oauth(servicio, configuracion=configuracion)
    return {
        'servicio': servicio,
        'token_request': _request_token_sanitizado(configuracion, credenciales_token),
        'service_request': _request_servicio_sanitizado(
            servicio=servicio,
            configuracion=configuracion,
            tipo_documento=tipo_documento,
            numero_documento=numero_documento,
            apellido=apellido,
        ),
    }


def _request_token_sanitizado(configuracion, credenciales):
    headers = {
        'client_id': credenciales.client_id,
        'client_secret': credenciales.client_secret,
        'Content-Type': 'application/json',
    }
    body = {
        'username': credenciales.username,
        'password': credenciales.password,
    }
    return {
        'url': configuracion.token_url,
        'method': 'POST',
        'headers_presentes': {clave: bool(valor) for clave, valor in headers.items()},
        'headers': _sanitizar_valor(headers),
        'body_keys': list(body.keys()),
        'body_preview': _sanitizar_valor(body),
        'usa_json': True,
        'usa_form_urlencoded': False,
        'incluye_grant_type': False,
    }


def _request_servicio_sanitizado(*, servicio, configuracion, tipo_documento, numero_documento, apellido):
    if servicio == SERVICIO_HISTORIAL:
        entrada = EntradaHistorialCredito(
            tipo_identificacion=tipo_documento,
            numero_identificacion=numero_documento,
            apellido=apellido,
            request_uuid='00000000-0000-4000-8000-000000000000',
        )
        headers = {
            'Authorization': 'Bearer token-diagnostico',
            'Content-Type': 'application/json',
            'serverIpAddress': configuracion.credenciales_servicio_historial.server_ip_address,
            'ProductId': configuracion.credenciales_servicio_historial.product_id,
            'InfoAccountType': configuracion.credenciales_servicio_historial.info_account_type,
            'client_id': configuracion.credenciales_historial.client_id,
            'client_secret': configuracion.credenciales_historial.client_secret,
        }
        body = _construir_payload_historial(entrada, configuracion)
        return {
            'url': configuracion.historial_url,
            'method': 'POST',
            'headers_presentes': {clave: bool(valor) for clave, valor in headers.items()},
            'headers': _sanitizar_valor(headers),
            'body_keys': list(body.keys()),
            'body_preview': _sanitizar_valor(body),
            'parameters_incluidos': 'parameters' in body,
        }

    entrada = EntradaMiDecisor(
        tipo_identificacion=tipo_documento,
        numero_identificacion=numero_documento,
        apellido_razon_social=apellido,
    )
    headers = {
        'Authorization': 'Bearer token-diagnostico',
        'Content-Type': 'application/json',
    }
    body = entrada.como_payload()
    return {
        'url': configuracion.midecisor_url,
        'method': 'POST',
        'headers_presentes': {clave: bool(valor) for clave, valor in headers.items()},
        'headers': _sanitizar_valor(headers),
        'body_keys': list(body.keys()),
        'body_preview': _sanitizar_valor(body),
        'headers_hdc_incluidos': any(
            clave in headers for clave in ('serverIpAddress', 'ProductId', 'InfoAccountType', 'client_id', 'client_secret')
        ),
    }


def _sanitizar_valor(valor):
    if isinstance(valor, dict):
        datos = {}
        for clave, subvalor in valor.items():
            clave_normalizada = str(clave).lower()
            if clave_normalizada in CLAVES_SECRETAS:
                datos[clave] = enmascarar_valor(subvalor)
            elif clave_normalizada in CLAVES_DOCUMENTO:
                datos[clave] = _enmascarar_documento(subvalor)
            elif clave_normalizada in CLAVES_TEXTO_PERSONAL:
                datos[clave] = '***'
            else:
                datos[clave] = _sanitizar_valor(subvalor)
        return datos
    if isinstance(valor, list):
        return [_sanitizar_valor(item) for item in valor]
    return valor


def _enmascarar_documento(documento):
    texto = str(documento or '')
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"

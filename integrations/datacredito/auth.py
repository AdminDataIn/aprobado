import logging

import requests
from django.core.cache import cache

from integrations.datacredito.dto import TokenDatacredito, enmascarar_valor
from integrations.datacredito.exceptions import (
    DatacreditoAuthError,
    DatacreditoConfigError,
    DatacreditoProviderDisabled,
    DatacreditoProviderError,
    DatacreditoTimeoutError,
)
from integrations.datacredito.settings import (
    obtener_configuracion_datacredito,
    obtener_credenciales_oauth as obtener_credenciales_oauth_configuracion,
)


logger = logging.getLogger(__name__)
SERVICIO_DECISOR = 'decisor'
SERVICIO_HISTORIAL = 'historial'
SERVICIOS_VALIDOS = {SERVICIO_DECISOR, SERVICIO_HISTORIAL}
TOKEN_CACHE_KEYS = {
    SERVICIO_DECISOR: 'datacredito:oauth:decisor',
    SERVICIO_HISTORIAL: 'datacredito:oauth:historial',
}
TOKEN_CACHE_KEY = TOKEN_CACHE_KEYS[SERVICIO_DECISOR]
MIN_TOKEN_CACHE_SECONDS = 30


def validar_consumo_real_habilitado(configuracion=None):
    configuracion = configuracion or obtener_configuracion_datacredito()
    if not configuracion.real_enabled:
        raise DatacreditoProviderDisabled(
            'DataCredito real esta deshabilitado por DATACREDITO_REAL_ENABLED=False.'
        )
    return configuracion


def obtener_credenciales_oauth(servicio, configuracion=None):
    return obtener_credenciales_oauth_configuracion(_normalizar_servicio(servicio), configuracion=configuracion)


def generar_token(servicio=SERVICIO_DECISOR, session=None):
    servicio = _normalizar_servicio(servicio)
    configuracion = validar_consumo_real_habilitado()
    credenciales = obtener_credenciales_oauth(servicio, configuracion=configuracion)
    faltantes = credenciales.validar_para_token()
    if faltantes:
        raise DatacreditoConfigError(
            f"Faltan credenciales DataCredito: {', '.join(faltantes)}"
        )
    if servicio == SERVICIO_DECISOR and configuracion.usa_legacy_decisor:
        logger.warning('DataCredito Decisor usa credenciales legacy genericas. Migrar a DATACREDITO_DECISOR_*')
    if servicio == SERVICIO_HISTORIAL and configuracion.usa_legacy_historial:
        logger.warning('DataCredito Historial usa credenciales legacy genericas. Migrar a DATACREDITO_HDC_*')

    cliente_http = session or requests
    headers = {
        'client_id': credenciales.client_id,
        'client_secret': credenciales.client_secret,
        'Content-Type': 'application/json',
    }
    payload = {
        'username': credenciales.username,
        'password': credenciales.password,
    }

    try:
        respuesta = cliente_http.post(
            configuracion.token_url,
            json=payload,
            headers=headers,
            timeout=configuracion.timeout_seconds,
        )
    except requests.exceptions.Timeout as exc:
        raise DatacreditoTimeoutError('Timeout generando token DataCredito.') from exc
    except requests.exceptions.RequestException as exc:
        raise DatacreditoProviderError('Error de red generando token DataCredito.') from exc

    if respuesta.status_code >= 400:
        raise DatacreditoAuthError(
            f'Error OAuth2 DataCredito status={respuesta.status_code}.'
        )

    cuerpo = respuesta.json()
    access_token = cuerpo.get('access_token')
    if not access_token:
        raise DatacreditoAuthError('Respuesta OAuth2 DataCredito sin access_token.')

    token = TokenDatacredito(
        access_token=access_token,
        token_type=cuerpo.get('token_type') or 'Bearer',
        expires_in=int(cuerpo.get('expires_in') or 0),
        metadata_segura={
            'environment': configuracion.environment,
            'servicio': servicio,
            'token_masked': enmascarar_valor(access_token),
        },
    )
    logger.info(
        'Token DataCredito generado. ambiente=%s servicio=%s token=%s',
        configuracion.environment,
        servicio,
        enmascarar_valor(access_token),
    )
    return token


def obtener_token_cacheado(servicio=SERVICIO_DECISOR, session=None):
    servicio = _normalizar_servicio(servicio)
    cache_key = TOKEN_CACHE_KEYS[servicio]
    token_cacheado = cache.get(cache_key)
    if token_cacheado:
        return token_cacheado

    token = generar_token(servicio=servicio, session=session)
    timeout = max(int(token.expires_in or 0) - 60, MIN_TOKEN_CACHE_SECONDS)
    cache.set(cache_key, token, timeout=timeout)
    return token


def revocar_token(token=None, servicio=SERVICIO_DECISOR, session=None):
    servicio = _normalizar_servicio(servicio)
    configuracion = validar_consumo_real_habilitado()
    token = token or cache.get(TOKEN_CACHE_KEYS[servicio])
    if not token:
        return {'revocado': False, 'reason': 'sin_token_cacheado'}

    cliente_http = session or requests
    headers = {'token': token.access_token}
    try:
        respuesta = cliente_http.post(
            configuracion.revoke_token_url,
            headers=headers,
            timeout=configuracion.timeout_seconds,
        )
    except requests.exceptions.Timeout as exc:
        raise DatacreditoTimeoutError('Timeout revocando token DataCredito.') from exc
    except requests.exceptions.RequestException as exc:
        raise DatacreditoProviderError('Error de red revocando token DataCredito.') from exc

    if respuesta.status_code >= 400:
        raise DatacreditoAuthError(
            f'Error revocando token DataCredito status={respuesta.status_code}.'
        )

    cache.delete(TOKEN_CACHE_KEYS[servicio])
    logger.info('Token DataCredito revocado. ambiente=%s servicio=%s', configuracion.environment, servicio)
    return {'revocado': True, 'status_code': respuesta.status_code}


def _normalizar_servicio(servicio):
    servicio = str(servicio or '').lower()
    if servicio not in SERVICIOS_VALIDOS:
        raise DatacreditoConfigError('Servicio DataCredito invalido.')
    return servicio

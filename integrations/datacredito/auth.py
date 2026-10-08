import logging
import hashlib

import requests
from django.core.cache import cache

from integrations.datacredito.dto import TokenDatacredito
from integrations.datacredito.exceptions import (
    DatacreditoAuthError,
    DatacreditoConfigError,
    DatacreditoProviderDisabled,
    DatacreditoProviderError,
    DatacreditoTimeoutError,
)
from integrations.datacredito.http import crear_session_datacredito
from integrations.datacredito.settings import (
    obtener_configuracion_datacredito,
    obtener_credenciales_oauth as obtener_credenciales_oauth_configuracion,
)


logger = logging.getLogger(__name__)
SERVICIO_DECISOR = 'decisor'
SERVICIO_HISTORIAL = 'historial'
SERVICIOS_VALIDOS = {SERVICIO_DECISOR, SERVICIO_HISTORIAL}


def clave_cache_token(servicio, configuracion=None):
    configuracion = configuracion or obtener_configuracion_datacredito()
    servicio = _normalizar_servicio(servicio)
    credenciales = obtener_credenciales_oauth(servicio, configuracion)
    identidad = '\x00'.join((configuracion.token_url, credenciales.client_id,
                             credenciales.username, credenciales.client_secret, credenciales.password))
    digest = hashlib.sha256(identidad.encode('utf-8')).hexdigest()
    return f'datacredito:oauth:v2:{configuracion.environment}:{servicio}:{digest}'


def invalidar_token(token, servicio):
    # Mark the rejected token, rather than deleting a concurrently refreshed token.
    clave = clave_cache_token(servicio)
    digest = hashlib.sha256(token.access_token.encode('utf-8')).hexdigest()
    cache.set(f'{clave}:rechazado:{digest}', True, timeout=max(int(token.expires_in or 0), 60))


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

    cliente_http = session if session is not None else crear_session_datacredito(configuracion)
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
            f'Error OAuth2 DataCredito status={respuesta.status_code}.',
            servicio=servicio, etapa='OAUTH', http_status=respuesta.status_code,
            error_tipo='OAUTH',
        )

    try:
        cuerpo = respuesta.json()
        access_token = cuerpo.get('access_token')
        expires_in = int(cuerpo.get('expires_in') or 0)
    except (ValueError, TypeError, AttributeError) as exc:
        raise DatacreditoAuthError('Respuesta OAuth2 no interpretable.', servicio=servicio) from exc
    if not access_token:
        raise DatacreditoAuthError('Respuesta OAuth2 DataCredito sin access_token.')

    token = TokenDatacredito(
        access_token=access_token,
        token_type=cuerpo.get('token_type') or 'Bearer',
        expires_in=expires_in,
        metadata_segura={
            'environment': configuracion.environment,
            'servicio': servicio,
        },
    )
    logger.info(
        'Token DataCredito generado. ambiente=%s servicio=%s',
        configuracion.environment,
        servicio,
    )
    return token


def obtener_token_cacheado(servicio=SERVICIO_DECISOR, session=None):
    servicio = _normalizar_servicio(servicio)
    configuracion = validar_consumo_real_habilitado()
    if obtener_credenciales_oauth(servicio, configuracion).validar_para_token():
        raise DatacreditoConfigError('Credenciales OAuth incompletas.')
    cache_key = clave_cache_token(servicio, configuracion)
    token_cacheado = cache.get(cache_key)
    if token_cacheado:
        digest = hashlib.sha256(token_cacheado.access_token.encode('utf-8')).hexdigest()
        if not cache.get(f'{cache_key}:rechazado:{digest}'):
            return token_cacheado

    token = generar_token(servicio=servicio, session=session)
    timeout = int(token.expires_in or 0) - 60
    if timeout > 0:
        cache.set(cache_key, token, timeout=timeout)
    return token


def revocar_token(token=None, servicio=SERVICIO_DECISOR, session=None):
    servicio = _normalizar_servicio(servicio)
    configuracion = validar_consumo_real_habilitado()
    token = token or cache.get(clave_cache_token(servicio, configuracion))
    if not token:
        return {'revocado': False, 'reason': 'sin_token_cacheado'}

    cliente_http = session if session is not None else crear_session_datacredito(configuracion)
    credenciales = obtener_credenciales_oauth(servicio, configuracion)
    if credenciales.validar_para_token():
        raise DatacreditoConfigError('Credenciales OAuth incompletas.')
    headers = {'token': token.access_token, 'client_id': credenciales.client_id,
               'client_secret': credenciales.client_secret, 'Content-Type': 'application/json'}
    payload = {'username': credenciales.username, 'password': credenciales.password}
    try:
        respuesta = cliente_http.post(
            configuracion.revoke_token_url,
            json=payload,
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

    invalidar_token(token, servicio)
    logger.info('Token DataCredito revocado. ambiente=%s servicio=%s', configuracion.environment, servicio)
    return {'revocado': True, 'status_code': respuesta.status_code}


def _normalizar_servicio(servicio):
    servicio = str(servicio or '').lower()
    if servicio not in SERVICIOS_VALIDOS:
        raise DatacreditoConfigError('Servicio DataCredito invalido.')
    return servicio

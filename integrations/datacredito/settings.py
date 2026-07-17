import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings

from integrations.datacredito.dto import (
    CredencialesDatacredito,
    CredencialesOAuthDecisor,
    CredencialesOAuthHistorial,
    CredencialesServicioHistorial,
)


TOKEN_URLS = {
    'uat': 'https://uat-api.datacredito.com.co/spla/oauth2/v1/token',
    'prod': 'https://api.datacredito.com.co/spla/oauth2/v1/token',
}

REVOKE_TOKEN_URLS = {
    'uat': 'https://uat-api.datacredito.com.co/spla/oauth2/v1/revokeToken',
    'prod': 'https://api.datacredito.com.co/spla/oauth2/v1/revokeToken',
}

MIDECISOR_URLS = {
    'uat': 'https://uat-api.datacredito.com.co/co/cs/midecisor/v1/client',
    'prod': 'https://prod-api.datacredito.com.co/co/cs/midecisor/v1/client',
}

HISTORIAL_URLS = {
    'uat': 'https://uat-api.datacredito.com.co/cs/credit-history/v1/hdcplus',
    'prod': 'https://api.datacredito.com.co/cs/credit-history/v1/hdcplus',
}


@dataclass(frozen=True)
class ConfiguracionDatacredito:
    real_enabled: bool
    environment: str
    default_service: str
    timeout_seconds: int
    reuse_days: int
    document_hash_secret: str
    token_url: str
    revoke_token_url: str
    midecisor_url: str
    historial_url: str
    credenciales: CredencialesDatacredito
    credenciales_decisor: CredencialesOAuthDecisor
    credenciales_historial: CredencialesOAuthHistorial
    credenciales_servicio_historial: CredencialesServicioHistorial
    usa_legacy_decisor: bool = False
    usa_legacy_historial: bool = False
    parametros_historial: tuple[dict, ...] = ()
    parametros_historial_error: str = ''
    parametros_historial_configurados: bool = False
    parametros_historial_longitud: int = 0


def obtener_configuracion_datacredito():
    ambiente = str(getattr(settings, 'DATACREDITO_ENVIRONMENT', 'uat') or 'uat').lower()
    if ambiente in {'production', 'produccion', 'prod'}:
        ambiente = 'prod'
    else:
        ambiente = 'uat'

    credenciales_legacy = CredencialesDatacredito(
        client_id=getattr(settings, 'DATACREDITO_CLIENT_ID', '') or '',
        client_secret=getattr(settings, 'DATACREDITO_CLIENT_SECRET', '') or '',
        username=getattr(settings, 'DATACREDITO_USERNAME', '') or '',
        password=getattr(settings, 'DATACREDITO_PASSWORD', '') or '',
        api_password=getattr(settings, 'DATACREDITO_API_PASSWORD', '') or '',
        product_id=str(getattr(settings, 'DATACREDITO_PRODUCT_ID', '') or ''),
        info_account_type=str(getattr(settings, 'DATACREDITO_INFO_ACCOUNT_TYPE', '1') or '1'),
        server_ip_address=getattr(settings, 'DATACREDITO_SERVER_IP_ADDRESS', '') or '',
    )
    credenciales_decisor = CredencialesOAuthDecisor(
        client_id=getattr(settings, 'DATACREDITO_DECISOR_CLIENT_ID', '') or '',
        client_secret=getattr(settings, 'DATACREDITO_DECISOR_CLIENT_SECRET', '') or '',
        username=(
            getattr(settings, 'DATACREDITO_DECISOR_TOKEN_USERNAME', '')
            or getattr(settings, 'DATACREDITO_DECISOR_USERNAME', '')
            or ''
        ),
        password=(
            getattr(settings, 'DATACREDITO_DECISOR_TOKEN_PASSWORD', '')
            or getattr(settings, 'DATACREDITO_DECISOR_PASSWORD', '')
            or ''
        ),
    )
    credenciales_historial = CredencialesOAuthHistorial(
        client_id=getattr(settings, 'DATACREDITO_HDC_CLIENT_ID', '') or '',
        client_secret=getattr(settings, 'DATACREDITO_HDC_CLIENT_SECRET', '') or '',
        username=(
            getattr(settings, 'DATACREDITO_HDC_TOKEN_USERNAME', '')
            or getattr(settings, 'DATACREDITO_HDC_USERNAME', '')
            or ''
        ),
        password=(
            getattr(settings, 'DATACREDITO_HDC_TOKEN_PASSWORD', '')
            or getattr(settings, 'DATACREDITO_HDC_PASSWORD', '')
            or ''
        ),
    )
    credenciales_servicio_historial = CredencialesServicioHistorial(
        user=getattr(settings, 'DATACREDITO_HDC_SERVICE_USER', '') or '',
        password=getattr(settings, 'DATACREDITO_HDC_SERVICE_PASSWORD', '') or '',
        product_id=str(getattr(settings, 'DATACREDITO_HDC_PRODUCT_ID', '64') or '64'),
        info_account_type=str(getattr(settings, 'DATACREDITO_HDC_INFO_ACCOUNT_TYPE', '1') or '1'),
        server_ip_address=getattr(settings, 'DATACREDITO_HDC_SERVER_IP_ADDRESS', '') or '',
        channel_name=getattr(settings, 'DATACREDITO_HDC_CHANNEL_NAME', 'Canal-01') or 'Canal-01',
        channel_type=str(getattr(settings, 'DATACREDITO_HDC_CHANNEL_TYPE', '42') or '42'),
    )
    parametros_historial_raw = _obtener_parametros_historial_raw()
    parametros_historial, parametros_historial_error = _parsear_parametros_historial(parametros_historial_raw)
    usa_legacy_decisor = False
    usa_legacy_historial = False
    if credenciales_decisor.validar_para_token() and not credenciales_legacy.validar_para_token():
        credenciales_decisor = CredencialesOAuthDecisor(
            client_id=credenciales_legacy.client_id,
            client_secret=credenciales_legacy.client_secret,
            username=credenciales_legacy.username,
            password=credenciales_legacy.password,
        )
        usa_legacy_decisor = True
    if credenciales_historial.validar_para_token() and not credenciales_legacy.validar_para_token():
        credenciales_historial = CredencialesOAuthHistorial(
            client_id=credenciales_legacy.client_id,
            client_secret=credenciales_legacy.client_secret,
            username=credenciales_legacy.username,
            password=credenciales_legacy.password,
        )
        usa_legacy_historial = True
    if (
        credenciales_servicio_historial.validar_para_historial()
        and credenciales_legacy.api_password
        and credenciales_legacy.product_id
        and credenciales_legacy.server_ip_address
    ):
        credenciales_servicio_historial = CredencialesServicioHistorial(
            user=credenciales_legacy.username,
            password=credenciales_legacy.api_password,
            product_id=credenciales_legacy.product_id,
            info_account_type=credenciales_legacy.info_account_type,
            server_ip_address=credenciales_legacy.server_ip_address,
        )

    return ConfiguracionDatacredito(
        real_enabled=bool(
            getattr(settings, 'DATACREDITO_ENABLED', False)
            and getattr(settings, 'DATACREDITO_REAL_ENABLED', False)
        ),
        environment=ambiente,
        default_service=str(getattr(settings, 'DATACREDITO_DEFAULT_SERVICE', 'decisor') or 'decisor').lower(),
        timeout_seconds=int(getattr(settings, 'DATACREDITO_TIMEOUT_SECONDS', 15) or 15),
        reuse_days=max(int(getattr(settings, 'DATACREDITO_REUSE_DAYS', 30) or 30), 1),
        document_hash_secret=str(getattr(settings, 'DATACREDITO_DOCUMENT_HASH_SECRET', '') or ''),
        token_url=getattr(settings, 'DATACREDITO_TOKEN_URL', '') or TOKEN_URLS[ambiente],
        revoke_token_url=getattr(settings, 'DATACREDITO_REVOKE_TOKEN_URL', '') or REVOKE_TOKEN_URLS[ambiente],
        midecisor_url=getattr(settings, 'DATACREDITO_MIDECISOR_URL', '') or MIDECISOR_URLS[ambiente],
        historial_url=getattr(settings, 'DATACREDITO_HISTORIAL_URL', '') or HISTORIAL_URLS[ambiente],
        credenciales=credenciales_legacy,
        credenciales_decisor=credenciales_decisor,
        credenciales_historial=credenciales_historial,
        credenciales_servicio_historial=credenciales_servicio_historial,
        usa_legacy_decisor=usa_legacy_decisor,
        usa_legacy_historial=usa_legacy_historial,
        parametros_historial=parametros_historial,
        parametros_historial_error=parametros_historial_error,
        parametros_historial_configurados=bool(parametros_historial_raw.strip()),
        parametros_historial_longitud=len(parametros_historial_raw),
    )


def obtener_credenciales_oauth(servicio, configuracion=None):
    configuracion = configuracion or obtener_configuracion_datacredito()
    servicio = str(servicio or '').lower()
    if servicio == 'decisor':
        return configuracion.credenciales_decisor
    if servicio == 'historial':
        return configuracion.credenciales_historial
    raise ValueError('servicio_datacredito_invalido')


def _parsear_parametros_historial(valor):
    texto = str(valor or '').strip()
    if not texto:
        return (), ''
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return (), 'DATACREDITO_HDC_PARAMETERS_JSON no es JSON valido.'
    if not isinstance(datos, list):
        return (), 'DATACREDITO_HDC_PARAMETERS_JSON debe ser una lista.'

    parametros = []
    for item in datos:
        if not isinstance(item, Mapping):
            return (), 'Cada parametro HDC debe ser un objeto.'
        parametro = {
            'type': str(item.get('type', '')).strip(),
            'nameParameter': str(item.get('nameParameter', '')).strip(),
            'valueParameter': str(item.get('valueParameter', '')).strip(),
        }
        if not all(parametro.values()):
            return (), 'Cada parametro HDC debe incluir type, nameParameter y valueParameter.'
        parametros.append(parametro)
    return tuple(parametros), ''


def _obtener_parametros_historial_raw():
    valor_configuracion = getattr(settings, 'DATACREDITO_HDC_PARAMETERS_JSON', None)
    if valor_configuracion:
        return str(valor_configuracion)
    valor_entorno = os.environ.get('DATACREDITO_HDC_PARAMETERS_JSON')
    if valor_entorno is not None:
        return str(valor_entorno or '')
    return str(valor_configuracion or '')

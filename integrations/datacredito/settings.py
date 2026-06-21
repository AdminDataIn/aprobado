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
    timeout_seconds: int
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
        username=getattr(settings, 'DATACREDITO_DECISOR_USERNAME', '') or '',
        password=getattr(settings, 'DATACREDITO_DECISOR_PASSWORD', '') or '',
    )
    credenciales_historial = CredencialesOAuthHistorial(
        client_id=getattr(settings, 'DATACREDITO_HDC_CLIENT_ID', '') or '',
        client_secret=getattr(settings, 'DATACREDITO_HDC_CLIENT_SECRET', '') or '',
        username=getattr(settings, 'DATACREDITO_HDC_USERNAME', '') or '',
        password=getattr(settings, 'DATACREDITO_HDC_PASSWORD', '') or '',
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
        real_enabled=bool(getattr(settings, 'DATACREDITO_REAL_ENABLED', False)),
        environment=ambiente,
        timeout_seconds=int(getattr(settings, 'DATACREDITO_TIMEOUT_SECONDS', 15) or 15),
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
    )


def obtener_credenciales_oauth(servicio, configuracion=None):
    configuracion = configuracion or obtener_configuracion_datacredito()
    servicio = str(servicio or '').lower()
    if servicio == 'decisor':
        return configuracion.credenciales_decisor
    if servicio == 'historial':
        return configuracion.credenciales_historial
    raise ValueError('servicio_datacredito_invalido')

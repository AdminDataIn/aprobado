import logging

import requests

from integrations.datacredito.settings import obtener_configuracion_datacredito


logger = logging.getLogger(__name__)


def crear_session_datacredito(configuracion=None):
    """Crea la sesion comun para OAuth y servicios DataCredito."""
    configuracion = configuracion or obtener_configuracion_datacredito()
    session = requests.Session()
    if configuracion.proxy_url:
        session.proxies.update(
            {
                'http': configuracion.proxy_url,
                'https': configuracion.proxy_url,
            }
        )
    logger.info(
        'Sesion HTTP DataCredito creada. ambiente=%s proxy_configured=%s',
        configuracion.environment,
        bool(configuracion.proxy_url),
    )
    return session

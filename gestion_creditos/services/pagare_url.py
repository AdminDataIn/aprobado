"""
Servicio para generar URLs públicas temporales de pagarés.
Necesario para que ZapSign pueda descargar el PDF.
"""

from django.core.signing import TimestampSigner, SignatureExpired, BadSignature
from django.http import FileResponse, JsonResponse, HttpRequest
from django.conf import settings
from gestion_creditos.models import Pagare
import logging

logger = logging.getLogger('zapsign')


def generar_url_publica_temporal(pagare: Pagare, max_age: int = 86400) -> str:
    """
    Genera una URL pública temporal para descargar el PDF del pagaré.

    Args:
        pagare: Instancia del modelo Pagare
        max_age: Tiempo de validez en segundos (default: 24 horas)

    Returns:
        str: URL completa para descargar el PDF

    Example:
        >>> pagare = Pagare.objects.get(id=1)
        >>> url = generar_url_publica_temporal(pagare)
        >>> print(url)
        'https://aprobado.com/api/pagares/download/MQ:1qfYnR:...'
    """
    signer = TimestampSigner()
    token = signer.sign(f"{pagare.id}:{max_age}")

    # Construir URL completa
    domain = getattr(settings, 'SITE_DOMAIN', 'localhost:8000')
    protocol = 'https' if getattr(settings, 'SITE_HTTPS', False) else 'http'

    url = f"{protocol}://{domain}/api/pagares/download/{token}/"

    logger.info(f"URL temporal generada para pagaré {pagare.numero_pagare} (válida por {max_age}s)")

    return url


def descargar_pagare_publico(request: HttpRequest, token: str) -> FileResponse:
    """
    Vista para descargar un pagaré usando un token firmado temporal.

    Args:
        request: HttpRequest de Django
        token: Token firmado generado por generar_url_publica_temporal()

    Returns:
        FileResponse: Archivo PDF del pagaré

    Raises:
        Http404: Si el token es inválido o el pagaré no existe
        Http410: Si el token ha expirado
    """
    signer = TimestampSigner()

    try:
        # Validar y decodificar token (max_age configurable)
        unsigned = signer.unsign(token)
        if ":" in unsigned:
            pagare_id_str, max_age_str = unsigned.split(":", 1)
            try:
                max_age = int(max_age_str)
            except ValueError:
                raise BadSignature("max_age invalido")
        else:
            pagare_id_str = unsigned
            max_age = 86400

        signer.unsign(token, max_age=max_age)
        pagare_id = int(pagare_id_str)

        # Buscar pagaré
        pagare = Pagare.objects.get(id=pagare_id)

        # Log de acceso
        logger.info(
            f"Descarga de pagaré {pagare.numero_pagare} "
            f"desde IP {request.META.get('REMOTE_ADDR')}"
        )

        # Retornar archivo PDF
        return FileResponse(
            pagare.archivo_pdf,
            content_type='application/pdf',
            as_attachment=False,  # Mostrar en navegador, no forzar descarga
            filename=f"{pagare.numero_pagare}.pdf"
        )

    except SignatureExpired:
        logger.warning(f"Token expirado para descarga de pagaré desde {request.META.get('REMOTE_ADDR')}")
        return JsonResponse(
            {'error': 'El enlace ha expirado. Por favor, solicite uno nuevo.'},
            status=410
        )

    except BadSignature:
        logger.warning(f"Token inválido para descarga de pagaré desde {request.META.get('REMOTE_ADDR')}")
        return JsonResponse(
            {'error': 'Enlace inválido.'},
            status=403
        )

    except Pagare.DoesNotExist:
        logger.error(f"Pagaré no encontrado para token válido: {token}")
        return JsonResponse(
            {'error': 'Pagaré no encontrado.'},
            status=404
        )

    except Exception as e:
        logger.error(f"Error al descargar pagaré: {str(e)}")
        return JsonResponse(
            {'error': 'Error al descargar el pagaré.'},
            status=500
        )


def validar_url_accesible(url: str) -> bool:
    """
    Valida que una URL sea accesible públicamente.
    Útil para testing antes de enviar a ZapSign.

    Args:
        url: URL a validar

    Returns:
        bool: True si la URL es accesible, False en caso contrario
    """
    import requests

    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"URL no accesible: {url} - {str(e)}")
        return False

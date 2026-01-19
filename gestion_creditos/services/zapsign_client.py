"""
Cliente para integración con ZapSign API.
Maneja el envío de documentos para firma electrónica.
"""

import requests
import logging
from typing import Dict, Optional
from django.conf import settings
from django.utils import timezone

from gestion_creditos.models import Pagare

logger = logging.getLogger('zapsign')


class ZapSignAPIError(Exception):
    """Excepción base para errores de la API de ZapSign"""
    pass


class ZapSignClient:
    """Cliente para interactuar con la API de ZapSign"""

    def __init__(self):
        self.api_token = settings.ZAPSIGN_API_TOKEN
        self.base_url = "https://api.zapsign.com.br/api/v1"
        self.environment = getattr(settings, 'ZAPSIGN_ENVIRONMENT', 'sandbox')

        if self.environment == 'sandbox':
            self.base_url = "https://sandbox.api.zapsign.com.br/api/v1"

        if not self.api_token:
            raise ZapSignAPIError("ZAPSIGN_API_TOKEN no está configurado en settings")

    def _get_headers(self) -> Dict[str, str]:
        """Retorna los headers necesarios para las peticiones a la API"""
        return {
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        }

    def crear_documento(
        self,
        nombre: str,
        url_pdf: str,
        email_firmante: str,
        nombre_firmante: str,
        brand_name: str = "Aprobado"
    ) -> Dict:
        """
        Crea un documento en ZapSign para firma.

        Args:
            nombre: Nombre del documento (ej: "Pagaré CR-2026-00123")
            url_pdf: URL pública del PDF a firmar
            email_firmante: Email del cliente que va a firmar
            nombre_firmante: Nombre completo del firmante
            brand_name: Nombre de la marca/empresa

        Returns:
            Dict con la respuesta de ZapSign:
            {
                "token": "d7fa9b7f-...",
                "signers": [{
                    "sign_url": "https://app.zapsign.com.br/verificar/..."
                }]
            }

        Raises:
            ZapSignAPIError: Si la API retorna un error
        """
        endpoint = f"{self.base_url}/docs/"

        auth_mode = getattr(settings, 'ZAPSIGN_AUTH_MODE', 'assinaturaTela')
        send_automatic_email = getattr(settings, 'ZAPSIGN_SEND_AUTOMATIC_EMAIL', True)
        payload = {
            "name": nombre,
            "url_pdf": url_pdf,
            "signers": [{
                "email": email_firmante,
                "name": nombre_firmante,
                "auth_mode": auth_mode,
                "send_automatic_email": send_automatic_email
            }],
            "brand_name": brand_name,
            "lang": "es"
        }

        try:
            logger.info(f"Enviando documento a ZapSign: {nombre}")
            logger.debug(f"Payload: {payload}")

            response = requests.post(
                endpoint,
                json=payload,
                headers=self._get_headers(),
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            logger.info(f"Documento creado exitosamente. Token: {data.get('token')}")
            return data

        except requests.exceptions.HTTPError as e:
            error_msg = f"Error HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Error al crear documento en ZapSign: {error_msg}")
            raise ZapSignAPIError(error_msg)

        except requests.exceptions.RequestException as e:
            error_msg = f"Error de conexión con ZapSign: {str(e)}"
            logger.error(error_msg)
            raise ZapSignAPIError(error_msg)

        except Exception as e:
            error_msg = f"Error inesperado: {str(e)}"
            logger.error(error_msg)
            raise ZapSignAPIError(error_msg)

    def consultar_documento(self, doc_token: str) -> Dict:
        """
        Consulta el estado de un documento en ZapSign.

        Args:
            doc_token: Token del documento en ZapSign

        Returns:
            Dict con el estado del documento:
            {
                "token": "d7fa9b7f-...",
                "status": "pending" | "signed" | "refused",
                "signed_at": "2026-01-12T16:45:22Z",
                "signers": [...]
            }

        Raises:
            ZapSignAPIError: Si la API retorna un error
        """
        endpoint = f"{self.base_url}/docs/{doc_token}/"

        try:
            logger.info(f"Consultando estado del documento: {doc_token}")

            response = requests.get(
                endpoint,
                headers=self._get_headers(),
                timeout=10
            )

            response.raise_for_status()
            data = response.json()

            logger.info(f"Estado del documento {doc_token}: {data.get('status')}")
            return data

        except requests.exceptions.HTTPError as e:
            error_msg = f"Error HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Error al consultar documento: {error_msg}")
            raise ZapSignAPIError(error_msg)

        except requests.exceptions.RequestException as e:
            error_msg = f"Error de conexión con ZapSign: {str(e)}"
            logger.error(error_msg)
            raise ZapSignAPIError(error_msg)

    def descargar_pdf_firmado(self, doc_token: str) -> bytes:
        """
        Descarga el PDF firmado desde ZapSign.

        Args:
            doc_token: Token del documento en ZapSign

        Returns:
            bytes: Contenido del PDF firmado

        Raises:
            ZapSignAPIError: Si la API retorna un error
        """
        endpoint = f"{self.base_url}/docs/{doc_token}/download-signed/"

        try:
            logger.info(f"Descargando PDF firmado: {doc_token}")

            response = requests.get(
                endpoint,
                headers=self._get_headers(),
                timeout=30
            )

            response.raise_for_status()

            logger.info(f"PDF firmado descargado exitosamente ({len(response.content)} bytes)")
            return response.content

        except requests.exceptions.HTTPError as e:
            error_msg = f"Error HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Error al descargar PDF firmado: {error_msg}")
            raise ZapSignAPIError(error_msg)

        except requests.exceptions.RequestException as e:
            error_msg = f"Error de conexión con ZapSign: {str(e)}"
            logger.error(error_msg)
            raise ZapSignAPIError(error_msg)


def enviar_pagare_a_zapsign(pagare: Pagare, url_pdf_publica: str) -> Pagare:
    """
    Envía un pagaré a ZapSign para firma electrónica.

    Args:
        pagare: Instancia del modelo Pagare
        url_pdf_publica: URL pública del PDF (debe ser accesible desde internet)

    Returns:
        Pagare: Instancia actualizada con los datos de ZapSign

    Raises:
        ZapSignAPIError: Si hay un error en la integración con ZapSign
        ValueError: Si el pagaré no está en estado válido
    """

    # Validaciones
    if pagare.estado != Pagare.EstadoPagare.CREATED:
        raise ValueError(f"El pagaré debe estar en estado CREATED, actual: {pagare.estado}")

    credito = pagare.credito
    detalle = credito.detalle
    usuario = credito.usuario

    if not detalle:
        raise ValueError("El credito no tiene detalle asociado")

    # Preparar datos del firmante
    if credito.linea == credito.LineaCredito.LIBRANZA:
        nombre_firmante = detalle.nombre_completo or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        email_firmante = detalle.correo_electronico or usuario.email
    else:
        nombre_firmante = detalle.nombre or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        email_firmante = usuario.email

    if not nombre_firmante:
        raise ValueError("No se puede determinar el nombre del firmante")

    if not email_firmante:
        raise ValueError("El usuario no tiene email configurado")

    # Crear cliente ZapSign
    client = ZapSignClient()

    try:
        # Enviar documento a ZapSign
        logger.info(f"Enviando pagaré {pagare.numero_pagare} a ZapSign")

        response_data = client.crear_documento(
            nombre=f"Pagaré {credito.numero_credito}",
            url_pdf=url_pdf_publica,
            email_firmante=email_firmante,
            nombre_firmante=nombre_firmante,
            brand_name="Aprobado"
        )

        sign_url = response_data['signers'][0].get('sign_url')

        # Actualizar pagar? con datos de ZapSign
        pagare.zapsign_doc_token = response_data['token']
        pagare.zapsign_sign_url = sign_url
        pagare.estado = Pagare.EstadoPagare.SENT
        pagare.fecha_envio = timezone.now()
        pagare.save()

        enviar_email_local = getattr(settings, 'ZAPSIGN_SEND_LOCAL_EMAIL', False)
        if enviar_email_local and sign_url:
            try:
                from gestion_creditos.email_service import enviar_email_simple
                asunto = f"Firma de pagar? {pagare.numero_pagare}"
                mensaje = (
                    f"Hola {nombre_firmante},\n\n"
                    f"Ya puedes firmar tu pagar? desde este enlace:\n{sign_url}\n\n"
                    "Si no solicitaste este cr?dito, por favor ignora este mensaje."
                )
                enviar_email_simple(email_firmante, asunto, mensaje)
            except Exception as e:
                logger.error(f"Error al enviar email local de firma para pagar? {pagare.numero_pagare}: {e}")

        logger.info(
            f"Pagar? {pagare.numero_pagare} enviado exitosamente a ZapSign. "
            f"Token: {pagare.zapsign_doc_token}"
        )

        return pagare

    except ZapSignAPIError as e:
        logger.error(f"Error al enviar pagaré {pagare.numero_pagare} a ZapSign: {str(e)}")
        raise

    except Exception as e:
        error_msg = f"Error inesperado al enviar pagaré a ZapSign: {str(e)}"
        logger.error(error_msg)
        raise ZapSignAPIError(error_msg)


def consultar_estado_pagare(pagare: Pagare) -> Dict:
    """
    Consulta el estado actual de un pagaré en ZapSign.

    Args:
        pagare: Instancia del modelo Pagare

    Returns:
        Dict: Estado del documento en ZapSign

    Raises:
        ValueError: Si el pagaré no tiene doc_token
        ZapSignAPIError: Si hay un error en la API
    """
    if not pagare.zapsign_doc_token:
        raise ValueError("El pagaré no tiene doc_token de ZapSign")

    client = ZapSignClient()
    return client.consultar_documento(pagare.zapsign_doc_token)


def descargar_pdf_firmado_pagare(pagare: Pagare) -> bytes:
    """
    Descarga el PDF firmado de un pagaré desde ZapSign.

    Args:
        pagare: Instancia del modelo Pagare

    Returns:
        bytes: Contenido del PDF firmado

    Raises:
        ValueError: Si el pagaré no está firmado o no tiene doc_token
        ZapSignAPIError: Si hay un error en la API
    """
    if not pagare.zapsign_doc_token:
        raise ValueError("El pagaré no tiene doc_token de ZapSign")

    if pagare.estado != Pagare.EstadoPagare.SIGNED:
        raise ValueError(f"El pagaré debe estar SIGNED, actual: {pagare.estado}")

    client = ZapSignClient()
    return client.descargar_pdf_firmado(pagare.zapsign_doc_token)

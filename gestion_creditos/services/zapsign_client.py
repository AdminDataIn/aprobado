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

        # Validacion de identidad por selfie en signers:
        # - En sandbox queda activa por defecto para pruebas.
        # - En produccion queda desactivada por defecto.
        # - Se puede controlar con settings:
        #   ZAPSIGN_ENABLE_SELFIE_VALIDATION (bool)
        #   ZAPSIGN_SELFIE_VALIDATION_TYPE (str)
        enable_selfie_validation = getattr(
            settings,
            'ZAPSIGN_ENABLE_SELFIE_VALIDATION',
            self.environment == 'sandbox'
        )
        selfie_validation_type = getattr(
            settings,
            'ZAPSIGN_SELFIE_VALIDATION_TYPE',
            'identity-verification'
        )

        signer_payload = {
            "email": email_firmante,
            "name": nombre_firmante,
            "auth_mode": auth_mode,
            "send_automatic_email": send_automatic_email
        }
        if enable_selfie_validation and selfie_validation_type:
            signer_payload["selfie_validation_type"] = selfie_validation_type

        payload = {
            "name": nombre,
            "url_pdf": url_pdf,
            "signers": [signer_payload],
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


def _limpiar_email(email: Optional[str]) -> str:
    return (email or '').strip().lower()


def _obtener_datos_firmante(credito, detalle, usuario):
    """
    Determina el firmante principal y correos de copia para notificaciones de firma.
    Regla de negocio:
    - Principal: correo ingresado en la solicitud (si existe), de lo contrario el del usuario.
    - Copias: correo del usuario + correo social (Google) cuando sea distinto al principal.
    """
    if credito.linea == credito.LineaCredito.LIBRANZA:
        nombre_firmante = detalle.nombre_completo or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        correo_solicitud = _limpiar_email(getattr(detalle, 'correo_electronico', ''))
    else:
        nombre_firmante = detalle.nombre or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        correo_solicitud = ''

    email_usuario = _limpiar_email(getattr(usuario, 'email', ''))
    email_firmante = correo_solicitud or email_usuario

    correos_copia = set()
    if email_usuario:
        correos_copia.add(email_usuario)
    if correo_solicitud:
        correos_copia.add(correo_solicitud)

    # Correo social (Google) como copia adicional si existe.
    try:
        from allauth.socialaccount.models import SocialAccount
        for account in SocialAccount.objects.filter(user=usuario):
            extra_data = account.extra_data or {}
            email_social = _limpiar_email(extra_data.get('email'))
            if email_social:
                correos_copia.add(email_social)
    except Exception:
        pass

    if email_firmante:
        correos_copia.discard(email_firmante)

    return nombre_firmante, email_firmante, sorted(correos_copia)


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
    nombre_firmante, email_firmante, emails_copia = _obtener_datos_firmante(credito, detalle, usuario)

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
        enviar_copias_firma = getattr(settings, 'ZAPSIGN_SEND_COPY_EMAILS', True)
        if sign_url and (enviar_email_local or enviar_copias_firma):
            try:
                from gestion_creditos.email_service import enviar_email_simple
                asunto = f"Firma de pagar? {pagare.numero_pagare}"
                mensaje = (
                    f"Hola {nombre_firmante},\n\n"
                    f"Ya puedes firmar tu pagar? desde este enlace:\n{sign_url}\n\n"
                    "Si no solicitaste este cr?dito, por favor ignora este mensaje."
                )
                # Si ZapSign no envia correo automatico, enviamos al principal por canal local.
                if enviar_email_local:
                    enviar_email_simple(email_firmante, asunto, mensaje)

                # Copias al correo de usuario y al correo social (Google), sin duplicados.
                if enviar_copias_firma:
                    for email_copia in emails_copia:
                        enviar_email_simple(email_copia, asunto, mensaje)
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

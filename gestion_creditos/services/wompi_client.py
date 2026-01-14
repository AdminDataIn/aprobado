"""
Wompi API Client - Integración completa según documentación oficial
https://docs.wompi.co/docs/colombia/metodos-de-pago/

Métodos de pago implementados:
- CARD (Tarjetas de Crédito/Débito)
- PSE (Débito bancario)
- NEQUI (Billetera digital)
- BANCOLOMBIA_TRANSFER (Botón Bancolombia)
"""
import requests
import logging
import hashlib
from typing import Dict, Optional, List
from django.conf import settings

logger = logging.getLogger(__name__)


class WompiAPIException(Exception):
    """Excepción personalizada para errores de la API de Wompi"""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(self.message)


class WompiClient:
    """
    Cliente para interactuar con la API de Wompi Colombia

    Documentación oficial: https://docs.wompi.co/docs/colombia/metodos-de-pago/
    API Reference: https://app.swaggerhub.com/apis-docs/waybox/wompi/1.2.0
    """

    # ==========================================
    # DATOS DE PRUEBA PARA SANDBOX
    # Fuente: https://docs.wompi.co/docs/colombia/datos-de-prueba-en-sandbox/
    # ==========================================

    # Nequi - Números de prueba
    SANDBOX_NEQUI_APPROVED = "3991111111"   # Transacción APROBADA
    SANDBOX_NEQUI_DECLINED = "3992222222"   # Transacción RECHAZADA
    # Cualquier otro número = ERROR

    # Tarjetas - Números de prueba
    SANDBOX_CARD_APPROVED = "4242424242424242"   # Transacción APROBADA
    SANDBOX_CARD_DECLINED = "4111111111111111"   # Transacción RECHAZADA
    # Cualquier otra tarjeta = ERROR

    # PSE - Códigos de banco de prueba
    SANDBOX_PSE_BANK_APPROVED = "1"   # Banco que aprueba
    SANDBOX_PSE_BANK_DECLINED = "2"   # Banco que rechaza (Banco que rechaza)

    def __init__(self):
        """Inicializar cliente con configuración de Django settings"""
        self.base_url = getattr(settings, 'WOMPI_API_BASE_URL', 'https://sandbox.wompi.co/v1')
        self.public_key = settings.WOMPI_PUBLIC_KEY
        self.private_key = settings.WOMPI_PRIVATE_KEY
        self.integrity_key = getattr(settings, 'WOMPI_INTEGRITY_KEY', None)
        self.environment = getattr(settings, 'WOMPI_ENVIRONMENT', 'sandbox')
        self.timeout = 30

        # Sesión para reutilizar conexiones
        self._session = requests.Session()

    def _get_headers(self, use_private_key: bool = False) -> Dict[str, str]:
        """
        Headers para las peticiones a la API

        IMPORTANTE: Incluye Origin y Referer para evitar bloqueo de WAF
        """
        key = self.private_key if use_private_key else self.public_key
        return {
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'es-CO,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            # Headers para evitar bloqueo de WAF
            'Origin': 'https://comercios.wompi.co',
            'Referer': 'https://comercios.wompi.co/',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }

    def _calculate_signature(self, reference: str, amount_in_cents: int, currency: str) -> str:
        """
        Calcular firma de integridad para una transacción

        Fórmula: SHA256(reference + amount_in_cents + currency + integrity_key)

        Args:
            reference: Referencia única de la transacción
            amount_in_cents: Monto en centavos
            currency: Moneda (COP)

        Returns:
            Firma SHA256 en hexadecimal
        """
        if not self.integrity_key:
            logger.warning("WOMPI_INTEGRITY_KEY no configurada - firma no generada")
            return ""

        concat_string = f"{reference}{amount_in_cents}{currency}{self.integrity_key}"
        signature = hashlib.sha256(concat_string.encode('utf-8')).hexdigest()

        logger.debug(f"Signature para ref {reference}: {signature[:20]}...")
        return signature

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        use_private_key: bool = False
    ) -> Dict:
        """
        Realizar petición a la API de Wompi

        Args:
            method: GET o POST
            endpoint: Ruta del endpoint (ej: /transactions)
            data: Datos para el body (POST) o params (GET)
            use_private_key: Usar llave privada en lugar de pública

        Returns:
            Respuesta JSON de la API

        Raises:
            WompiAPIException: En caso de error
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(use_private_key=use_private_key)

        try:
            key_type = "PRIVATE" if use_private_key else "PUBLIC"
            logger.info(f"Wompi API: {method} {endpoint} (Auth: {key_type})")

            if method.upper() == 'GET':
                response = self._session.get(url, headers=headers, params=data, timeout=self.timeout)
            elif method.upper() == 'POST':
                response = self._session.post(url, headers=headers, json=data, timeout=self.timeout)
            else:
                raise WompiAPIException(f"Método HTTP no soportado: {method}")

            logger.info(f"Wompi Response: {response.status_code}")

            # Detectar bloqueo de WAF (respuesta HTML en lugar de JSON)
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type and response.status_code in [403, 503, 429]:
                logger.error(f"BLOQUEADO POR WAF - Status: {response.status_code}")
                raise WompiAPIException(
                    message="Request bloqueado por firewall. Intenta desde otra red o espera unos minutos.",
                    status_code=response.status_code,
                    response_data={'blocked_by_waf': True}
                )

            # Respuesta exitosa
            if response.status_code in [200, 201]:
                return response.json()

            # Error de la API
            try:
                error_data = response.json() if response.text else {}
                error_type = error_data.get('error', {}).get('type', 'UNKNOWN_ERROR')
                error_reason = error_data.get('error', {}).get('reason', 'Error desconocido')
                error_message = f"{error_type}: {error_reason}"
            except:
                error_data = {}
                error_message = f"Error HTTP {response.status_code}: {response.text[:200]}"

            logger.error(f"Wompi API Error: {error_message}")
            raise WompiAPIException(
                message=error_message,
                status_code=response.status_code,
                response_data=error_data
            )

        except requests.exceptions.Timeout:
            logger.error(f"Timeout conectando a Wompi: {url}")
            raise WompiAPIException("Timeout al conectar con Wompi. Intenta de nuevo.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error de conexión: {str(e)}")
            raise WompiAPIException(f"Error de conexión: {str(e)}")

    # ==========================================
    # ACCEPTANCE TOKEN (Obligatorio para transacciones)
    # ==========================================

    def get_acceptance_token(self) -> Dict:
        """
        Obtener el token de aceptación de términos y condiciones

        OBLIGATORIO: Debe obtenerse antes de crear cualquier transacción

        Returns:
            {
                "data": {
                    "presigned_acceptance": {
                        "acceptance_token": "eyJhbGciOiJIUzI1NiJ9...",
                        "permalink": "https://wompi.co/assets/downloadble/...",
                        "type": "END_USER_POLICY"
                    }
                }
            }
        """
        return self._make_request('GET', f'/merchants/{self.public_key}', use_private_key=False)

    # ==========================================
    # TOKENIZACIÓN DE TARJETAS
    # Fuente: https://docs.wompi.co/docs/colombia/metodos-de-pago/#tarjetas
    # ==========================================

    def tokenize_card(
        self,
        card_number: str,
        cvc: str,
        exp_month: str,
        exp_year: str,
        card_holder: str
    ) -> Dict:
        """
        Tokenizar una tarjeta de crédito/débito

        NUNCA guardes información de tarjetas - usa siempre tokenización

        Args:
            card_number: Número de la tarjeta (ej: "4242424242424242")
            cvc: Código de seguridad (3-4 dígitos)
            exp_month: Mes de expiración (formato: "12")
            exp_year: Año de expiración (formato: "29")
            card_holder: Nombre del titular

        Returns:
            {
                "status": "CREATED",
                "data": {
                    "id": "tok_prod_1_BBb749EAB32e97a2D058Dd538a608301",
                    "brand": "VISA",
                    "last_four": "4242",
                    ...
                }
            }
        """
        data = {
            "number": card_number.replace(" ", ""),  # Quitar espacios
            "cvc": cvc,
            "exp_month": exp_month.zfill(2),  # Asegurar 2 dígitos
            "exp_year": exp_year[-2:],  # Solo últimos 2 dígitos
            "card_holder": card_holder.upper()
        }

        return self._make_request('POST', '/tokens/cards', data=data, use_private_key=False)

    # ==========================================
    # CREAR TRANSACCIONES
    # ==========================================

    def create_transaction(
        self,
        amount_in_cents: int,
        currency: str,
        customer_email: str,
        payment_method: Dict,
        reference: str,
        acceptance_token: str,
        signature: Optional[str] = None,
        redirect_url: Optional[str] = None,
        customer_data: Optional[Dict] = None,
        shipping_address: Optional[Dict] = None,
        expiration_time: Optional[str] = None,
        payment_source_id: Optional[int] = None
    ) -> Dict:
        """
        Crear una nueva transacción

        Args:
            amount_in_cents: Monto en centavos (ej: 5000000 = $50,000 COP)
            currency: Moneda ("COP")
            customer_email: Email del cliente
            payment_method: Objeto con detalles del método de pago
            reference: Referencia única de la transacción
            acceptance_token: Token de aceptación (obligatorio)
            signature: Firma de integridad (se calcula automáticamente si no se proporciona)
            redirect_url: URL de redirección después del pago
            customer_data: Datos adicionales del cliente (requerido para PSE)
            shipping_address: Dirección de envío
            expiration_time: Tiempo de expiración (ISO 8601)
            payment_source_id: ID de fuente de pago (para cobros recurrentes)

        Returns:
            Respuesta con datos de la transacción creada
        """
        # Calcular firma si no se proporciona y tenemos integrity_key
        if not signature and self.integrity_key:
            signature = self._calculate_signature(reference, amount_in_cents, currency)

        data = {
            "amount_in_cents": amount_in_cents,
            "currency": currency,
            "customer_email": customer_email,
            "payment_method": payment_method,
            "reference": reference,
            "acceptance_token": acceptance_token,
        }

        # Campos opcionales
        if signature:
            data["signature"] = signature
        if redirect_url:
            data["redirect_url"] = redirect_url
        if customer_data:
            data["customer_data"] = customer_data
        if shipping_address:
            data["shipping_address"] = shipping_address
        if expiration_time:
            data["expiration_time"] = expiration_time
        if payment_source_id:
            data["payment_source_id"] = payment_source_id

        # Usar llave privada SOLO con payment_source_id (fuentes de pago)
        use_private = payment_source_id is not None

        return self._make_request('POST', '/transactions', data=data, use_private_key=use_private)

    # ==========================================
    # CONSULTAR TRANSACCIONES
    # ==========================================

    def get_transaction(self, transaction_id: str) -> Dict:
        """
        Obtener información de una transacción por su ID

        Usar para verificar el estado final de una transacción (long polling)
        Estados posibles: PENDING, APPROVED, DECLINED, VOIDED, ERROR

        Args:
            transaction_id: ID de la transacción (ej: "1234-1610641025-49201")

        Returns:
            Información completa de la transacción
        """
        return self._make_request('GET', f'/transactions/{transaction_id}', use_private_key=True)

    # ==========================================
    # PSE - INSTITUCIONES FINANCIERAS
    # Fuente: https://docs.wompi.co/docs/colombia/metodos-de-pago/#pse
    # ==========================================

    def get_pse_financial_institutions(self) -> List[Dict]:
        """
        Obtener lista de instituciones financieras disponibles para PSE

        OBLIGATORIO obtener esta lista antes de mostrar opciones de banco al usuario

        Returns:
            Lista de bancos con código y nombre:
            [
                {"financial_institution_code": "1051", "financial_institution_name": "Bancolombia"},
                {"financial_institution_code": "1001", "financial_institution_name": "Banco de Bogotá"},
                ...
            ]
        """
        response = self._make_request('GET', '/pse/financial_institutions', use_private_key=False)
        return response.get('data', [])

    # ==========================================
    # MÉTODOS AUXILIARES
    # ==========================================

    def is_sandbox(self) -> bool:
        """Verificar si estamos en ambiente sandbox"""
        return 'test' in self.public_key.lower() or self.environment.lower() == 'sandbox'

    def get_test_data(self, payment_type: str, approved: bool = True) -> str:
        """
        Obtener datos de prueba para sandbox según el tipo de pago

        Args:
            payment_type: "NEQUI", "CARD", "PSE"
            approved: True para datos que generan APPROVED, False para DECLINED

        Returns:
            Dato de prueba correspondiente
        """
        if payment_type == "NEQUI":
            return self.SANDBOX_NEQUI_APPROVED if approved else self.SANDBOX_NEQUI_DECLINED
        elif payment_type == "CARD":
            return self.SANDBOX_CARD_APPROVED if approved else self.SANDBOX_CARD_DECLINED
        elif payment_type == "PSE":
            return self.SANDBOX_PSE_BANK_APPROVED if approved else self.SANDBOX_PSE_BANK_DECLINED
        return ""

    # ==========================================
    # BUILDERS DE PAYMENT_METHOD
    # ==========================================

    @staticmethod
    def build_card_payment_method(token: str, installments: int = 1) -> Dict:
        """
        Construir objeto payment_method para tarjeta

        Args:
            token: Token de la tarjeta (obtenido de tokenize_card)
            installments: Número de cuotas (1-36)
        """
        return {
            "type": "CARD",
            "token": token,
            "installments": installments
        }

    @staticmethod
    def build_nequi_payment_method(phone_number: str) -> Dict:
        """
        Construir objeto payment_method para Nequi

        Args:
            phone_number: Número de celular Nequi (10 dígitos)
                         Sandbox: 3991111111 (APPROVED), 3992222222 (DECLINED)
        """
        return {
            "type": "NEQUI",
            "phone_number": phone_number
        }

    @staticmethod
    def build_pse_payment_method(
        financial_institution_code: str,
        user_type: int,
        user_legal_id_type: str,
        user_legal_id: str,
        payment_description: str
    ) -> Dict:
        """
        Construir objeto payment_method para PSE

        Args:
            financial_institution_code: Código del banco (de get_pse_financial_institutions)
                                       Sandbox: "1" (APPROVED), "2" (DECLINED)
            user_type: 0 = Persona Natural, 1 = Persona Jurídica
            user_legal_id_type: "CC", "CE", "NIT", "TI", "PP"
            user_legal_id: Número de documento
            payment_description: Descripción del pago (máximo 30 caracteres)
        """
        return {
            "type": "PSE",
            "user_type": user_type,
            "user_legal_id_type": user_legal_id_type,
            "user_legal_id": user_legal_id,
            "financial_institution_code": financial_institution_code,
            "payment_description": payment_description[:30]  # Máximo 30 caracteres
        }

    @staticmethod
    def build_bancolombia_transfer_payment_method(payment_description: str) -> Dict:
        """
        Construir objeto payment_method para Botón Bancolombia

        Args:
            payment_description: Descripción del pago (máximo 64 caracteres)
        """
        return {
            "type": "BANCOLOMBIA_TRANSFER",
            "payment_description": payment_description[:64]  # Máximo 64 caracteres
        }

    @staticmethod
    def build_customer_data(phone_number: str, full_name: str) -> Dict:
        """
        Construir objeto customer_data (requerido para PSE)

        Args:
            phone_number: Teléfono con código de país (ej: "573145678901")
            full_name: Nombre completo del cliente
        """
        return {
            "phone_number": phone_number,
            "full_name": full_name
        }

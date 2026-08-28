from dataclasses import dataclass, field
from decimal import Decimal


FUENTE_MIDECISOR = 'midecisor'
FUENTE_HISTORIAL_CREDITO = 'historial_credito'
FUENTE_NO_CONFIGURADO = 'no_configurado'

NIVEL_RIESGO_BAJO = 'BAJO'
NIVEL_RIESGO_MEDIO = 'MEDIO'
NIVEL_RIESGO_ALTO = 'ALTO'
NIVEL_RIESGO_NO_DISPONIBLE = 'NO_DISPONIBLE'

ESTADO_EXITOSA_CON_INFORMACION = 'EXITOSA_CON_INFORMACION'
ESTADO_EXITOSA_SIN_INFORMACION = 'EXITOSA_SIN_INFORMACION'
ESTADO_IDENTIFICACION_NO_ENCONTRADA = 'IDENTIFICACION_NO_ENCONTRADA'
ESTADO_APELLIDO_NO_COINCIDE = 'APELLIDO_NO_COINCIDE'
ESTADO_ERROR_CREDENCIAL_SERVICIO = 'ERROR_CREDENCIAL_SERVICIO'
ESTADO_CONFIGURACION_BLOQUEADA = 'CONFIGURACION_BLOQUEADA'
ESTADO_CONFIGURACION_VENCIDA = 'CONFIGURACION_VENCIDA'
ESTADO_ERROR_TEMPORAL = 'ERROR_TEMPORAL'
ESTADO_ERROR_TECNICO = 'ERROR_TECNICO'

FUENTE_SCORE_MIDECISOR = 'MIDECISOR'
FUENTE_SCORE_HISTORIA_CREDITO = 'HISTORIA_CREDITO'


def enmascarar_valor(valor, visibles=4):
    if not valor:
        return ''
    texto = str(valor)
    if len(texto) <= visibles:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - visibles)}{texto[-visibles:]}"


TIPOS_IDENTIFICACION_MIDECISOR = {
    'CC': '1',
    'CEDULA': '1',
    'CEDULA_CIUDADANIA': '1',
    'CEDULA DE CIUDADANIA': '1',
    '1': '1',
}


def homologar_tipo_identificacion_midecisor(tipo_identificacion):
    valor = str(tipo_identificacion or '').strip().upper()
    return TIPOS_IDENTIFICACION_MIDECISOR.get(valor, str(tipo_identificacion or '').strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class CredencialesDatacredito:
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)
    api_password: str = field(default='', repr=False)
    product_id: str = ''
    info_account_type: str = '1'
    server_ip_address: str = ''

    def validar_para_token(self):
        faltantes = [
            nombre for nombre, valor in (
                ('DATACREDITO_CLIENT_ID', self.client_id),
                ('DATACREDITO_CLIENT_SECRET', self.client_secret),
                ('DATACREDITO_USERNAME', self.username),
                ('DATACREDITO_PASSWORD', self.password),
            )
            if not valor
        ]
        return faltantes

    def validar_para_historial(self):
        faltantes = self.validar_para_token()
        for nombre, valor in (
            ('DATACREDITO_API_PASSWORD', self.api_password),
            ('DATACREDITO_PRODUCT_ID', self.product_id),
            ('DATACREDITO_SERVER_IP_ADDRESS', self.server_ip_address),
        ):
            if not valor:
                faltantes.append(nombre)
        return faltantes


@dataclass(frozen=True, slots=True, kw_only=True)
class CredencialesOAuthDatacredito:
    servicio: str
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)

    def validar_para_token(self):
        prefijo = 'DATACREDITO_DECISOR' if self.servicio == 'decisor' else 'DATACREDITO_HDC'
        faltantes = [
            nombre for nombre, valor in (
                (f'{prefijo}_CLIENT_ID', self.client_id),
                (f'{prefijo}_CLIENT_SECRET', self.client_secret),
                (f'{prefijo}_USERNAME', self.username),
                (f'{prefijo}_PASSWORD', self.password),
            )
            if not valor
        ]
        return faltantes


@dataclass(frozen=True, slots=True, kw_only=True)
class CredencialesOAuthDecisor:
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)
    servicio: str = 'decisor'

    def validar_para_token(self):
        return CredencialesOAuthDatacredito(
            servicio=self.servicio,
            client_id=self.client_id,
            client_secret=self.client_secret,
            username=self.username,
            password=self.password,
        ).validar_para_token()


@dataclass(frozen=True, slots=True, kw_only=True)
class CredencialesOAuthHistorial:
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)
    servicio: str = 'historial'

    def validar_para_token(self):
        return CredencialesOAuthDatacredito(
            servicio=self.servicio,
            client_id=self.client_id,
            client_secret=self.client_secret,
            username=self.username,
            password=self.password,
        ).validar_para_token()


@dataclass(frozen=True, slots=True, kw_only=True)
class CredencialesServicioHistorial:
    user: str
    password: str = field(repr=False)
    product_id: str = '64'
    info_account_type: str = '1'
    server_ip_address: str = ''
    channel_name: str = 'Canal-01'
    channel_type: str = '42'

    def validar_para_historial(self):
        faltantes = []
        for nombre, valor in (
            ('DATACREDITO_HDC_SERVICE_USER', self.user),
            ('DATACREDITO_HDC_SERVICE_PASSWORD', self.password),
            ('DATACREDITO_HDC_PRODUCT_ID', self.product_id),
            ('DATACREDITO_HDC_INFO_ACCOUNT_TYPE', self.info_account_type),
            ('DATACREDITO_HDC_SERVER_IP_ADDRESS', self.server_ip_address),
            ('DATACREDITO_HDC_CHANNEL_NAME', self.channel_name),
            ('DATACREDITO_HDC_CHANNEL_TYPE', self.channel_type),
        ):
            if not valor:
                faltantes.append(nombre)
        return faltantes


@dataclass(frozen=True)
class TokenDatacredito:
    access_token: str = field(repr=False)
    token_type: str = 'Bearer'
    expires_in: int = 0
    metadata_segura: dict = field(default_factory=dict)

    @property
    def authorization_header(self):
        return f"{self.token_type or 'Bearer'} {self.access_token}"

    def como_dict_seguro(self):
        return {
            'access_token': enmascarar_valor(self.access_token),
            'token_type': self.token_type,
            'expires_in': self.expires_in,
            'metadata_segura': self.metadata_segura,
        }


@dataclass(frozen=True)
class EntradaMiDecisor:
    tipo_identificacion: str
    numero_identificacion: str
    apellido_razon_social: str

    def como_payload(self):
        return {
            'tipoIdentificacion': homologar_tipo_identificacion_midecisor(self.tipo_identificacion),
            'numeroIdentificacion': self.numero_identificacion,
            'apellidoRazonSocial': self.apellido_razon_social,
        }


@dataclass(frozen=True)
class EntradaHistorialCredito:
    tipo_identificacion: str
    numero_identificacion: str
    apellido: str
    request_uuid: str | None = None
    fecha_hora: str | None = None
    canal_origen_nombre: str | None = None
    canal_origen_tipo: str | None = None
    parametros: tuple[dict, ...] = field(default_factory=tuple)
    user_ip_address: str | None = None


@dataclass(frozen=True)
class ResultadoMiDecisorRawSeguro:
    status_code: int
    response_code: str | None = None
    codigo_funcional: str | None = None
    fuente: str = FUENTE_MIDECISOR
    raw_sanitizado: dict = field(default_factory=dict)
    metadata_segura: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoHistorialCreditoRawSeguro:
    status_code: int
    response_code: str | None = None
    fuente: str = FUENTE_HISTORIAL_CREDITO
    raw_sanitizado: dict = field(default_factory=dict)
    metadata_segura: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoDatacreditoNormalizado:
    disponible: bool
    fuente: str
    servicio: str = ''
    estado: str = ESTADO_ERROR_TECNICO
    con_informacion: bool | None = None
    codigo_respuesta: str | None = None
    descripcion_respuesta: str | None = None
    score: int | None = None
    score_midecisor: int | None = None
    fuente_score: str | None = None
    scores_hdc: tuple[dict, ...] = field(default_factory=tuple)
    score_normalizado_0_1000: int | None = None
    viable: bool | None = None
    monto_sugerido: int | None = None
    saldo_actual: Decimal | None = None
    saldo_mora: Decimal | None = None
    valor_cuota_total: Decimal | None = None
    creditos_vigentes: int | None = None
    creditos_cerrados: int | None = None
    porcentaje_deuda: Decimal | None = None
    ingreso_estimado: Decimal | None = None
    porcentaje_cuota_vs_ingreso: Decimal | None = None
    nivel_riesgo: str = NIVEL_RIESGO_NO_DISPONIBLE
    mora_severa: bool | None = None
    mora_actual: bool | None = None
    embargos: bool | None = None
    liquidacion: bool | None = None
    response_code: str | None = None
    viabilidad: str | None = None
    rating_recaudo: str | None = None
    cantidad_alertas: int = 0
    requiere_revision_cumplimiento: bool = False
    bloqueo_automatico: bool = False
    requiere_revision_manual: bool = False
    error_tipo: str | None = None
    alertas_resumen: tuple[str, ...] = field(default_factory=tuple)
    metadata_segura: dict = field(default_factory=dict)
    metadata_sanitizada: dict = field(default_factory=dict)

    def como_dict(self):
        return {
            'servicio': self.servicio,
            'estado': self.estado,
            'disponible': self.disponible,
            'con_informacion': self.con_informacion,
            'codigo_respuesta': self.codigo_respuesta,
            'descripcion_respuesta': self.descripcion_respuesta,
            'fuente': self.fuente,
            'score': self.score,
            'score_midecisor': self.score_midecisor,
            'fuente_score': self.fuente_score,
            'scores_hdc': list(self.scores_hdc),
            'score_normalizado_0_1000': self.score_normalizado_0_1000,
            'viable': self.viable,
            'monto_sugerido': self.monto_sugerido,
            'saldo_actual': str(self.saldo_actual) if self.saldo_actual is not None else None,
            'saldo_mora': self.saldo_mora,
            'valor_cuota_total': str(self.valor_cuota_total) if self.valor_cuota_total is not None else None,
            'creditos_vigentes': self.creditos_vigentes,
            'creditos_cerrados': self.creditos_cerrados,
            'porcentaje_deuda': str(self.porcentaje_deuda) if self.porcentaje_deuda is not None else None,
            'ingreso_estimado': str(self.ingreso_estimado) if self.ingreso_estimado is not None else None,
            'porcentaje_cuota_vs_ingreso': (
                str(self.porcentaje_cuota_vs_ingreso) if self.porcentaje_cuota_vs_ingreso is not None else None
            ),
            'nivel_riesgo': self.nivel_riesgo,
            'mora_severa': self.mora_severa,
            'mora_actual': self.mora_actual,
            'embargos': self.embargos,
            'liquidacion': self.liquidacion,
            'response_code': self.response_code,
            'viabilidad': self.viabilidad,
            'rating_recaudo': self.rating_recaudo,
            'cantidad_alertas': self.cantidad_alertas,
            'requiere_revision_cumplimiento': self.requiere_revision_cumplimiento,
            'bloqueo_automatico': self.bloqueo_automatico,
            'requiere_revision_manual': self.requiere_revision_manual,
            'error_tipo': self.error_tipo,
            'alertas_resumen': list(self.alertas_resumen),
            'metadata_segura': self.metadata_segura or self.metadata_sanitizada,
            'metadata_sanitizada': self.metadata_sanitizada or self.metadata_segura,
        }

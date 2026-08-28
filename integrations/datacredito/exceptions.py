class DatacreditoError(Exception):
    """Error base de la integracion DataCredito."""

    def __init__(
        self,
        mensaje='Error DataCredito.',
        *,
        servicio=None,
        etapa=None,
        http_status=None,
        codigo_funcional=None,
        error_tipo=None,
        causa_clase=None,
    ):
        super().__init__(mensaje)
        self.servicio = servicio
        self.etapa = etapa
        self.http_status = http_status
        self.codigo_funcional = codigo_funcional
        self.error_tipo = error_tipo
        self.causa_clase = causa_clase


class DatacreditoConfigError(DatacreditoError):
    """Configuracion incompleta o invalida."""


class DatacreditoAuthError(DatacreditoError):
    """Error de autenticacion OAuth2."""


class DatacreditoProviderDisabled(DatacreditoError):
    """El consumo real esta apagado por configuracion."""


class DatacreditoProviderError(DatacreditoError):
    """Error controlado del proveedor."""


class DatacreditoTimeoutError(DatacreditoProviderError):
    """Timeout en consumo al proveedor."""

from integrations.datacredito.exceptions import DatacreditoConfigError


# Codigos comunes: manual HDC tabla 1 y Swagger MiDecisor ConsultaRequest.
# Codigo 6 difiere entre servicios (PPT/CD); no se homologa como tipo comun.
TIPOS_IDENTIFICACION = {'CC': '1', 'NIT': '2', 'PJE': '3', 'CE': '4',
                        'PAS': '5', 'TI': '7', 'DNI': '8', 'PEP': '9'}
ALIASES = {'1': 'CC', '4': 'CE', 'CEDULA': 'CC', 'CEDULA_CIUDADANIA': 'CC',
           'CEDULA DE CIUDADANIA': 'CC', 'PP': 'PAS',
           **{codigo: tipo for tipo, codigo in TIPOS_IDENTIFICACION.items()}}


def homologar_tipo_identificacion(tipo):
    valor = str(tipo or '').strip().upper()
    valor = ALIASES.get(valor, valor)
    if valor not in TIPOS_IDENTIFICACION:
        raise DatacreditoConfigError('Tipo de identificacion no soportado para Prestadores.')
    return TIPOS_IDENTIFICACION[valor]

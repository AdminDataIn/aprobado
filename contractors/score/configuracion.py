CONFIGURACION_SCORE_PRESTADORES_V1 = {
    'version': 'prestadores_score_v1',
    'calcular_score_parcial_sin_datacredito': True,
    'componentes': {
        'datacredito': {
            'peso': '0.45',
            'valor_default': None,
            'estado_si_no_disponible': 'PENDIENTE',
        },
        'capacidad': {
            'peso': '0.30',
            'valor_default': None,
            'metrica': 'uso_capacidad_contractual',
            'puntos_por_uso': [
                {'maximo_ratio': '0.25', 'score': 900},
                {'maximo_ratio': '0.50', 'score': 820},
                {'maximo_ratio': '0.75', 'score': 740},
                {'maximo_ratio': '1.00', 'score': 660},
            ],
        },
        'comportamiento_digital': {
            'peso': '0.08',
            'valor_default': 750,
        },
        'riesgo_fraude': {
            'peso': '0.12',
            'valor_default': 750,
        },
        'referencias': {
            'peso': '0.05',
            'valor_default': 750,
        },
        'geolocalizacion': {
            'peso': '0.00',
            'valor_default': None,
            'penaliza': True,
            'umbral_penalizacion': 600,
            'penalizacion': -80,
        },
    },
    'bandas': [
        {
            'nombre': 'PREMIUM',
            'minimo': 850,
            'maximo': 1000,
            'monto_maximo': '2000000.00',
            'plazo_maximo_meses': 6,
            'decision': 'APROBACION_DIRECTA_READ_ONLY',
        },
        {
            'nombre': 'ALTA',
            'minimo': 750,
            'maximo': 849,
            'monto_maximo': '1500000.00',
            'plazo_maximo_meses': 6,
            'decision': 'APROBACION_ESTANDAR_READ_ONLY',
        },
        {
            'nombre': 'MEDIA',
            'minimo': 680,
            'maximo': 749,
            'monto_maximo': '1000000.00',
            'plazo_maximo_meses': 4,
            'decision': 'OFERTA_PRUDENTE_READ_ONLY',
        },
        {
            'nombre': 'ENTRADA',
            'minimo': 600,
            'maximo': 679,
            'monto_maximo': '500000.00',
            'plazo_maximo_meses': 3,
            'decision': 'APROBADO_CONDICIONADO_READ_ONLY',
        },
        {
            'nombre': 'REVISION',
            'minimo': 0,
            'maximo': 599,
            'monto_maximo': '0.00',
            'plazo_maximo_meses': 0,
            'decision': 'REVISION_MANUAL_READ_ONLY',
        },
    ],
    'reglas_criticas': {
        'cuota_ingreso_maximo': '0.25',
        'mora_severa_bloquea': True,
        'datacredito_mora_severa_bloquea': True,
    },
}


CONFIGURACION_SCORE_PRESTADORES_V2 = {
    'version': 'prestadores_score_v2_2026_06',
    'calcular_score_parcial_sin_datacredito': True,
    'producto': {
        'monto_maximo': '10000000.00',
        'plazo_maximo_meses': 8,
        'tasa_mensual': '2.2000',
    },
    'componentes': {
        'datacredito': {
            'peso': '0.45',
            'valor_default': None,
            'estado_si_no_disponible': 'PENDIENTE',
        },
        'capacidad': {
            'peso': '0.30',
            'valor_default': None,
            'metrica': 'uso_capacidad_contractual',
            'puntos_por_uso': [
                {'maximo_ratio': '0.25', 'score': 900},
                {'maximo_ratio': '0.50', 'score': 820},
                {'maximo_ratio': '0.75', 'score': 740},
                {'maximo_ratio': '1.00', 'score': 660},
            ],
        },
        'comportamiento_digital': {
            'peso': '0.08',
            'valor_default': 750,
        },
        'riesgo_fraude': {
            'peso': '0.12',
            'valor_default': 750,
        },
        'referencias': {
            'peso': '0.05',
            'valor_default': 750,
        },
        'geolocalizacion': {
            'peso': '0.00',
            'valor_default': None,
            'penaliza': True,
            'umbral_penalizacion': 600,
            'penalizacion': -80,
        },
    },
    'bandas': [
        {
            'nombre': 'PREMIUM',
            'minimo': 850,
            'maximo': 1000,
            'monto_maximo': '10000000.00',
            'plazo_maximo_meses': 8,
            'decision': 'APROBACION_DIRECTA_READ_ONLY',
        },
        {
            'nombre': 'ALTA',
            'minimo': 750,
            'maximo': 849,
            'monto_maximo': '8000000.00',
            'plazo_maximo_meses': 8,
            'decision': 'APROBACION_ESTANDAR_READ_ONLY',
        },
        {
            'nombre': 'MEDIA',
            'minimo': 680,
            'maximo': 749,
            'monto_maximo': '5000000.00',
            'plazo_maximo_meses': 8,
            'decision': 'OFERTA_PRUDENTE_READ_ONLY',
        },
        {
            'nombre': 'ENTRADA',
            'minimo': 600,
            'maximo': 679,
            'monto_maximo': '3000000.00',
            'plazo_maximo_meses': 6,
            'decision': 'APROBADO_CONDICIONADO_READ_ONLY',
        },
        {
            'nombre': 'REVISION',
            'minimo': 0,
            'maximo': 599,
            'monto_maximo': '0.00',
            'plazo_maximo_meses': 0,
            'decision': 'REVISION_MANUAL_READ_ONLY',
        },
    ],
    'reglas_criticas': {
        'cuota_ingreso_maximo': '0.30',
        'mora_severa_bloquea': True,
        'datacredito_mora_severa_bloquea': True,
    },
}


CONFIGURACIONES_SCORE_PRESTADORES = {
    CONFIGURACION_SCORE_PRESTADORES_V1['version']: CONFIGURACION_SCORE_PRESTADORES_V1,
    CONFIGURACION_SCORE_PRESTADORES_V2['version']: CONFIGURACION_SCORE_PRESTADORES_V2,
}


class ErrorConfiguracionScorePrestadores(ValueError):
    pass


def obtener_configuracion_score_prestadores(version=None):
    if version is None:
        from django.conf import settings

        version = getattr(
            settings,
            'CONTRACTORS_SCORE_CONFIG_VERSION',
            CONFIGURACION_SCORE_PRESTADORES_V2['version'],
        )

    try:
        return CONFIGURACIONES_SCORE_PRESTADORES[version]
    except KeyError as exc:
        raise ErrorConfiguracionScorePrestadores(
            f'configuracion_score_prestadores_no_existe:{version}'
        ) from exc

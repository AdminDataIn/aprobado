"""Local configuration checks only: no database, network, OAuth or DNS."""
from django.conf import settings

from contractors.services.autorizacion_datacredito import obtener_configuracion_autorizacion_datacredito
from integrations.datacredito.settings import (
    HISTORIAL_URLS, MIDECISOR_URLS, REVOKE_TOKEN_URLS, TOKEN_URLS,
    obtener_configuracion_datacredito, secreto_documental_valido,
)


def _requisito(nombre, configurado, mensaje, *, critico=True, estado=None, **publicos):
    return dict(nombre=nombre, configurado=bool(configurado), critico=critico,
                mensaje=mensaje, estado=estado or ('OK' if configurado else 'FALTA_O_INVALIDO'), **publicos)


def _presencia(nombre):
    presente = bool(str(getattr(settings, nombre, '') or '').strip())
    return _requisito(nombre, presente, 'Configurado.' if presente else 'Falta configuracion explicita.')


def _url(nombre, efectiva, esperada):
    coincide = efectiva == esperada
    # Never echo an untrusted URL: it could contain credentials or query secrets.
    return _requisito(nombre, coincide, 'URL contractual.' if coincide else 'URL no coincide con la esperada.',
                      **({'url': efectiva} if coincide else {}))


def evaluar_readiness_datacredito():
    configuracion = obtener_configuracion_datacredito()
    consentimiento = obtener_configuracion_autorizacion_datacredito()
    ambiente = str(getattr(settings, 'DATACREDITO_ENVIRONMENT', '') or '').strip().lower()
    secciones = {
        'GLOBAL': [
            _requisito('DATACREDITO_DOCUMENT_HASH_SECRET',
                       secreto_documental_valido(configuracion.document_hash_secret),
                       'Requiere al menos 32 bytes y generacion criptografica; no se evalua entropia.'),
            _requisito('DATACREDITO_ENVIRONMENT', ambiente in {'uat', 'prod', 'production', 'produccion'},
                       'Ambiente reconocido; DEMO contratado por Aprobado utiliza URLs UAT.'),
            _url('DATACREDITO_TOKEN_URL', configuracion.token_url, TOKEN_URLS[configuracion.environment]),
            _url('DATACREDITO_REVOKE_TOKEN_URL', configuracion.revoke_token_url,
                 REVOKE_TOKEN_URLS[configuracion.environment]),
        ],
        'CONSENTIMIENTO': [
            _requisito('CONSENTIMIENTO_TEXTO_CANONICO', bool(consentimiento.texto),
                       'Cuerpo legal disponible en codigo.' if consentimiento.texto else 'Falta cuerpo legal canonico.'),
            _requisito('CONSENTIMIENTO_VERSION_CANONICA', bool(consentimiento.version_texto),
                       'Version disponible en codigo.' if consentimiento.version_texto else 'Falta version canonica.'),
            _requisito('CONSENTIMIENTO_SHA256', bool(consentimiento.texto_hash),
                       'SHA256 del cuerpo canonico calculable.' if consentimiento.texto_hash else 'SHA256 no disponible.'),
            _requisito('DATACREDITO_AUTHORIZATION_TEXT', consentimiento.texto_override_compatible,
                       'Configuracion coincide exactamente con codigo.' if consentimiento.texto_override_compatible
                       else 'Override de texto difiere del canonico; consentimiento bloqueado.'),
            _requisito('DATACREDITO_AUTHORIZATION_TEXT_VERSION', consentimiento.version_override_compatible,
                       'Configuracion coincide exactamente con codigo.' if consentimiento.version_override_compatible
                       else 'Override de version difiere de la canonica; consentimiento bloqueado.'),
        ],
        'MIDECISOR': [_presencia('DATACREDITO_DECISOR_' + campo) for campo in (
            'CLIENT_ID', 'CLIENT_SECRET', 'TOKEN_USERNAME', 'TOKEN_PASSWORD',
        )] + [_url('DATACREDITO_MIDECISOR_URL', configuracion.midecisor_url,
                  MIDECISOR_URLS[configuracion.environment])],
        'HDC': [_presencia('DATACREDITO_HDC_' + campo) for campo in (
            'CLIENT_ID', 'CLIENT_SECRET', 'TOKEN_USERNAME', 'TOKEN_PASSWORD',
            'SERVICE_USER', 'SERVICE_PASSWORD', 'SERVER_IP_ADDRESS',
        )] + [_url('DATACREDITO_HISTORIAL_URL', configuracion.historial_url,
                  HISTORIAL_URLS[configuracion.environment])],
        'FLAGS': [],
    }
    for campo in ('PRODUCT_ID', 'INFO_ACCOUNT_TYPE'):
        nombre = 'DATACREDITO_HDC_' + campo
        explicito = bool(getattr(settings, nombre + '_EXPLICIT', False))
        valor = str(getattr(settings, nombre, '') or '').strip()
        valido = explicito and valor.isdigit() and int(valor) > 0
        secciones['HDC'].append(_requisito(
            nombre, valido,
            'Parametro declarado explicitamente; confirmar respaldo contractual.' if valido
            else 'Parametro contractual pendiente de confirmacion explicita.',
            estado='CONFIGURADO_EXPLICITAMENTE' if valido else (
                'DEFAULT_NO_CONFIRMADO' if not explicito else 'FALTA_O_INVALIDO'),
        ))
    for campo, esperado in (('CHANNEL_NAME', 'CONEXRED-01'), ('CHANNEL_TYPE', '42')):
        nombre = 'DATACREDITO_HDC_' + campo
        secciones['HDC'].append(_requisito(
            nombre, str(getattr(settings, nombre, '') or '').strip() == esperado,
            'Debe coincidir con el canal contractual ' + esperado + '.',
        ))
    secciones['HDC'].append(_requisito(
        'DATACREDITO_HDC_PARAMETERS_JSON', not configuracion.parametros_historial_error,
        'Parametros locales validos o no requeridos; confirmar contrato.',
    ))
    for nombre in ('DATACREDITO_ENABLED', 'DATACREDITO_REAL_ENABLED'):
        habilitado = bool(getattr(settings, nombre, False))
        secciones['FLAGS'].append(_requisito(
            nombre, True, 'Flag habilitado.' if habilitado else 'Flag apagado.',
            critico=False, habilitado=habilitado,
        ))
    completa = all(r['configurado'] for requisitos in secciones.values()
                   for r in requisitos if r['critico'])
    habilitado = configuracion.real_enabled
    estado = ('CONFIGURADO_PERO_DESHABILITADO' if completa else 'INCOMPLETO_Y_DESHABILITADO') if not habilitado else (
        'CONFIGURADO_Y_HABILITADO' if completa else 'INCOMPLETO_Y_HABILITADO')
    return dict(secciones=secciones, configuracion_completa=completa, habilitado=habilitado,
                estado=estado, resultado='LISTO_CONFIGURACION' if completa else 'NO_LISTO')

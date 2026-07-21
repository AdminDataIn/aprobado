import requests
import uuid
from zoneinfo import ZoneInfo

from django.utils import timezone

from integrations.datacredito.auth import SERVICIO_HISTORIAL, obtener_token_cacheado, validar_consumo_real_habilitado
from integrations.datacredito.dto import EntradaHistorialCredito, ResultadoHistorialCreditoRawSeguro
from integrations.datacredito.exceptions import (
    DatacreditoConfigError,
    DatacreditoProviderError,
    DatacreditoTimeoutError,
)
from integrations.datacredito.http import crear_session_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito


def consultar_historial_credito(entrada: EntradaHistorialCredito, session=None):
    configuracion = validar_consumo_real_habilitado(obtener_configuracion_datacredito())
    if configuracion.parametros_historial_error:
        raise DatacreditoConfigError(configuracion.parametros_historial_error)
    faltantes = (
        configuracion.credenciales_historial.validar_para_token()
        + configuracion.credenciales_servicio_historial.validar_para_historial()
    )
    if faltantes:
        raise DatacreditoConfigError(
            f"Faltan credenciales DataCredito historial: {', '.join(faltantes)}"
        )

    cliente_http = session if session is not None else crear_session_datacredito(configuracion)
    token = obtener_token_cacheado(servicio=SERVICIO_HISTORIAL, session=cliente_http)
    headers = {
        'Authorization': token.authorization_header,
        'Content-Type': 'application/json',
        'serverIpAddress': configuracion.credenciales_servicio_historial.server_ip_address,
        'ProductId': configuracion.credenciales_servicio_historial.product_id,
        'InfoAccountType': configuracion.credenciales_servicio_historial.info_account_type,
        'client_id': configuracion.credenciales_historial.client_id,
        'client_secret': configuracion.credenciales_historial.client_secret,
    }
    if entrada.user_ip_address:
        headers['userIpAddress'] = entrada.user_ip_address

    payload = _construir_payload_historial(entrada, configuracion)

    try:
        respuesta = cliente_http.post(
            configuracion.historial_url,
            json=payload,
            headers=headers,
            timeout=configuracion.timeout_seconds,
        )
    except requests.exceptions.Timeout as exc:
        raise DatacreditoTimeoutError('Timeout consultando Historia de Credito DataCredito.') from exc
    except requests.exceptions.RequestException as exc:
        raise DatacreditoProviderError('Error de red consultando Historia de Credito DataCredito.') from exc

    if respuesta.status_code >= 400:
        raise DatacreditoProviderError(
            f'Error Historia de Credito DataCredito status={respuesta.status_code}.'
        )

    cuerpo = respuesta.json()
    return ResultadoHistorialCreditoRawSeguro(
        status_code=respuesta.status_code,
        response_code=_buscar_response_code(cuerpo),
        raw_sanitizado=_sanitizar_raw(cuerpo),
        metadata_segura={
            'endpoint': 'historial_credito',
            'status_code': respuesta.status_code,
            'documento_enmascarado': _enmascarar_documento(entrada.numero_identificacion),
        },
    )


def _construir_payload_historial(entrada, configuracion):
    request_uuid = entrada.request_uuid or str(uuid.uuid4())
    fecha_hora = entrada.fecha_hora or timezone.now().astimezone(ZoneInfo('America/Bogota')).isoformat()
    canal_nombre = entrada.canal_origen_nombre or configuracion.credenciales_servicio_historial.channel_name
    canal_tipo = entrada.canal_origen_tipo or configuracion.credenciales_servicio_historial.channel_type
    payload = {
        'user': configuracion.credenciales_servicio_historial.user,
        'password': configuracion.credenciales_servicio_historial.password,
        'identifyingTrx': {
            'requestUUID': request_uuid,
            'dateTime': fecha_hora,
            'originatorChannelName': canal_nombre,
            'originatorChannelType': str(canal_tipo),
        },
        'identifyingUser': {
            'person': {
                'personId': {
                    'personIdNumber': entrada.numero_identificacion,
                    'personIdType': _normalizar_tipo_identificacion(entrada.tipo_identificacion),
                },
                'personLastName': str(entrada.apellido or '').strip().upper(),
            }
        },
    }
    parametros = entrada.parametros or configuracion.parametros_historial
    if parametros:
        payload['parameters'] = [dict(parametro) for parametro in parametros]
    return payload


def _normalizar_tipo_identificacion(tipo_identificacion):
    texto = str(tipo_identificacion or '').strip().upper()
    mapa = {
        'CC': 1,
        '1': 1,
        'CE': 2,
        '2': 2,
        'NIT': 3,
        '3': 3,
        'TI': 4,
        '4': 4,
        'PP': 5,
        '5': 5,
    }
    return mapa.get(texto, texto)


def _sanitizar_raw(cuerpo):
    if not isinstance(cuerpo, dict):
        return {}
    prohibidas = {
        'personIdNumber',
        'personLastName',
        'numeroIdentificacion',
        'identificacion',
        'documento',
        'documentNumber',
        'identificationNumber',
        'accountNumber',
        'account_number',
        'numeroCuenta',
        'numero_cuenta',
        'counterpartyIdNumber',
        'primaryKey',
        'name',
        'fullName',
        'address',
        'direccion',
        'phone',
        'telefono',
        'email',
        'correo',
        'access_token',
        'refresh_token',
        'client_secret',
        'password',
    }
    return _sanitizar_valor(cuerpo, {clave.lower() for clave in prohibidas})


def _sanitizar_valor(valor, prohibidas):
    if isinstance(valor, dict):
        return {
            clave: _sanitizar_valor(subvalor, prohibidas)
            for clave, subvalor in valor.items()
            if str(clave).lower() not in prohibidas
        }
    if isinstance(valor, list):
        return [_sanitizar_valor(item, prohibidas) for item in valor]
    return valor


def _buscar_response_code(cuerpo):
    if not isinstance(cuerpo, dict):
        return None
    for clave in ('responseCode', 'response_code', 'codigoRespuesta'):
        if clave in cuerpo:
            return str(cuerpo[clave])
    return None


def _enmascarar_documento(documento):
    texto = str(documento or '')
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"

import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings

from contractors.models import DOCUMENTOS_OBLIGATORIOS_PRESTADOR


VERSION_POLITICA_EVALUACION = 'prestadores_politica_v1'
VERSION_SCORE_NO_HABILITADO = 'score_no_habilitado_v1'
MODO_EVALUACION_BASE = 'BASE_SIN_SERVICIOS_EXTERNOS'
VERSION_AUTORIZACIONES = 'autorizaciones_prestadores_v1'


def construir_snapshot_entrada_evaluacion(solicitud):
    tipos_documentos = sorted(solicitud.documentos.values_list('tipo_documento', flat=True))
    metadata_analisis = solicitud.metadata_analisis_contractual or {}
    autorizacion_datacredito = solicitud.autorizaciones_datacredito.order_by(
        '-aceptada_en', '-id'
    ).first()
    return {
        'solicitud_id': solicitud.id,
        'empresa_id': solicitud.empresa_id,
        'documento_hash': _hmac_documento(solicitud.numero_documento),
        'documento_enmascarado': _enmascarar_documento(solicitud.numero_documento),
        'datos_solicitante_hash': _hmac_campos(
            solicitud.nombres,
            solicitud.apellidos,
            solicitud.celular,
            solicitud.correo,
            solicitud.direccion,
        ),
        'actividad_contractual_hash': _hmac_campos(solicitud.cargo),
        'monto_solicitado': _decimal_seguro(solicitud.monto_solicitado),
        'plazo_meses': solicitud.plazo_meses,
        'tipo_contrato': solicitud.tipo_contrato,
        'fecha_inicio_contrato': _fecha_segura(solicitud.fecha_inicio_contrato),
        'fecha_fin_contrato': _fecha_segura(solicitud.fecha_fin_contrato),
        'duracion_contrato_meses': solicitud.duracion_contrato_meses,
        'estado_contractual_declarado': solicitud.estado_contractual_declarado,
        'valor_total_contrato': _decimal_seguro(solicitud.valor_total_contrato),
        'valor_pagado_contrato': _decimal_seguro(solicitud.valor_pagado_contrato),
        'valor_pendiente_cobrar': _decimal_seguro(solicitud.valor_pendiente_cobrar),
        'forma_pago': solicitud.forma_pago,
        'frecuencia_pago': solicitud.frecuencia_pago,
        'valor_mensual_contractual': _decimal_seguro(
            solicitud.valor_mensual_contractual
        ),
        'evidencia_forma_pago_hash': _hmac_campos(
            solicitud.evidencia_forma_pago,
            solicitud.fuente_forma_pago,
            solicitud.confianza_forma_pago,
        ),
        'estado_documental': {
            'completo': set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(tipos_documentos),
            'tipos_cargados': tipos_documentos,
        },
        'contrato_hash_sha256': (
            _hash_contrato_guardado(solicitud)
            or str(metadata_analisis.get('archivo_hash_sha256') or '')
        ),
        'estado_analisis_contractual': solicitud.estado_analisis_contractual,
        'configuracion_financiera_simulacion': {
            'version': solicitud.version_configuracion_financiera_simulacion,
            'version_politica': solicitud.version_politica_simulacion,
            'monto_simulado': _decimal_seguro(solicitud.monto_simulado),
            'plazo_simulado_meses': solicitud.plazo_simulado_meses,
            'tasa_mensual': _decimal_seguro(solicitud.tasa_mensual_simulacion),
            'monto_maximo': _decimal_seguro(
                solicitud.monto_maximo_configuracion_simulacion
            ),
            'plazo_maximo_meses': solicitud.plazo_maximo_configuracion_simulacion,
            'simulada_en': (
                solicitud.simulada_en.isoformat() if solicitud.simulada_en else None
            ),
        },
        'autorizaciones': {
            'version': VERSION_AUTORIZACIONES,
            'terminos': bool(solicitud.acepta_terminos),
            'privacidad': bool(solicitud.acepta_politica_privacidad),
            'analisis_contractual': bool(solicitud.autoriza_analisis_contractual_asistido),
            'consulta_centrales': bool(solicitud.autoriza_consulta_centrales),
            'consulta_centrales_version': (
                autorizacion_datacredito.version_texto if autorizacion_datacredito else ''
            ),
            'consulta_centrales_texto_hash': (
                autorizacion_datacredito.texto_hash if autorizacion_datacredito else ''
            ),
        },
    }


def construir_version_datos(solicitud):
    snapshot = construir_snapshot_entrada_evaluacion(solicitud)
    return _sha256_json(snapshot), snapshot


def construir_clave_idempotencia(
    *, solicitud, version_datos, version_politica=VERSION_POLITICA_EVALUACION,
    version_score=VERSION_SCORE_NO_HABILITADO, version_configuracion_financiera='',
    modo_evaluacion=MODO_EVALUACION_BASE,
):
    return _sha256_json({
        'solicitud_id': solicitud.id,
        'version_datos': version_datos,
        'version_politica': version_politica,
        'version_score': version_score,
        'version_configuracion_financiera': version_configuracion_financiera,
        'modo_evaluacion': modo_evaluacion,
    })


def _hash_contrato_guardado(solicitud):
    contrato = solicitud.documentos.filter(tipo_documento='CONTRATO').first()
    if not contrato or not contrato.archivo:
        return ''
    digest = hashlib.sha256()
    try:
        contrato.archivo.open('rb')
        for bloque in contrato.archivo.chunks():
            digest.update(bloque)
        contrato.archivo.close()
    except (FileNotFoundError, OSError, ValueError):
        return ''
    return digest.hexdigest()


def _hmac_documento(documento):
    normalizado = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum()).upper()
    secreto = f'contractors-evaluation:{settings.SECRET_KEY}'.encode('utf-8')
    return hmac.new(secreto, normalizado.encode('utf-8'), hashlib.sha256).hexdigest()


def _hmac_campos(*valores):
    normalizado = '\x1f'.join(str(valor or '').strip() for valor in valores)
    secreto = f'contractors-evaluation:{settings.SECRET_KEY}'.encode('utf-8')
    return hmac.new(secreto, normalizado.encode('utf-8'), hashlib.sha256).hexdigest()


def _enmascarar_documento(documento):
    texto = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum())
    if not texto:
        return ''
    return f"{'*' * max(0, len(texto) - 4)}{texto[-4:]}"


def _sha256_json(datos):
    serializado = json.dumps(datos, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(serializado.encode('utf-8')).hexdigest()


def _decimal_seguro(valor):
    if valor is None:
        return None
    return format(Decimal(valor), 'f')


def _fecha_segura(valor):
    return valor.isoformat() if valor else None

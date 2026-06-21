import base64
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError


CAMPOS_CONTRATO_ESPERADOS = (
    'fecha_inicio_contrato',
    'fecha_fin_contrato',
    'valor_total_contrato',
    'valor_pendiente_estimado',
    'empresa_contratante',
    'cargo_o_servicio',
)

ESQUEMA_ANALISIS_CONTRATO = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'es_contrato': {'type': ['boolean', 'null']},
        'tipo_documento_detectado': {'type': ['string', 'null']},
        'empresa_contratante': {'type': ['string', 'null']},
        'nit_empresa': {'type': ['string', 'null']},
        'nombre_contratista': {'type': ['string', 'null']},
        'documento_contratista': {'type': ['string', 'null']},
        'cargo_o_servicio': {'type': ['string', 'null']},
        'fecha_inicio_contrato': {'type': ['string', 'null']},
        'fecha_fin_contrato': {'type': ['string', 'null']},
        'valor_total_contrato': {'type': ['number', 'string', 'null']},
        'valor_mensual_o_honorarios': {'type': ['number', 'string', 'null']},
        'valor_pagado_estimado': {'type': ['number', 'string', 'null']},
        'valor_pendiente_estimado': {'type': ['number', 'string', 'null']},
        'duracion_meses_contrato': {'type': ['number', 'string', 'null']},
        'moneda': {'type': ['string', 'null']},
        'resumen': {'type': ['string', 'null']},
        'campos_no_encontrados': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'advertencias': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'confianza_general': {'type': ['number', 'string', 'null']},
        'requiere_confirmacion_usuario': {'type': 'boolean'},
    },
    'required': [
        'es_contrato',
        'tipo_documento_detectado',
        'empresa_contratante',
        'nit_empresa',
        'nombre_contratista',
        'documento_contratista',
        'cargo_o_servicio',
        'fecha_inicio_contrato',
        'fecha_fin_contrato',
        'valor_total_contrato',
        'valor_mensual_o_honorarios',
        'valor_pagado_estimado',
        'valor_pendiente_estimado',
        'duracion_meses_contrato',
        'moneda',
        'resumen',
        'campos_no_encontrados',
        'advertencias',
        'confianza_general',
        'requiere_confirmacion_usuario',
    ],
}


class ErrorAnalisisContratoIA(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoAnalisisContratoIAOpenAI:
    es_contrato: bool | None = None
    tipo_documento_detectado: str = ''
    empresa_contratante: str = ''
    nit_empresa: str = ''
    nombre_contratista: str = ''
    documento_contratista: str = ''
    cargo_o_servicio: str = ''
    fecha_inicio_contrato: date | None = None
    fecha_fin_contrato: date | None = None
    valor_total_contrato: Decimal | None = None
    valor_mensual_o_honorarios: Decimal | None = None
    valor_pagado_estimado: Decimal | None = None
    valor_pendiente_estimado: Decimal | None = None
    duracion_meses_contrato: int | None = None
    moneda: str = 'COP'
    resumen: str = ''
    campos_no_encontrados: tuple[str, ...] = field(default_factory=tuple)
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    confianza_general: Decimal = Decimal('0.00')
    requiere_confirmacion_usuario: bool = True
    modelo_usado: str = ''
    habilitado: bool = False
    exito: bool = False
    error: str = ''

    def como_dict(self) -> dict[str, Any]:
        return {
            'es_contrato': self.es_contrato,
            'tipo_documento_detectado': self.tipo_documento_detectado,
            'empresa_contratante': self.empresa_contratante,
            'nit_empresa': self.nit_empresa,
            'nombre_contratista': self.nombre_contratista,
            'documento_contratista': self.documento_contratista,
            'cargo_o_servicio': self.cargo_o_servicio,
            'fecha_inicio_contrato': self.fecha_inicio_contrato.isoformat() if self.fecha_inicio_contrato else None,
            'fecha_fin_contrato': self.fecha_fin_contrato.isoformat() if self.fecha_fin_contrato else None,
            'valor_total_contrato': str(self.valor_total_contrato) if self.valor_total_contrato is not None else None,
            'valor_mensual_o_honorarios': str(self.valor_mensual_o_honorarios) if self.valor_mensual_o_honorarios is not None else None,
            'valor_pagado_estimado': str(self.valor_pagado_estimado) if self.valor_pagado_estimado is not None else None,
            'valor_pendiente_estimado': str(self.valor_pendiente_estimado) if self.valor_pendiente_estimado is not None else None,
            'duracion_meses_contrato': self.duracion_meses_contrato,
            'moneda': self.moneda,
            'resumen': self.resumen,
            'campos_no_encontrados': list(self.campos_no_encontrados),
            'advertencias': list(self.advertencias),
            'confianza_general': str(self.confianza_general),
            'requiere_confirmacion_usuario': self.requiere_confirmacion_usuario,
            'modelo_usado': self.modelo_usado,
            'habilitado': self.habilitado,
            'exito': self.exito,
            'error': self.error,
        }

    def metadata_segura(self, *, documento_id=None) -> dict[str, Any]:
        campos_detectados = []
        for campo in (
            'tipo_documento_detectado',
            'empresa_contratante',
            'nit_empresa',
            'nombre_contratista',
            'documento_contratista',
            'cargo_o_servicio',
            'fecha_inicio_contrato',
            'fecha_fin_contrato',
            'valor_total_contrato',
            'valor_mensual_o_honorarios',
            'valor_pagado_estimado',
            'valor_pendiente_estimado',
            'duracion_meses_contrato',
        ):
            valor = getattr(self, campo)
            if valor not in (None, ''):
                campos_detectados.append(campo)

        datos = {
            'enabled': self.habilitado,
            'attempted': self.habilitado,
            'success': self.exito,
            'modelo': self.modelo_usado,
            'es_contrato': self.es_contrato,
            'campos_detectados': campos_detectados,
            'campos_no_encontrados': list(self.campos_no_encontrados),
            'advertencias': list(self.advertencias),
            'confianza_general': str(self.confianza_general),
            'requiere_confirmacion_usuario': self.requiere_confirmacion_usuario,
            'error_tipo': self.error,
        }
        if documento_id:
            datos['documento_id'] = documento_id
        return datos

    def datos_autocompletado(self) -> dict[str, Any]:
        return {
            'empresa_contratante': self.empresa_contratante,
            'nit_empresa': self.nit_empresa,
            'nombre_contratista': self.nombre_contratista,
            'documento_contratista': self.documento_contratista,
            'cargo_o_servicio': self.cargo_o_servicio,
            'fecha_inicio_contrato': self.fecha_inicio_contrato.isoformat() if self.fecha_inicio_contrato else '',
            'fecha_fin_contrato': self.fecha_fin_contrato.isoformat() if self.fecha_fin_contrato else '',
            'valor_total_contrato': str(self.valor_total_contrato) if self.valor_total_contrato is not None else '',
            'valor_mensual_o_honorarios': (
                str(self.valor_mensual_o_honorarios)
                if self.valor_mensual_o_honorarios is not None
                else ''
            ),
            'valor_pagado_estimado': (
                str(self.valor_pagado_estimado)
                if self.valor_pagado_estimado is not None
                else ''
            ),
            'valor_pendiente_estimado': (
                str(self.valor_pendiente_estimado)
                if self.valor_pendiente_estimado is not None
                else ''
            ),
            'duracion_meses_contrato': self.duracion_meses_contrato,
            'moneda': self.moneda,
        }


def analizar_contrato_con_openai(documento) -> ResultadoAnalisisContratoIAOpenAI:
    if not getattr(settings, 'CONTRACTORS_CONTRACT_AI_ENABLED', False):
        return _resultado_deshabilitado('ia_deshabilitada')
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        return _resultado_deshabilitado('openai_api_key_no_configurada')

    nombre_archivo = getattr(documento, 'name', 'contrato.pdf') or 'contrato.pdf'
    contenido = _leer_documento_pdf(documento)
    modelo = getattr(settings, 'CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4o-mini')

    try:
        from openai import OpenAI

        cliente = OpenAI(api_key=settings.OPENAI_API_KEY)
        respuesta = _crear_respuesta_openai(cliente, modelo, nombre_archivo, contenido)
    except Exception as exc:
        return ResultadoAnalisisContratoIAOpenAI(
            modelo_usado=modelo,
            habilitado=True,
            exito=False,
            error=_clasificar_error_openai(exc),
            advertencias=(str(exc.__class__.__name__), _resumir_error_seguro(exc)),
            campos_no_encontrados=CAMPOS_CONTRATO_ESPERADOS,
        )

    texto = getattr(respuesta, 'output_text', '') or ''
    try:
        return normalizar_resultado_analisis_contrato(texto, modelo_usado=modelo)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return ResultadoAnalisisContratoIAOpenAI(
            modelo_usado=modelo,
            habilitado=True,
            exito=False,
            error='respuesta_ia_invalida',
            advertencias=(str(exc.__class__.__name__), _resumir_error_seguro(exc)),
            campos_no_encontrados=CAMPOS_CONTRATO_ESPERADOS,
        )


def _crear_respuesta_openai(cliente, modelo, nombre_archivo, contenido):
    return cliente.responses.create(
        model=modelo,
        input=[
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'input_file',
                        'filename': nombre_archivo,
                        'file_data': _pdf_a_data_url(contenido),
                    },
                    {
                        'type': 'input_text',
                        'text': _prompt_analisis_contrato(),
                    },
                ],
            },
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': 'analisis_contrato_contratista',
                'strict': True,
                'schema': ESQUEMA_ANALISIS_CONTRATO,
            },
        },
    )


def normalizar_resultado_analisis_contrato(resultado, *, modelo_usado='') -> ResultadoAnalisisContratoIAOpenAI:
    if isinstance(resultado, str):
        resultado = _cargar_json_respuesta(resultado)
    if not isinstance(resultado, dict):
        raise ValueError('resultado_analisis_invalido')

    campos_no_encontrados = tuple(str(valor) for valor in resultado.get('campos_no_encontrados') or ())
    advertencias = tuple(str(valor) for valor in resultado.get('advertencias') or ())

    return ResultadoAnalisisContratoIAOpenAI(
        es_contrato=_normalizar_bool(resultado.get('es_contrato')),
        tipo_documento_detectado=str(resultado.get('tipo_documento_detectado') or ''),
        empresa_contratante=str(resultado.get('empresa_contratante') or ''),
        nit_empresa=str(resultado.get('nit_empresa') or ''),
        nombre_contratista=str(resultado.get('nombre_contratista') or ''),
        documento_contratista=str(resultado.get('documento_contratista') or ''),
        cargo_o_servicio=str(resultado.get('cargo_o_servicio') or ''),
        fecha_inicio_contrato=_normalizar_fecha(resultado.get('fecha_inicio_contrato')),
        fecha_fin_contrato=_normalizar_fecha(resultado.get('fecha_fin_contrato')),
        valor_total_contrato=_normalizar_decimal(resultado.get('valor_total_contrato')),
        valor_mensual_o_honorarios=_normalizar_decimal(resultado.get('valor_mensual_o_honorarios')),
        valor_pagado_estimado=_normalizar_decimal(resultado.get('valor_pagado_estimado')),
        valor_pendiente_estimado=_normalizar_decimal(resultado.get('valor_pendiente_estimado')),
        duracion_meses_contrato=_normalizar_entero(resultado.get('duracion_meses_contrato')),
        moneda=str(resultado.get('moneda') or 'COP'),
        resumen=str(resultado.get('resumen') or ''),
        campos_no_encontrados=campos_no_encontrados,
        advertencias=advertencias,
        confianza_general=_normalizar_decimal(resultado.get('confianza_general')) or Decimal('0.00'),
        requiere_confirmacion_usuario=bool(resultado.get('requiere_confirmacion_usuario', True)),
        modelo_usado=modelo_usado,
        habilitado=True,
        exito=True,
    )


def validar_resultado_analisis_contrato(resultado):
    if not isinstance(resultado, ResultadoAnalisisContratoIAOpenAI):
        raise ValidationError('resultado_analisis_contrato_invalido')
    if resultado.habilitado and resultado.exito and resultado.es_contrato is False:
        raise ValidationError('El documento cargado no parece ser un contrato valido.')
    return True


def _resultado_deshabilitado(error):
    return ResultadoAnalisisContratoIAOpenAI(
        es_contrato=None,
        campos_no_encontrados=CAMPOS_CONTRATO_ESPERADOS,
        advertencias=('No fue posible analizar automaticamente el contrato. Puedes completar la informacion manualmente.',),
        requiere_confirmacion_usuario=True,
        modelo_usado=getattr(settings, 'CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4o-mini'),
        habilitado=False,
        exito=False,
        error=error,
    )


def _leer_documento_pdf(documento):
    posicion = None
    if hasattr(documento, 'tell') and hasattr(documento, 'seek'):
        try:
            posicion = documento.tell()
            documento.seek(0)
        except Exception:
            posicion = None
    contenido = documento.read()
    if posicion is not None:
        documento.seek(posicion)
    if not contenido:
        raise ErrorAnalisisContratoIA('contrato_pdf_vacio')
    return contenido


def _prompt_analisis_contrato():
    return (
        'Analiza este PDF como contrato de prestacion de servicios o contrato laboral colombiano. '
        'Devuelve exclusivamente JSON valido con estas llaves: es_contrato, tipo_documento_detectado, '
        'empresa_contratante, nit_empresa, nombre_contratista, documento_contratista, cargo_o_servicio, '
        'fecha_inicio_contrato, fecha_fin_contrato, duracion_meses_contrato, valor_total_contrato, '
        'valor_mensual_o_honorarios, valor_pagado_estimado, valor_pendiente_estimado, moneda, resumen, '
        'campos_no_encontrados, advertencias, confianza_general, '
        'requiere_confirmacion_usuario. Usa fechas ISO YYYY-MM-DD, valores numericos sin simbolos, moneda COP '
        'si no se especifica otra. Nunca inventes datos: si falta un campo, usa null o cadena vacia y agregalo '
        'a campos_no_encontrados. Si el contrato expresa duracion en meses pero no fecha final, informa '
        'duracion_meses_contrato. La respuesta siempre requiere confirmacion del usuario.'
    )


def _cargar_json_respuesta(texto):
    texto = texto.strip()
    if texto.startswith('```'):
        texto = texto.strip('`')
        if texto.lower().startswith('json'):
            texto = texto[4:].strip()
    return json.loads(texto)


def _pdf_a_data_url(contenido):
    return f"data:application/pdf;base64,{base64.b64encode(contenido).decode('ascii')}"


def _resumir_error_seguro(exc):
    mensaje = str(exc)
    if not mensaje:
        return ''
    mensaje = mensaje.replace(getattr(settings, 'OPENAI_API_KEY', '') or '***', '[redactado]')
    prohibidos = ('sk-', 'data:application/pdf;base64,', '%PDF', 'prompt')
    if any(valor.lower() in mensaje.lower() for valor in prohibidos):
        return 'detalle_redactado'
    return mensaje[:220]


def _clasificar_error_openai(exc):
    clase = str(exc.__class__.__name__)
    mensaje = str(exc).lower()
    if (
        clase == 'RateLimitError'
        or 'insufficient_quota' in mensaje
        or 'exceeded your current quota' in mensaje
        or 'billing' in mensaje
        or 'quota' in mensaje
    ):
        return 'cuota_openai_excedida'
    return 'error_openai'


def _normalizar_bool(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() in {'true', '1', 'si', 'sí', 'yes'}
    if valor is None:
        return None
    return bool(valor)


def _normalizar_fecha(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _normalizar_decimal(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None


def _normalizar_entero(valor):
    decimal = _normalizar_decimal(valor)
    if decimal is None:
        return None
    try:
        entero = int(decimal)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return entero if entero > 0 else None

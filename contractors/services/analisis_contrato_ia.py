import base64
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings

from contractors.services.analisis_contrato import ResultadoAnalisisContrato


logger = logging.getLogger(__name__)


def analizar_contrato_con_openai(documento):
    if not getattr(settings, 'CONTRACTORS_CONTRACT_AI_ENABLED', False):
        return None
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        return None

    try:
        documento.open('rb')
        documento.seek(0)
        contenido = documento.read()
        documento.seek(0)
        from openai import OpenAI

        modelo = getattr(settings, 'CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4.1-mini')
        cliente = OpenAI(api_key=settings.OPENAI_API_KEY)
        respuesta = cliente.responses.create(
            model=modelo,
            input=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'input_file',
                        'filename': 'contrato.pdf',
                        'file_data': f'data:application/pdf;base64,{base64.b64encode(contenido).decode("ascii")}',
                    },
                    {'type': 'input_text', 'text': _prompt_seguro()},
                ],
            }],
        )
        datos = _cargar_json_respuesta(respuesta.output_text)
        return _normalizar(datos, modelo=modelo)
    except Exception as exc:
        modelo = getattr(settings, 'CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4.1-mini')
        logger.warning(
            'Fallo seguro del analisis contractual OpenAI: model=%s error_type=%s',
            modelo,
            type(exc).__name__,
        )
        return ResultadoAnalisisContrato(
            fuente='openai',
            disponible=False,
            error_tipo='error_analisis_ia',
            advertencias=(
                'No fue posible completar el análisis asistido. Puedes confirmar los datos manualmente.',
            ),
            diagnostico={
                'engine': 'openai',
                'pdf_text_chars': None,
                'reason': 'openai_error',
                'openai_error_type': type(exc).__name__,
                'model': modelo,
            },
        )
    finally:
        try:
            documento.seek(0)
        except (AttributeError, OSError, ValueError):
            pass


def _normalizar(datos, *, modelo):
    return ResultadoAnalisisContrato(
        nombre_contratista=str(datos.get('nombre_contratista') or ''),
        documento_contratista=str(datos.get('documento_contratista') or ''),
        empresa_contratante=str(datos.get('empresa_contratante') or ''),
        nit_empresa=str(datos.get('nit_empresa') or ''),
        cargo_o_servicio=str(datos.get('cargo_o_servicio') or ''),
        tipo_contrato=_tipo_contrato(datos.get('tipo_contrato')),
        fecha_inicio_contrato=_fecha(datos.get('fecha_inicio_contrato')),
        fecha_fin_contrato=_fecha(datos.get('fecha_fin_contrato')),
        valor_total_contrato=_decimal(datos.get('valor_total_contrato')),
        valor_pagado_estimado=_decimal(datos.get('valor_pagado_estimado')),
        valor_pendiente_estimado=_decimal(datos.get('valor_pendiente_estimado')),
        valor_mensual_o_honorarios=_decimal(datos.get('valor_mensual_o_honorarios')),
        forma_pago=_forma_pago(datos.get('forma_pago')),
        frecuencia_pago=str(datos.get('frecuencia_pago') or '')[:120],
        evidencia_forma_pago=str(datos.get('evidencia_forma_pago') or '')[:500],
        confianza_forma_pago=_confianza(datos.get('confianza_forma_pago')),
        duracion_meses_contrato=_entero(datos.get('duracion_meses_contrato')),
        confianza_general=_confianza(datos.get('confianza_general')),
        advertencias=tuple(str(item) for item in datos.get('advertencias') or ()),
        fuente='openai',
        diagnostico={
            'engine': 'openai',
            'pdf_text_chars': None,
            'reason': 'openai_completed',
            'model': modelo,
        },
    )


def _cargar_json_respuesta(texto):
    contenido = str(texto or '').strip()
    if contenido.startswith('```'):
        contenido = contenido.removeprefix('```json').removeprefix('```').strip()
        if contenido.endswith('```'):
            contenido = contenido[:-3].strip()
    return json.loads(contenido)


def _prompt_seguro():
    return (
        'Analiza el contrato colombiano y devuelve exclusivamente un objeto JSON. '
        'No inventes información. Usa null o cadena vacía cuando no exista evidencia. '
        'Campos: nombre_contratista, documento_contratista, empresa_contratante, nit_empresa, '
        'cargo_o_servicio, tipo_contrato, fecha_inicio_contrato, fecha_fin_contrato, '
        'valor_total_contrato, valor_pagado_estimado, valor_pendiente_estimado, '
        'valor_mensual_o_honorarios, duracion_meses_contrato, confianza_general y advertencias. '
        'Incluye forma_pago, frecuencia_pago, evidencia_forma_pago y confianza_forma_pago. '
        'forma_pago debe ser MENSUAL, QUINCENAL, SEMANAL, POR_ENTREGABLE, '
        'CONTRA_FACTURA, VARIABLE, NO_IDENTIFICADA u OTRO. evidencia_forma_pago debe '
        'ser un fragmento breve, máximo 300 caracteres, que soporte la clasificación. '
        'tipo_contrato debe ser PRESTACION_SERVICIOS, LABORAL u OTRO; usa cadena vacía si no hay evidencia. '
        'Las fechas deben usar YYYY-MM-DD y los valores deben ser numéricos sin símbolos.'
    )


def _fecha(valor):
    try:
        return date.fromisoformat(str(valor)[:10]) if valor else None
    except ValueError:
        return None


def _decimal(valor):
    try:
        return Decimal(str(valor)) if valor not in (None, '') else None
    except (InvalidOperation, ValueError):
        return None


def _confianza(valor):
    confianza = _decimal(valor)
    if confianza is None or confianza < 0 or confianza > 1:
        return Decimal('0.00')
    return confianza


def _tipo_contrato(valor):
    texto = str(valor or '').strip().lower()
    if not texto:
        return ''
    normalizado = texto.replace('_', ' ')
    if 'prestacion' in _sin_tildes(normalizado) or 'orden de servicios' in _sin_tildes(normalizado):
        return 'PRESTACION_SERVICIOS'
    if texto == 'laboral' or 'contrato laboral' in normalizado:
        return 'LABORAL'
    if texto in {'prestacion_servicios', 'prestación_servicios'}:
        return 'PRESTACION_SERVICIOS'
    return 'OTRO'


def _sin_tildes(valor):
    return str(valor).translate(str.maketrans('áéíóúüñ', 'aeiouun'))


def _entero(valor):
    try:
        return int(valor) if valor not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _forma_pago(valor):
    normalizado = str(valor or '').strip().upper().replace(' ', '_')
    permitidos = {
        'MENSUAL', 'QUINCENAL', 'SEMANAL', 'POR_ENTREGABLE',
        'CONTRA_FACTURA', 'VARIABLE', 'NO_IDENTIFICADA', 'OTRO',
    }
    return normalizado if normalizado in permitidos else 'NO_IDENTIFICADA'

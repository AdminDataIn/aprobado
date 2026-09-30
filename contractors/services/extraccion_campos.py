"""Explicit field schema, per-field validation and provenance; no credit decisions."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError

from contractors.validators import documento_numerico, nombre_persona, texto_normalizado


CAMPOS = (
    'nombre_contratista', 'nombres', 'apellidos', 'documento_contratista',
    'empresa_contratante', 'nit_empresa', 'cargo_o_servicio', 'tipo_contrato',
    'fecha_inicio_contrato', 'fecha_fin_contrato', 'valor_total_contrato',
    'valor_pagado_estimado', 'valor_pendiente_estimado', 'valor_mensual_o_honorarios',
    'forma_pago', 'duracion_meses_contrato',
)


def presente(value):
    return value not in (None, '', 'NO_IDENTIFICADA')


def limpiar_resultado(resultado):
    cambios = {}
    for campo in CAMPOS:
        valor = getattr(resultado, campo)
        if not presente(valor):
            continue
        try:
            if campo in {'nombres', 'apellidos', 'nombre_contratista'}:
                valor = nombre_persona(valor)
                if len(valor) > (240 if campo == 'nombre_contratista' else 120):
                    raise ValueError('invalid_length')
            elif campo == 'documento_contratista':
                # PDF often prints grouped digits, unlike the human-input contract.
                valor = documento_numerico(str(valor).replace('.', '').replace(' ', ''))
            elif campo.startswith('valor_'):
                if not isinstance(valor, Decimal) or not valor.is_finite() or valor < 0 or valor >= Decimal('1000000000000'):
                    raise ValueError('invalid_amount')
                if valor != valor.quantize(Decimal('0.01')):
                    raise ValueError('invalid_precision')
            elif campo.startswith('fecha_'):
                if not isinstance(valor, date):
                    raise ValueError('invalid_date')
            elif campo == 'duracion_meses_contrato':
                if isinstance(valor, bool) or not isinstance(valor, int) or not 1 <= valor <= 32767:
                    raise ValueError('invalid_duration')
            elif campo == 'tipo_contrato' and valor not in {'PRESTACION_SERVICIOS', 'LABORAL', 'OTRO'}:
                raise ValueError('invalid_choice')
            elif isinstance(valor, str):
                valor = texto_normalizado(valor)
                if len(valor) > 160:
                    raise ValueError('invalid_length')
            cambios[campo] = valor
        except (ValidationError, ValueError, ArithmeticError):
            cambios[campo] = None if campo.startswith(('valor_', 'fecha_', 'duracion_')) else ''
    return replace(resultado, **cambios)


def completar_faltantes(ia, fallback):
    ia, fallback = limpiar_resultado(ia), limpiar_resultado(fallback)
    cambios, fuentes = {}, {}
    for campo in CAMPOS:
        if ia.disponible and presente(getattr(ia, campo)):
            fuentes[campo] = 'IA'
        elif fallback.disponible and presente(getattr(fallback, campo)):
            cambios[campo] = getattr(fallback, campo)
            fuentes[campo] = 'REGEX_TEXTO_PDF'
    if 'forma_pago' in cambios:
        for campo in ('frecuencia_pago', 'evidencia_forma_pago', 'confianza_forma_pago'):
            cambios[campo] = getattr(fallback, campo)
    return replace(ia if ia.disponible else fallback, **cambios, fuentes_campos=fuentes)


def evidencia_campos(resultado):
    evidencia = {}
    for campo in CAMPOS:
        valor = getattr(resultado, campo)
        encontrado = presente(valor)
        if campo == 'documento_contratista' and encontrado:
            valor = '****' + str(valor)[-4:]
        elif isinstance(valor, (Decimal, date)):
            valor = str(valor)
        evidencia[campo] = {
            'valor': valor if encontrado else None,
            'fuente': resultado.fuentes_campos.get(campo, 'IA' if resultado.fuente == 'openai' else 'REGEX_TEXTO_PDF') if encontrado else '',
            'encontrado': encontrado,
            'estado_evidencia': 'EXTRAIDO_NO_VERIFICADO' if encontrado else 'NO_ENCONTRADO',
        }
    return evidencia

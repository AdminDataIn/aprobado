from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
import logging
import re
import unicodedata

from django.conf import settings

from contractors.services.analisis_contrato import analizar_contrato_fallback
from contractors.services.analisis_contrato_ia import analizar_contrato_con_openai
from gestion_creditos.models import Empresa


MENSAJE_DOCUMENTO_DIFERENTE = (
    'El documento detectado en el contrato no coincide con el número de documento ingresado.'
)
MENSAJE_DOCUMENTO_NO_DETECTADO = 'No fue posible validar el documento dentro del contrato.'

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoAnalisisContractualSeguro:
    datos: dict
    advertencias: tuple[str, ...]
    bloqueos: tuple[str, ...]
    confianza: Decimal
    fuente: str
    disponible: bool
    empresa_sugerida: dict
    metadata: dict

    @property
    def estado(self):
        if self.bloqueos:
            return 'BLOQUEADO'
        if not self.disponible:
            return 'NO_DISPONIBLE'
        if self.advertencias:
            return 'CON_ADVERTENCIAS'
        return 'COMPLETADO'

    def respuesta_publica(self):
        respuesta = {
            'success': self.disponible and not self.bloqueos,
            'manual_allowed': not self.bloqueos,
            'estado': self.estado,
            'datos': self.datos,
            'advertencias': list(self.advertencias),
            'bloqueos': list(self.bloqueos),
            'confianza_general': str(self.confianza),
            'fuente': self.fuente,
            'empresa_sugerida': self.empresa_sugerida,
        }
        if settings.DEBUG:
            respuesta['diagnostico'] = self.metadata.get('diagnostico', {})
        return respuesta


def analizar_contrato_seguro(*, solicitud, documento):
    ai_enabled = bool(getattr(settings, 'CONTRACTORS_CONTRACT_AI_ENABLED', False))
    has_openai_key = bool(getattr(settings, 'OPENAI_API_KEY', ''))
    modelo = getattr(settings, 'CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4.1-mini')
    resultado_ia = analizar_contrato_con_openai(documento)
    resultado = resultado_ia
    advertencias_previas = []
    fallback_reason = ''
    if resultado is None or not resultado.disponible:
        if resultado is not None:
            advertencias_previas.extend(resultado.advertencias)
            fallback_reason = resultado.diagnostico.get('reason') or resultado.error_tipo
        elif not ai_enabled:
            fallback_reason = 'ai_disabled'
        elif not has_openai_key:
            fallback_reason = 'openai_key_missing'
        resultado = analizar_contrato_fallback(documento)

    advertencias = list(advertencias_previas) + list(resultado.advertencias)
    bloqueos = []
    documento_detectado = _solo_digitos(resultado.documento_contratista)
    documento_solicitud = _solo_digitos(solicitud.numero_documento)
    documento_coincide = None
    if documento_detectado:
        documento_coincide = documento_detectado == documento_solicitud
        if not documento_coincide:
            bloqueos.append(MENSAJE_DOCUMENTO_DIFERENTE)
    elif MENSAJE_DOCUMENTO_NO_DETECTADO not in advertencias:
        advertencias.append(MENSAJE_DOCUMENTO_NO_DETECTADO)

    empresa_sugerida = sugerir_empresa_exacta(
        nit=resultado.nit_empresa,
        nombre=resultado.empresa_contratante,
    )
    empresa_sugerida_id = empresa_sugerida.get('empresa_sugerida_id')
    match_tipo = empresa_sugerida.get('match_tipo')
    if resultado.nit_empresa or resultado.empresa_contratante:
        if match_tipo == 'aproximado' and empresa_sugerida_id:
            advertencias.append(
                f"Empresa sugerida encontrada en Aprobado: {empresa_sugerida['nombre']}. "
                'Revisa y confirma antes de continuar.'
            )
        elif match_tipo == 'ambiguo':
            advertencias.append(
                'Encontramos varias empresas similares en Aprobado. Debes elegir manualmente la empresa correcta.'
            )
        elif not empresa_sugerida_id:
            advertencias.append(
                'La empresa detectada no coincide con una empresa activa registrada en Aprobado.'
            )

    valor_pendiente = resultado.valor_pendiente_estimado
    if (
        valor_pendiente is None
        and resultado.valor_total_contrato is not None
        and resultado.valor_pagado_estimado is not None
    ):
        valor_pendiente = max(
            Decimal('0.00'),
            resultado.valor_total_contrato - resultado.valor_pagado_estimado,
        )

    datos = resultado.datos_sugeridos()
    forma_pago = _normalizar_forma_pago(resultado.forma_pago)
    datos.update({
        'forma_pago': forma_pago,
        'frecuencia_pago': str(resultado.frecuencia_pago or '')[:120],
        'forma_pago_mensual': forma_pago == 'MENSUAL',
        'evidencia_forma_pago': str(resultado.evidencia_forma_pago or '')[:500],
        'confianza_forma_pago': str(resultado.confianza_forma_pago),
        'fuente_forma_pago': resultado.fuente,
    })
    datos['valor_pendiente_estimado'] = str(valor_pendiente) if valor_pendiente is not None else ''
    datos['documento_detectado'] = _enmascarar_documento(documento_detectado)

    diagnostico_ia = resultado_ia.diagnostico if resultado_ia is not None else {}
    diagnostico_resultado = resultado.diagnostico or {}
    diagnostico = {
        'engine': resultado.fuente if resultado.disponible else 'unavailable',
        'ai_enabled': ai_enabled,
        'has_openai_key': has_openai_key,
        'model': modelo,
        'pdf_text_chars': diagnostico_resultado.get('pdf_text_chars'),
        'reason': diagnostico_resultado.get('reason') or resultado.error_tipo or fallback_reason,
        'fallback_reason': fallback_reason,
        'openai_error_type': diagnostico_ia.get('openai_error_type', ''),
        'analysis_status': '',
    }
    metadata = {
        'version': 'analisis_contractual_seguro_v1',
        'estado': '',
        'fuente': resultado.fuente,
        'disponible': resultado.disponible,
        'confianza_general': str(resultado.confianza_general),
        'advertencias': _deduplicar(advertencias),
        'bloqueos': _deduplicar(bloqueos),
        'identidad': {
            'documento_detectado': bool(documento_detectado),
            'documento_coincide': documento_coincide,
        },
        'empresa_sugerida': empresa_sugerida,
        'datos_sugeridos': datos,
        'forma_pago_contractual': {
            'forma_pago': forma_pago,
            'frecuencia_pago': datos['frecuencia_pago'],
            'forma_pago_mensual': forma_pago == 'MENSUAL',
            'evidencia': datos['evidencia_forma_pago'],
            'confianza': datos['confianza_forma_pago'],
            'fuente': resultado.fuente,
        },
        'diagnostico': diagnostico,
    }
    seguro = ResultadoAnalisisContractualSeguro(
        datos=datos,
        advertencias=tuple(metadata['advertencias']),
        bloqueos=tuple(metadata['bloqueos']),
        confianza=resultado.confianza_general,
        fuente=resultado.fuente,
        disponible=resultado.disponible,
        empresa_sugerida=empresa_sugerida,
        metadata=metadata,
    )
    metadata['estado'] = seguro.estado
    diagnostico['analysis_status'] = seguro.estado
    logger.info(
        'Analisis contractual finalizado: engine=%s ai_enabled=%s has_openai_key=%s model=%s status=%s',
        diagnostico['engine'],
        ai_enabled,
        has_openai_key,
        modelo,
        seguro.estado,
    )
    return seguro


def sugerir_empresa_exacta(*, nit='', nombre=''):
    nit_normalizado = _solo_digitos(nit)
    nombre_normalizado = normalizar_nombre_empresa(nombre)
    empresas = list(Empresa.objects.filter(convenio_activo=True).order_by('id'))

    if nit_normalizado:
        for empresa in empresas:
            if _solo_digitos(getattr(empresa, 'nit', '')) == nit_normalizado:
                return _empresa_dict(
                    empresa,
                    coincidencia='NIT_EXACTO',
                    match_tipo='nit_exacto',
                    match_score=1.0,
                    empresa_detectada=nombre,
                    nit_detectado=nit,
                )

    if nombre_normalizado:
        for empresa in empresas:
            for campo, valor in _nombres_empresa(empresa):
                if nombre_normalizado == valor:
                    match_tipo = 'slug_exacto' if campo == 'slug' else 'nombre_exacto'
                    coincidencia = 'SLUG_EXACTO' if campo == 'slug' else 'NOMBRE_EXACTO'
                    return _empresa_dict(
                        empresa,
                        coincidencia=coincidencia,
                        match_tipo=match_tipo,
                        match_score=1.0,
                        empresa_detectada=nombre,
                        nit_detectado=nit,
                    )

        candidatos = []
        for empresa in empresas:
            puntaje = max(
                (_similitud_nombres(nombre_normalizado, valor) for _, valor in _nombres_empresa(empresa)),
                default=0.0,
            )
            candidatos.append((puntaje, empresa))
        candidatos.sort(key=lambda item: (-item[0], item[1].id))
        if candidatos and candidatos[0][0] >= 0.82:
            mejor_puntaje, mejor_empresa = candidatos[0]
            segundo_puntaje = candidatos[1][0] if len(candidatos) > 1 else 0.0
            if mejor_puntaje - segundo_puntaje >= 0.05:
                return _empresa_dict(
                    mejor_empresa,
                    coincidencia='APROXIMADO',
                    match_tipo='aproximado',
                    match_score=mejor_puntaje,
                    empresa_detectada=nombre,
                    nit_detectado=nit,
                )
            return _empresa_sin_seleccion(
                empresa_detectada=nombre,
                nit_detectado=nit,
                match_tipo='ambiguo',
                match_score=mejor_puntaje,
            )
    return _empresa_sin_seleccion(
        empresa_detectada=nombre,
        nit_detectado=nit,
        match_tipo='sin_match',
    )


def normalizar_nombre_empresa(valor):
    texto = ''.join(
        caracter
        for caracter in unicodedata.normalize('NFKD', str(valor or '').lower())
        if not unicodedata.combining(caracter)
    )
    texto = re.sub(r'[^a-z0-9 ]', ' ', texto)
    tokens = texto.split()
    sufijos = {
        ('sas',), ('sa',), ('ltda',), ('limitada',), ('s', 'a', 's'), ('s', 'a'),
        ('s', 'en', 'c'), ('sociedad', 'anonima'),
    }
    while tokens:
        eliminado = False
        for sufijo in sorted(sufijos, key=len, reverse=True):
            if tuple(tokens[-len(sufijo):]) == sufijo:
                del tokens[-len(sufijo):]
                eliminado = True
                break
        if not eliminado:
            break
    return ' '.join(_singularizar_token(token) for token in tokens)


def _nombres_empresa(empresa):
    candidatos = []
    for campo in ('nombre', 'razon_social', 'slug'):
        normalizado = normalizar_nombre_empresa(getattr(empresa, campo, ''))
        if normalizado and normalizado not in {valor for _, valor in candidatos}:
            candidatos.append((campo, normalizado))
    return candidatos


def _similitud_nombres(izquierda, derecha):
    if not izquierda or not derecha:
        return 0.0
    secuencia = SequenceMatcher(None, izquierda, derecha).ratio()
    tokens_izquierda = izquierda.split()
    tokens_derecha = derecha.split()
    similitud_tokens = sum(
        max(SequenceMatcher(None, token, candidato).ratio() for candidato in tokens_derecha)
        for token in tokens_izquierda
    ) / max(len(tokens_izquierda), len(tokens_derecha))
    return round(max(secuencia, similitud_tokens), 4)


def _singularizar_token(token):
    if len(token) > 4 and token.endswith('es'):
        return token[:-2]
    if len(token) > 4 and token.endswith('s') and not token.endswith('sis'):
        return token[:-1]
    return token


def _empresa_dict(
    empresa,
    *,
    coincidencia,
    match_tipo,
    match_score,
    empresa_detectada,
    nit_detectado,
):
    return {
        'empresa_sugerida_id': empresa.id,
        'nombre': empresa.razon_social or empresa.nombre,
        'empresa_sugerida_nombre': empresa.razon_social or empresa.nombre,
        'nit': getattr(empresa, 'nit', ''),
        'tipo_coincidencia': coincidencia,
        'match_tipo': match_tipo,
        'match_score': round(float(match_score), 4),
        'empresa_detectada': str(empresa_detectada or '')[:160],
        'nit_detectado': str(nit_detectado or '')[:32],
        'requiere_confirmacion': True,
    }


def _empresa_sin_seleccion(*, empresa_detectada, nit_detectado, match_tipo, match_score=None):
    return {
        'empresa_sugerida_id': None,
        'nombre': '',
        'empresa_sugerida_nombre': '',
        'nit': '',
        'tipo_coincidencia': match_tipo.upper(),
        'match_tipo': match_tipo,
        'match_score': round(float(match_score), 4) if match_score is not None else None,
        'empresa_detectada': str(empresa_detectada or '')[:160],
        'nit_detectado': str(nit_detectado or '')[:32],
        'requiere_confirmacion': True,
    }


def _solo_digitos(valor):
    return ''.join(caracter for caracter in str(valor or '') if caracter.isdigit())


def _enmascarar_documento(valor):
    return f'****{valor[-4:]}' if valor else ''


def _deduplicar(valores):
    return list(dict.fromkeys(valor for valor in valores if valor))


def _normalizar_forma_pago(valor):
    normalizado = str(valor or '').strip().upper().replace(' ', '_')
    permitidos = {
        'MENSUAL', 'QUINCENAL', 'SEMANAL', 'POR_ENTREGABLE',
        'CONTRA_FACTURA', 'VARIABLE', 'NO_IDENTIFICADA', 'OTRO',
    }
    return normalizado if normalizado in permitidos else 'NO_IDENTIFICADA'

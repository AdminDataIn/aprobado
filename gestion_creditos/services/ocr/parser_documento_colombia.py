"""Conservative CC labelled-layout parser. Unknown layouts require review."""
import re
import unicodedata
from collections import OrderedDict
from datetime import date

VERSION = 'cc-etiquetas-1'


def normalizar_texto(value):
    value = unicodedata.normalize('NFD', str(value or ''))
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).upper().split())


def normalizar_numero(value):
    value = re.sub(r'[.\s-]', '', str(value or ''))
    return value if re.fullmatch(r'[0-9]{1,20}', value) else ''


def fecha_inequivoca(value):
    # Only ISO dates in v1. Do not guess day/month from ambiguous numeric text.
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _lineas(tokens):
    grouped = OrderedDict()
    for token in tokens:
        grouped.setdefault(tuple(token['line']), []).append(token)
    return [(' '.join(t['text'] for t in group).strip(),
             min((t['confidence'] for t in group if t['confidence'] >= 0), default=None))
            for group in grouped.values()]


LABELS = ('NUMERO', 'APELLIDOS', 'NOMBRES', 'FECHA DE NACIMIENTO',
          'FECHA DE EXPEDICION', 'LUGAR DE EXPEDICION')


def _campo(lines, label):
    candidates = []
    for i, (raw, confidence) in enumerate(lines):
        normalized = normalizar_texto(raw)
        if normalized == label and i + 1 < len(lines):
            next_raw, next_conf = lines[i + 1]
            if not any(normalizar_texto(next_raw).startswith(x) for x in LABELS):
                candidates.append((next_raw, next_conf))
        elif normalized.startswith(label + ':'):
            candidates.append((raw.split(':', 1)[1].strip(), confidence))
    return candidates[0] if len(candidates) == 1 else ('', None)


def clasificar(tokens):
    lines = _lineas(tokens)
    text = ' '.join(normalizar_texto(line[0]) for line in lines)
    if 'CEDULA DE EXTRANJERIA' in text:
        return 'NO_SOPORTADO', 'DESCONOCIDO', lines
    if 'REPUBLICA DE COLOMBIA' not in text or 'CEDULA DE CIUDADANIA' not in text:
        return 'DESCONOCIDO', 'DESCONOCIDO', lines
    front = all(_campo(lines, key)[0] for key in ('APELLIDOS', 'NOMBRES'))
    back = any(_campo(lines, key)[0] for key in ('FECHA DE NACIMIENTO', 'FECHA DE EXPEDICION'))
    side = 'FRONTAL' if front and not back else 'TRASERA' if back and not front else 'DESCONOCIDO'
    return 'CC_COLOMBIA', side, lines


def parsear(frontal, trasera):
    ft, fl, front = clasificar(frontal['tokens'])
    bt, bl, back = clasificar(trasera['tokens'])
    result = {'tipo_documento_detectado': ft if ft == bt else 'DESCONOCIDO',
              'lado_frontal_detectado': fl, 'lado_trasera_detectado': bl,
              'confianza': {}, 'evidencia': {'layout': VERSION, 'codigos': []}}
    codes = result['evidencia']['codigos']
    if not frontal['tokens'] or not trasera['tokens']:
        codes.append('TEXTO_ILEGIBLE')
    if 'NO_SOPORTADO' in (ft, bt):
        result['tipo_documento_detectado'] = 'NO_SOPORTADO'
        codes.append('DOCUMENTO_NO_SOPORTADO')
    elif ft != 'CC_COLOMBIA' or bt != 'CC_COLOMBIA':
        codes.append('DOCUMENTO_DESCONOCIDO')
    if fl == 'TRASERA' or bl == 'FRONTAL':
        codes.append('LADO_INCORRECTO')
    elif fl == 'DESCONOCIDO' or bl == 'DESCONOCIDO':
        codes.append('LADO_DESCONOCIDO')
    for field, label in [('numero_documento', 'NUMERO'), ('nombres', 'NOMBRES'), ('apellidos', 'APELLIDOS')]:
        raw, confidence = _campo(front, label) if fl == 'FRONTAL' else ('', None)
        limit = 80 if field == 'numero_documento' else 240
        normalized = normalizar_numero(raw) if field == 'numero_documento' else normalizar_texto(raw)
        if len(raw) > limit or confidence is None or confidence < 70:
            normalized = ''
        result[field + '_bruto'] = raw[:limit]
        result[field + '_normalizado'] = normalized[:limit]
        result['confianza'][field] = confidence
        if not normalized:
            codes.append('NUMERO_NO_ENCONTRADO' if field == 'numero_documento' else 'NOMBRE_NO_LEIBLE')
    for field, label in [('fecha_nacimiento', 'FECHA DE NACIMIENTO'), ('fecha_expedicion', 'FECHA DE EXPEDICION')]:
        raw, confidence = _campo(back, label) if bl == 'TRASERA' else ('', None)
        result[field] = fecha_inequivoca(raw) if confidence is not None and confidence >= 70 else None
        result['confianza'][field] = confidence
        if raw and not result[field]:
            codes.append('FECHA_NO_INTERPRETABLE')
    raw, confidence = _campo(back, 'LUGAR DE EXPEDICION') if bl == 'TRASERA' else ('', None)
    result['lugar_expedicion_bruto'] = raw[:240]
    result['confianza']['lugar_expedicion'] = confidence
    result['lugar_expedicion_normalizado'] = normalizar_texto(raw)[:240] if confidence is not None and confidence >= 70 else ''
    result['codigo_error'] = codes[0] if codes else ''
    result['estado'] = 'REQUIERE_REVISION' if codes else 'COMPLETADO'
    return result

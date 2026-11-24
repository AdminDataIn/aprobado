"""Conservative CC labelled-layout parser. Unknown layouts require review."""
import re
import unicodedata
from collections import OrderedDict
from datetime import date

VERSION_ANTERIOR = 'cc-etiquetas-1'
VERSION = 'cc-espacial-2'
VERSIONES = (VERSION_ANTERIOR, VERSION)
CONFIANZA_MINIMA = 70


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


def _grupos(tokens):
    grouped = OrderedDict()
    for token in tokens:
        grouped.setdefault(tuple(token['line']), []).append(token)
    return grouped.values()


def _lectura(group):
    return (' '.join(t['text'] for t in group).strip(),
            min((t['confidence'] for t in group if t['confidence'] >= 0), default=None))


def _lineas(tokens):
    return [_lectura(group) for group in _grupos(tokens)]


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


ETIQUETA_NUMERO = re.compile(
    r'^(NUMERO(?: DE (?:DOCUMENTO|IDENTIFICACION))?|NUIP|NUM\.)(?=\s|:|$)\s*:?\s*(.*)$')
OTRO_DOCUMENTO = re.compile(r'\b(?:CEDULA DE EXTRANJERIA|TARJETA DE IDENTIDAD|CONTRASENA)\b')


def numero_delimitado(raw):
    """Accept digits or consistent thousands separators, not joined unrelated groups."""
    raw = raw.strip()
    if not re.fullmatch(r'[0-9]{6,10}|[0-9]{1,3}([. -])[0-9]{3}(?:\1[0-9]{3}){0,2}', raw):
        return ''
    digits = normalizar_numero(raw)
    return digits if 6 <= len(digits) <= 10 else ''


def _caja(group):
    positions = [t.get('box') for t in group]
    if not positions or any(not p or p[2] <= 0 or p[3] <= 0 for p in positions):
        return None
    x, y = min(p[0] for p in positions), min(p[1] for p in positions)
    return x, y, max(p[0] + p[2] for p in positions) - x, max(p[1] + p[3] for p in positions) - y


def _lineas_geometricas(tokens):
    return [(*_lectura(group), _caja(group)) for group in _grupos(tokens)]


def _vecino(label, value):
    if not label or not value:
        return False
    lx, ly, lw, lh = label
    vx, vy, _, vh = value
    h = max(lh, vh)
    right = (-1 <= vx - (lx + lw) <= 3 * h
             and abs((vy + vh / 2) - (ly + lh / 2)) <= .6 * h)
    below = (-1 <= vy - (ly + lh) <= 1.5 * h and abs(vx - lx) <= .75 * h)
    return right or below


def tiene_cabecera(tokens):
    text = normalizar_texto(' '.join(t['text'] for t in tokens))
    return (not OTRO_DOCUMENTO.search(text) and 'REPUBLICA DE COLOMBIA' in text
            and 'CEDULA DE CIUDADANIA' in text)


def ancla_recuperada(tokens):
    anchors = [t for t in _lineas_geometricas(tokens)
               if t[0] and normalizar_texto(t[0]).rstrip(':').strip() == 'NUMERO']
    return anchors[0] if len(anchors) == 1 and anchors[0][1] is not None and anchors[0][1] >= CONFIANZA_MINIMA else None


def _numero_espacial(documento):
    lines = _lineas_geometricas(documento['tokens'])
    anchors = [(m, conf, box) for raw, conf, box in lines
               if (m := ETIQUETA_NUMERO.fullmatch(normalizar_texto(raw)))]
    recovered = documento.get('rescate', {}).get('etiqueta')
    local = False
    if not anchors and recovered:
        raw, conf, box = recovered
        if normalizar_texto(raw).rstrip(':').strip() == 'NUMERO':
            anchors = [(ETIQUETA_NUMERO.fullmatch('NUMERO'), conf, box)]
            local = True
    if len(anchors) != 1:
        return '', None
    anchor, confidence, box = anchors[0]
    if confidence is None or confidence < CONFIANZA_MINIMA:
        return '', None
    label, inline = anchor.groups()
    if inline:
        values = [(inline, confidence, box)] if numero_delimitado(inline) else []
    else:
        # The confirmed local label can disambiguate a primary OCR line spanning columns.
        fragments = [(t['text'], t['confidence'], _caja([t])) for t in documento['tokens']]
        values = [(raw, conf, value_box) for raw, conf, value_box in (fragments if local else lines)
                  if numero_delimitado(raw) and _vecino(box, value_box)]
        if len(values) == 1:
            # A nearby date/code label makes a digits-only line ambiguous.
            if any(_vecino(other_box, values[0][2]) for raw, _, other_box in lines
                   if re.match(r'^(?:FECHA|SERIE|CODIGO|COD\.)(?:\s|:|$)', normalizar_texto(raw))):
                return '', None
            if local and any(_vecino(box, fragment_box) and re.fullmatch(r'[0-9., /-]+', raw)
                             and (raw, conf, fragment_box) != values[0] for raw, conf, fragment_box in fragments):
                return '', None
    if len(values) != 1:
        return '', None
    raw, value_conf, _ = values[0]
    if label == 'NUIP' and len(numero_delimitado(raw)) != 10:
        return '', None
    return (raw, min(confidence, value_conf)) if value_conf is not None else ('', None)


def _clasificar_espacial(documento, *, reverso_de_cc=False):
    tokens = documento['tokens']
    lines = _lineas(tokens)
    text = normalizar_texto(' '.join(raw for raw, _ in lines))
    if OTRO_DOCUMENTO.search(text):
        return 'NO_SOPORTADO', 'DESCONOCIDO', lines, ('', None)
    number = _numero_espacial(documento)
    cc = tiene_cabecera(tokens) or documento.get('rescate', {}).get('cabecera') is True
    name_fields = all(_campo(lines, key)[0] for key in ('APELLIDOS', 'NOMBRES'))
    birth = 'FECHA DE NACIMIENTO' in text
    issued = any(label in text for label in ('LUGAR DE EXPEDICION', 'FECHA DE EXPEDICION',
                                             'FECHA Y LUGAR DE EXPEDICION'))
    rear = ((cc and any(_campo(lines, key)[0] for key in ('FECHA DE NACIMIENTO', 'FECHA DE EXPEDICION')))
            or (reverso_de_cc and birth and issued and not name_fields))
    front = cc and (name_fields or (not rear and number[1] is not None and number[1] >= CONFIANZA_MINIMA))
    side = 'FRONTAL' if front and not rear else 'TRASERA' if rear and not front else 'DESCONOCIDO'
    return 'CC_COLOMBIA' if cc or rear else 'DESCONOCIDO', side, lines, number


def parsear(frontal, trasera, *, version=VERSION):
    if version not in VERSIONES:
        raise ValueError('OCR_VERSION_PARSER_INVALIDA')
    espacial = version == VERSION
    if espacial:
        ft, fl, front, number = _clasificar_espacial(frontal)
        bt, bl, back, back_number = _clasificar_espacial(trasera, reverso_de_cc=fl == 'FRONTAL')
    else:
        ft, fl, front = clasificar(frontal['tokens'])
        bt, bl, back = clasificar(trasera['tokens'])
    result = {'tipo_documento_detectado': ft if ft == bt else 'DESCONOCIDO',
              'lado_frontal_detectado': fl, 'lado_trasera_detectado': bl,
              'confianza': {}, 'evidencia': {'layout': version, 'codigos': []}}
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
        if espacial and field == 'numero_documento':
            raw, confidence = number if fl == 'FRONTAL' and bl != 'FRONTAL' and bt != 'NO_SOPORTADO' else ('', None)
            if (raw and back_number[0] and back_number[1] >= CONFIANZA_MINIMA
                    and numero_delimitado(raw) != numero_delimitado(back_number[0])):
                codes.append('NUMERO_CONTRADICTORIO')
                raw, confidence = '', None
        else:
            raw, confidence = _campo(front, label) if fl == 'FRONTAL' else ('', None)
        limit = 80 if field == 'numero_documento' else 240
        normalized = normalizar_numero(raw) if field == 'numero_documento' else normalizar_texto(raw)
        if len(raw) > limit or confidence is None or confidence < CONFIANZA_MINIMA:
            normalized = ''
        result[field + '_bruto'] = raw[:limit]
        result[field + '_normalizado'] = normalized[:limit]
        result['confianza'][field] = confidence
        if not normalized:
            codes.append('NUMERO_NO_ENCONTRADO' if field == 'numero_documento' else 'NOMBRE_NO_LEIBLE')
    for field, label in [('fecha_nacimiento', 'FECHA DE NACIMIENTO'), ('fecha_expedicion', 'FECHA DE EXPEDICION')]:
        raw, confidence = _campo(back, label) if bl == 'TRASERA' else ('', None)
        result[field] = fecha_inequivoca(raw) if confidence is not None and confidence >= CONFIANZA_MINIMA else None
        result['confianza'][field] = confidence
        present = espacial and bl == 'TRASERA' and any(
            normalizar_texto(line).startswith(label) for line, _ in back)
        if (raw or present) and not result[field]:
            codes.append('FECHA_NO_INTERPRETABLE')
    raw, confidence = _campo(back, 'LUGAR DE EXPEDICION') if bl == 'TRASERA' else ('', None)
    result['lugar_expedicion_bruto'] = raw[:240]
    result['confianza']['lugar_expedicion'] = confidence
    result['lugar_expedicion_normalizado'] = normalizar_texto(raw)[:240] if confidence is not None and confidence >= CONFIANZA_MINIMA else ''
    result['codigo_error'] = codes[0] if codes else ''
    result['estado'] = 'REQUIERE_REVISION' if codes else 'COMPLETADO'
    return result

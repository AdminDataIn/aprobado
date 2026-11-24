"""Isolated local runner; stdout is consumed privately, never logged."""
import json
import sys
import tempfile
import time

from .parser_documento_colombia import (
    CONFIANZA_MINIMA, ETIQUETA_NUMERO, OTRO_DOCUMENTO, ancla_recuperada, normalizar_texto,
    numero_delimitado, tiene_cabecera, _lineas,
)


def _tokens(ocr, source, tessdata, psm, deadline):
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise RuntimeError('timeout')
    data = ocr.image_to_data(source, lang='spa', config=f'--tessdata-dir "{tessdata}" --oem 1 --psm {psm}',
                             timeout=timeout, output_type=ocr.Output.DICT)
    return [{'text': raw, 'confidence': float(data['conf'][i]),
             'line': [data[k][i] for k in ('page_num', 'block_num', 'par_num', 'line_num')],
             'box': [data[k][i] for k in ('left', 'top', 'width', 'height')]}
            for i, raw in enumerate(data['text']) if raw.strip()]


def _rescate_frontal(path, tokens, read_region):
    from PIL import Image, ImageOps

    text = normalizar_texto(' '.join(t['text'] for t in tokens))
    if ('REPUBLICA DE COLOMBIA' not in text or 'CIUDADANIA' not in text or OTRO_DOCUMENTO.search(text)):
        return {}
    # A clear existing anchor, including an ambiguous label set, is never replaced.
    if any(ETIQUETA_NUMERO.fullmatch(normalizar_texto(raw)) for raw, _ in _lineas(tokens)):
        return {}
    seeds = [t for t in tokens if t['confidence'] >= CONFIANZA_MINIMA and numero_delimitado(t['text'])]
    if len(seeds) != 1:
        return {}
    x, y, w, h = seeds[0]['box']
    with Image.open(path) as image:
        if not (h >= 8 and w >= 2 * h and 0 <= x < image.width and .12 * image.height <= y <= .62 * image.height):
            return {}

        def region(left, top, right, bottom):
            bounds = (max(0, round(left)), max(0, round(top)),
                      min(image.width, round(right)), min(image.height, round(bottom)))
            if bounds[2] - bounds[0] < h or bounds[3] - bounds[1] < h:
                return [], bounds
            crop = ImageOps.autocontrast(image.crop(bounds).convert('L'), cutoff=1)
            with crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS) as enlarged:
                return read_region(enlarged), bounds

        words, bounds = region(x - 4.5 * h, y - .25 * h, x - 1, y + 1.5 * h)
        anchor = ancla_recuperada(words)
        if not anchor or not anchor[2]:
            return {}
        if not tiene_cabecera(tokens):
            header, _ = region(x - 5.75 * h, y - 6.25 * h, x + w + 8.25 * h, y + .35 * h)
            if not tiene_cabecera(header):
                return {}
        raw, confidence, (left, top, width, height) = anchor
        return {'cabecera': True, 'etiqueta': (raw, confidence,
                (bounds[0] + left / 2, bounds[1] + top / 2, width / 2, height / 2))}


def main():
    try:
        import pytesseract
        params = json.load(sys.stdin)
        tempfile.tempdir = params['temporary']
        if params['executable']:
            pytesseract.pytesseract.tesseract_cmd = params['executable']
        version = str(pytesseract.get_tesseract_version())
        if version != params['version_motor']:
            print(json.dumps({'error': 'OCR_VERSION_MOTOR_INVALIDA'}))
            return
        deadline = time.monotonic() + params['timeout']
        tokens = _tokens(pytesseract, params['path'], params['tessdata'], 6, deadline)
        result = {'tokens': tokens, 'version': version}
        if params.get('rescate_frontal'):
            try:
                result['rescate'] = _rescate_frontal(params['path'], tokens,
                    lambda region: _tokens(pytesseract, region, params['tessdata'], 11, deadline))
            except RuntimeError as exc:
                if 'timeout' not in str(exc).lower():
                    raise
                # A timed-out optional label read cannot supply evidence.
        print(json.dumps(result))
    except Exception as exc:
        code = 'OCR_ERROR_TECNICO'
        if type(exc).__name__ == 'TesseractNotFoundError':
            code = 'OCR_MOTOR_NO_DISPONIBLE'
        elif isinstance(exc, RuntimeError) and 'timeout' in str(exc).lower():
            code = 'OCR_TIMEOUT'
        print(json.dumps({'error': code}))


if __name__ == '__main__':
    main()

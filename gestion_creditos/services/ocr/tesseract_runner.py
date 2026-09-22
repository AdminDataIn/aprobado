"""Isolated local runner; stdout is consumed privately, never logged."""
import json
import sys
import tempfile


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
        data = pytesseract.image_to_data(params['path'], lang='spa',
            config=f'--tessdata-dir "{params["tessdata"]}" --oem 1 --psm 6',
            timeout=params['timeout'], output_type=pytesseract.Output.DICT)
        tokens = []
        for i, raw in enumerate(data['text']):
            if raw.strip():
                tokens.append({'text': raw, 'confidence': float(data['conf'][i]),
                    'line': [data[k][i] for k in ('page_num', 'block_num', 'par_num', 'line_num')],
                    'box': [data[k][i] for k in ('left', 'top', 'width', 'height')]})
        print(json.dumps({'tokens': tokens, 'version': version}))
    except Exception as exc:
        code = 'OCR_ERROR_TECNICO'
        if type(exc).__name__ == 'TesseractNotFoundError':
            code = 'OCR_MOTOR_NO_DISPONIBLE'
        elif isinstance(exc, RuntimeError) and 'timeout' in str(exc).lower():
            code = 'OCR_TIMEOUT'
        print(json.dumps({'error': code}))


if __name__ == '__main__':
    main()

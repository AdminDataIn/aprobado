"""Private, bounded Tesseract execution. No document data leaves this host."""
import hashlib
import tempfile
import json
import subprocess
import sys
import shutil
import importlib.util
from pathlib import Path

from django.conf import settings


class ErrorOCR(Exception):
    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)


def extraer(captura, configuracion):
    # pytesseract accepts a filename, avoiding its implicit image tempfile.
    # Output tempfiles are forced into this same private temporary directory.
    try:
        import os

        executable = shutil.which(getattr(settings, 'TESSERACT_CMD', '') or 'tesseract')
        if not executable or importlib.util.find_spec('pytesseract') is None:
            raise ErrorOCR('OCR_MOTOR_NO_DISPONIBLE')
        tessdata = Path(getattr(settings, 'OCR_DOCUMENTAL_TESSDATA_DIR', ''))
        model = tessdata / 'spa.traineddata'
        if not model.is_file():
            raise ErrorOCR('OCR_MODELO_NO_DISPONIBLE')
        if hashlib.sha256(model.read_bytes()).hexdigest() != configuracion['modelo_sha256']:
            raise ErrorOCR('OCR_MODELO_INVALIDO')
        root = Path(settings.PRIVATE_DOCUMENTS_ROOT) / 'ocr_temporales'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            os.chmod(temporary, 0o700)
            path = Path(temporary) / 'input.jpg'
            with captura.archivo.open('rb') as source, path.open('wb') as target:
                digest = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: source.read(65536), b''):
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise ErrorOCR('OCR_INPUT_MODIFICADO')
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != captura.hash_archivo:
                raise ErrorOCR('OCR_INPUT_MODIFICADO')
            # Isolate tempfile configuration from other OCR users/worker threads.
            result = subprocess.run([sys.executable, '-m',
                'gestion_creditos.services.ocr.tesseract_runner'],
                input=json.dumps({'temporary': temporary, 'path': str(path),
                    'tessdata': str(tessdata.resolve()), 'executable': executable,
                    'version_motor': configuracion['version_motor'],
                    'timeout': configuracion['timeout']}), capture_output=True, text=True,
                timeout=configuracion['timeout'] + 15, check=True)
            payload = json.loads(result.stdout)
            if payload.get('error'):
                raise ErrorOCR(payload['error'])
            if payload['version'] != configuracion['version_motor']:
                raise ErrorOCR('OCR_VERSION_MOTOR_INVALIDA')
            return {'version': payload['version'], 'tokens': payload['tokens']}
    except ErrorOCR:
        raise
    except ImportError:
        raise ErrorOCR('OCR_MOTOR_NO_DISPONIBLE') from None
    except Exception as exc:
        if type(exc).__name__ == 'TesseractNotFoundError':
            code = 'OCR_MOTOR_NO_DISPONIBLE'
        elif isinstance(exc, subprocess.TimeoutExpired):
            code = 'OCR_TIMEOUT'
        else:
            code = 'OCR_ERROR_TECNICO'
        raise ErrorOCR(code) from None

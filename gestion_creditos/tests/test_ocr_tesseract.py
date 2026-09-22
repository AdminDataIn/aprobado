"""Adapter isolation tests plus opt-in real-engine integration on synthetic data."""
import hashlib
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from gestion_creditos.services.ocr import tesseract_adapter as adapter


class TesseractAdapterTest(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = override_settings(PRIVATE_DOCUMENTS_ROOT=temporary.name, OCR_DOCUMENTAL_TESSDATA_DIR=temporary.name)
        settings.enable()
        self.addCleanup(settings.disable)
        (self.root / 'spa.traineddata').write_bytes(b'synthetic-model')
        self.config = {'version_motor': 'test-1', 'modelo_sha256': hashlib.sha256(b'synthetic-model').hexdigest(), 'timeout': 1}
        self.file = ContentFile(b'synthetic-image', name='synthetic.jpg')
        self.capture = SimpleNamespace(archivo=self.file, hash_archivo=hashlib.sha256(b'synthetic-image').hexdigest())
        available = patch.object(adapter.shutil, 'which', return_value='tesseract-test')
        available.start()
        self.addCleanup(available.stop)

    def test_temporales_privados_eliminados_y_sin_url(self):
        def run(args, **kwargs):
            params = json.loads(kwargs['input'])
            self.assertTrue(Path(params['path']).is_relative_to(self.root))
            self.assertEqual(Path(params['path']).read_bytes(), b'synthetic-image')
            self.assertNotIn('synthetic-image', kwargs['input'])
            return SimpleNamespace(stdout=json.dumps({'tokens': [], 'version': 'test-1'}))
        with patch.object(adapter.subprocess, 'run', side_effect=run):
            self.assertEqual(adapter.extraer(self.capture, self.config)['tokens'], [])
        self.assertEqual(list((self.root / 'ocr_temporales').iterdir()), [])

    def test_fallo_no_filtra_excepcion_y_limpia_temporales(self):
        with patch.object(adapter.subprocess, 'run', side_effect=ValueError('NUMERO-PII')):
            with self.assertRaises(adapter.ErrorOCR) as error:
                adapter.extraer(self.capture, self.config)
        self.assertEqual(str(error.exception), 'OCR_ERROR_TECNICO')
        self.assertEqual(list((self.root / 'ocr_temporales').iterdir()), [])

    def test_binario_ausente_codigo_seguro(self):
        with patch.object(adapter.shutil, 'which', return_value=None):
            with self.assertRaises(adapter.ErrorOCR) as error:
                adapter.extraer(self.capture, self.config)
        self.assertEqual(str(error.exception), 'OCR_MOTOR_NO_DISPONIBLE')

    def test_timeout_seguro(self):
        with patch.object(adapter.subprocess, 'run',
                side_effect=adapter.subprocess.TimeoutExpired('private-path', 1)):
            with self.assertRaises(adapter.ErrorOCR) as error:
                adapter.extraer(self.capture, self.config)
        self.assertEqual(str(error.exception), 'OCR_TIMEOUT')

    def test_modelo_y_input_no_coincidentes(self):
        for config, digest, expected in [({**self.config, 'modelo_sha256': 'wrong'}, self.capture.hash_archivo, 'OCR_MODELO_INVALIDO'),
                (self.config, 'wrong', 'OCR_INPUT_MODIFICADO')]:
            self.capture.hash_archivo = digest
            with self.assertRaises(adapter.ErrorOCR) as error:
                adapter.extraer(self.capture, config)
            self.assertEqual(str(error.exception), expected)


class TesseractRealTest(SimpleTestCase):
    def test_motor_real_fixture_sintetico(self):
        try:
            import pytesseract
            version = str(pytesseract.get_tesseract_version())
            langs = pytesseract.get_languages(config='')
        except Exception:
            self.skipTest('Tesseract local no disponible; integrar en VPS con binario y spa')
        if 'spa' not in langs:
            self.skipTest('Tesseract sin idioma spa; requiere modelo espanol en VPS')
        model_dir = os.environ.get('OCR_DOCUMENTAL_TESSDATA_DIR', '')
        model = Path(model_dir) / 'spa.traineddata'
        if not model_dir or not model.is_file():
            self.skipTest('Definir OCR_DOCUMENTAL_TESSDATA_DIR para validar el adaptador real')
        with tempfile.TemporaryDirectory() as root, override_settings(PRIVATE_DOCUMENTS_ROOT=root,
                OCR_DOCUMENTAL_TESSDATA_DIR=model_dir):
            image = Image.new('RGB', (1600, 350), 'white')
            draw = ImageDraw.Draw(image)
            draw.text((40, 50), 'NUMERO 99000123\nPERSONA FICTICIA', fill='black', font=ImageFont.load_default(size=60))
            content = BytesIO()
            image.save(content, format='JPEG', quality=95)
            raw = content.getvalue()
            capture = SimpleNamespace(archivo=ContentFile(raw, name='sintetico.jpg'), hash_archivo=hashlib.sha256(raw).hexdigest())
            result = adapter.extraer(capture, {'version_motor': version,
                'modelo_sha256': hashlib.sha256(model.read_bytes()).hexdigest(), 'timeout': 20})
            self.assertIn('99000123', ' '.join(t['text'] for t in result['tokens']))
            self.assertEqual(list((Path(root) / 'ocr_temporales').iterdir()), [])

import hashlib
import io
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from gestion_creditos.services.ocr import parser_documento_colombia as parser
from gestion_creditos.services.ocr import tesseract_adapter, tesseract_runner


@skipUnless(os.environ.get('ID01E_TESSDATA'), 'Motor OCR real opt-in, sin descargas en runtime')
class TesseractEspacialTest(SimpleTestCase):
    @staticmethod
    def card(text, *, angle=0, blank=False):
        # Invented layout, no government styling or likeness.
        image = Image.new('RGB', (1000, 650), '#f4f7f4')
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 991, 641), outline='#253b43', width=3)
        font = ImageFont.truetype(os.environ['ID01E_TEST_FONT'], 30)
        if not blank:
            for y, line in enumerate(text, 1):
                draw.text((55, y * 66), line, fill='#202727', font=font)
        with io.BytesIO() as output:
            image.rotate(angle, expand=True).save(output, 'JPEG', quality=90)
            return output.getvalue()

    def test_tesseract_real_ambas_caras_y_calidad_insuficiente(self):
        import pytesseract

        tessdata = Path(os.environ['ID01E_TESSDATA'])
        opts = {'version_motor': str(pytesseract.get_tesseract_version()),
                'modelo_sha256': hashlib.sha256((tessdata / 'spa.traineddata').read_bytes()).hexdigest(),
                'timeout': 20, 'parser': parser.VERSION}
        front_lines = ['REPUBLICA DE COLOMBIA', 'CEDULA DE CIUDADANIA', 'NUIP: 9900012305',
                       'APELLIDOS: PERSONA DE ENSAYO', 'NOMBRES: DATOS SINTETICOS', 'ENSAYO NO LEGAL']
        back_lines = ['FECHA DE NACIMIENTO: 1990-01-20', 'FECHA DE EXPEDICION: 2010-02-22', 'ENSAYO NO LEGAL']
        with tempfile.TemporaryDirectory() as root, override_settings(
                PRIVATE_DOCUMENTS_ROOT=root, OCR_DOCUMENTAL_TESSDATA_DIR=str(tessdata)):
            def read(data, side):
                file = SimpleNamespace(lado=side, archivo=ContentFile(data), hash_archivo=hashlib.sha256(data).hexdigest())
                return tesseract_adapter.extraer(file, opts)

            back = read(self.card(back_lines), 'TRASERA')
            result = parser.parsear(read(self.card(front_lines), 'FRONTAL'), back)
            self.assertEqual(result['numero_documento_normalizado'], '9900012305')
            self.assertEqual(result['estado'], 'COMPLETADO')
            for distorted in (self.card(front_lines, angle=90), self.card(front_lines, blank=True)):
                result = parser.parsear(read(distorted, 'FRONTAL'), back)
                self.assertEqual(result['estado'], 'REQUIERE_REVISION')
                self.assertFalse(result['numero_documento_normalizado'])
            self.assertFalse(list((Path(root) / 'ocr_temporales').iterdir()))

    def test_rescate_real_de_etiqueta_en_una_region_sintetica(self):
        import pytesseract

        tessdata = os.environ['ID01E_TESSDATA']
        font = ImageFont.truetype(os.environ['ID01E_TEST_FONT'], 21)
        with tempfile.TemporaryDirectory() as folder, patch.object(tempfile, 'tempdir', folder):
            path = Path(folder) / 'ensayo.jpg'
            image = Image.new('RGB', (1000, 650), 'white')
            draw = ImageDraw.Draw(image)
            for y, text in ((60, 'REPUBLICA DE COLOMBIA'), (120, 'CEDULA DE CIUDADANIA')):
                draw.text((90, y), text, fill='black', font=font)
            draw.text((94, 204), 'NUMERO', fill='black', font=font)
            draw.text((207, 204), '99.000.123', fill='black', font=font)
            image.save(path, 'JPEG')
            primary = [{'text': text, 'box': box, 'confidence': 95, 'line': [1, 1, 1, i]}
                       for i, (text, box) in enumerate((('REPUBLICA DE COLOMBIA', [90, 60, 300, 20]),
                           ('CIUDADANIA', [190, 120, 180, 20]), ('99.000.123', [207, 206, 135, 30])), 1)]
            deadline = time.monotonic() + 20
            recovered = tesseract_runner._rescate_frontal(path, primary,
                lambda region: tesseract_runner._tokens(pytesseract, region, tessdata, 11, deadline))
            result = parser.parsear({'tokens': primary, 'rescate': recovered}, {'tokens': []})
            self.assertEqual(result['numero_documento_normalizado'], '99000123')
            self.assertEqual(result['estado'], 'REQUIERE_REVISION')
            self.assertEqual(list(Path(folder).iterdir()), [path])

from django.test import SimpleTestCase

from gestion_creditos.services.ocr import parser_documento_colombia as parser
from gestion_creditos.tests.test_ocr_documental import FRONTAL, TRASERA, documento


def ficha(*lines):
    return {'tokens': [{'text': text, 'confidence': conf, 'line': [1, 1, 1, i + 1], 'box': box}
                       for i, (text, box, conf) in enumerate(lines)]}


def row(text, x, y, width=100, confidence=95, height=20):
    return text, [x, y, width, height], confidence


HEADER = (
    row('REPUBLICA DE COLOMBIA', 30, 30, 280),
    row('CEDULA DE CIUDADANIA', 30, 60, 280),
)
FRONT_FIELDS = (
    row('APELLIDOS: PERSONA DE ENSAYO', 30, 300, 325),
    row('NOMBRES: DATOS SINTETICOS', 30, 340, 300),
)
REAR_NO_TITLE = documento(['FECHA DE NACIMIENTO: 1990-01-20', 'FECHA DE EXPEDICION: 2010-02-22'])


class ParserEspacialTest(SimpleTestCase):
    def extraido(self, *values, back=REAR_NO_TITLE):
        return parser.parsear(ficha(*HEADER, *values, *FRONT_FIELDS), back)

    def test_numero_a_la_derecha_en_otra_linea_ocr_y_ajeno_distante(self):
        result = self.extraido(row('N\u00famero', 30, 140), row('99.000.123', 157, 142),
                              row('8811122233', 470, 142), row('CODIGO: 88112233', 320, 280))
        self.assertEqual(result['numero_documento_normalizado'], '99000123')
        self.assertEqual(result['estado'], 'COMPLETADO')
        self.assertEqual(result['lado_trasera_detectado'], 'TRASERA')
        self.assertEqual(result['evidencia']['layout'], parser.VERSION)

    def test_nuip_debajo_y_separadores_de_miles(self):
        result = self.extraido(row('NUIP', 30, 120), row('9 900 012 305', 34, 145, 155))
        self.assertEqual(result['numero_documento_normalizado'], '9900012305')
        self.assertEqual(result['tipo_documento_detectado'], 'CC_COLOMBIA')

    def test_numero_en_linea_con_variantes_de_etiqueta(self):
        for label in ('N\u00famero de documento', 'N\u00famero de identificaci\u00f3n', 'N\u00fam.'):
            with self.subTest(label=label):
                self.assertEqual(self.extraido(row(label + ' 99-000-123', 30, 142, 330))
                                 ['numero_documento_normalizado'], '99000123')

    def test_etiqueta_truncada_solo_si_relectura_confirma(self):
        primary = ficha(HEADER[0], row('CIUDADANIA', 30, 60), row('NUMER', 30, 140),
                        row('99.000.123', 148, 142))
        self.assertFalse(parser.parsear(primary, REAR_NO_TITLE)['numero_documento_normalizado'])
        primary['rescate'] = {'cabecera': True, 'etiqueta': ('NUMERO', 95, [30, 140, 100, 20])}
        primary['tokens'].append({'text': 'TEXTO', 'confidence': 90, 'box': [500, 140, 100, 20],
                                  'line': primary['tokens'][-1]['line']})
        result = parser.parsear(primary, REAR_NO_TITLE)
        self.assertEqual(result['numero_documento_normalizado'], '99000123')
        self.assertEqual(result['estado'], 'REQUIERE_REVISION')
        self.assertFalse(result['nombres_normalizado'])
        primary['rescate']['etiqueta'] = ('NUMERO', 69, [30, 140, 100, 20])
        self.assertFalse(parser.parsear(primary, REAR_NO_TITLE)['numero_documento_normalizado'])

    def test_rechaza_fecha_codigos_candidatos_ambiguos_y_digitos_incompletos(self):
        near = row('NUMERO', 30, 140)
        for lines in (
            (near, row('99000123', 148, 142), row('88001234', 151, 148)),
            (near, row('99000123', 148, 142, confidence=69)),
            (near, row('99000123', 510, 140)),
            (near, row('2001-08-07', 148, 142)),
            (near, row('19/02/1990', 148, 142)),
            (near, row('19900120', 150, 142), row('FECHA DE NACIMIENTO', 31, 145)),
            (near, row('99000123', 150, 142), row('SERIE', 31, 145)),
            (near, row('99001', 148, 142)),
            (near, row('99000123 NUMERACION DE SERIE', 148, 142)),
            (row('NUIP: 99000123', 30, 140),),
            (row('NUMERO: 99.00O.123', 30, 140),),
            (row('NUMERO: 99.000-123', 30, 140),),
            (row('NUMERO: 331 07 2026', 30, 140),),
            (row('FIRMA: 99000123', 30, 140),),
        ):
            with self.subTest(case=lines):
                result = self.extraido(*lines)
                self.assertFalse(result['numero_documento_normalizado'])
                self.assertEqual(result['estado'], 'REQUIERE_REVISION')

    def test_repeticion_del_numero_en_reverso_explicita_y_coherente(self):
        tail = ['FECHA DE NACIMIENTO: 1990-01-20', 'FECHA DE EXPEDICION: 2010-02-22']
        same = self.extraido(row('NUMERO: 99000123', 30, 140), back=documento(tail + ['NUMERO: 99000123']))
        diff = self.extraido(row('NUMERO: 99000123', 30, 140), back=documento(tail + ['NUMERO: 88000123']))
        self.assertEqual(same['numero_documento_normalizado'], '99000123')
        self.assertFalse(diff['numero_documento_normalizado'])
        self.assertIn('NUMERO_CONTRADICTORIO', diff['evidencia']['codigos'])

    def test_otra_clase_o_ambas_caras_frontales_no_publican_numero(self):
        number = row('NUMERO: 99000123', 30, 140)
        front = ficha(*HEADER, number, *FRONT_FIELDS)
        for back in (front, documento(['CEDULA DE EXTRANJERIA']), documento(['TARJETA DE IDENTIDAD'])):
            self.assertFalse(parser.parsear(front, back)['numero_documento_normalizado'])

    def test_trabajo_preexistente_procesa_con_version_original(self):
        old = parser.parsear(FRONTAL, TRASERA, version=parser.VERSION_ANTERIOR)
        self.assertEqual(old['numero_documento_normalizado'], '99000123')
        self.assertEqual(old['evidencia']['layout'], parser.VERSION_ANTERIOR)
        self.assertFalse(parser.parsear(documento(['REPUBLICA DE COLOMBIA', 'CEDULA DE CIUDADANIA',
            'NUIP: 9900012305']), TRASERA, version=parser.VERSION_ANTERIOR)['numero_documento_normalizado'])

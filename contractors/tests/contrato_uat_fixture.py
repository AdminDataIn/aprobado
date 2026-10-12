"""Textual PDF fixture preserving the relevant clauses of the supplied UAT PDF."""
from html import escape

from django.core.files.uploadedfile import SimpleUploadedFile
from weasyprint import HTML


TEXTO_UAT = '''CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES
Entre los suscritos a saber:
PRUEBAS DATAIN, identificada con NIT 999888777, con domicilio principal en Villavicencio Meta.,
representada legalmente por quien haga sus veces, quien para efectos del presente contrato se
denominará EL CONTRATANTE; y
CARLOS DANIEL ORTIZ ANGEL, identificado con cédula de ciudadanía No. 1.006.442.329,
domiciliado en Colombia, quien para efectos del presente contrato se denominará EL
CONTRATISTA;
PRIMERA. OBJETO DEL CONTRATO
EL CONTRATISTA se obliga a prestar sus servicios profesionales como Project Manager, liderando
la planeación, coordinación, ejecución, seguimiento y cierre de proyectos tecnológicos
desarrollados por EL CONTRATANTE.
SEGUNDA. VALOR DEL CONTRATO
El valor total del presente contrato será de:
CIENTO VEINTICUATRO MILLONES OCHOCIENTOS MIL PESOS M/CTE ($124.800.000 COP)
Este valor incluye todos los honorarios correspondientes a la ejecución del objeto contractual.
TERCERA. FORMA DE PAGO
EL CONTRATANTE cancelará el valor del contrato mediante doce (12) pagos mensuales de:
$10.400.000 COP
Cada pago será realizado dentro de los primeros cinco (5) días hábiles del mes siguiente al
período causado, previa presentación de la cuenta de cobro correspondiente. A la fecha de
suscripción del presente contrato no se registra ningún pago efectuado.
CUARTA. PLAZO DEL CONTRATO
El presente contrato tendrá una duración de:
Doce (12) meses
Fecha de inicio:
01 de agosto de 2026
Fecha de terminación:
31 de julio de 2027
'''


def pdf_uat():
    contenido = HTML(string='<div style="white-space:pre-wrap">' + escape(TEXTO_UAT) + '</div>').write_pdf()
    return SimpleUploadedFile('contrato-sintetico-uat.pdf', contenido, content_type='application/pdf')


TEXTO_UAT_CALENDARIO = '''CONTRATO DE PRUEBA DE PRESTACION DE SERVICIOS
Contratante: Empresa Convenio
Contratista: Ana Maria Perez Gomez
Documento: 123456789
Objeto: Asesoria juridica
HONORARIOS
El CONTRATANTE pagara a la CONTRATISTA honorarios de $7.000.000 COP mensuales, para un valor total de
prueba de $84.000.000 COP por doce (12) meses, previa presentacion de la cuenta de cobro correspondiente.
DURACION
La duracion simulada sera de doce (12) meses, desde el 01 de julio de 2026 hasta el 30 de junio de 2027,
fechas tomadas del documento de referencia.
'''


def pdf_uat_calendario(*, adenda=''):
    html = ('<div style="position:fixed;top:15px">PRUEBA - SIN VALIDEZ</div>'
            '<div style="white-space:pre-wrap;margin-top:50px">'
            + escape(TEXTO_UAT_CALENDARIO + adenda) + '</div>')
    return SimpleUploadedFile('contrato-calendario-sintetico.pdf', HTML(string=html).write_pdf(),
                              content_type='application/pdf')

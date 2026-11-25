"""Interchange workbooks. Nothing here authorizes or performs remittance."""
from datetime import date
from decimal import Decimal
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


REFERENCIA_FACILIDAT_SHA256 = 'dd57903ab11cfedd628b7d188d2610b5e7632b70f5cdca4149421a285b761e09'
XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
ENCABEZADOS_FACILIDAT = (
    'TIPO DE IDENTIFICACION', 'NUMERO DE IDENTIFICACION', 'NUMERO DE LA CUENTA U OBLIGACION',
    'NOMBRE COMPLETO', 'SITUACION DEL TITULAR', 'FECHA APERTURA', 'FECHA VENCIMIENTO', 'RESPONSABLE',
    'FORMA DE PAGO', 'NOVEDAD', 'SITUACION CARTERA', 'FECHA SITUACION CARTERA', 'ESTADO DE LA CUENTA',
    'FECHA ESTADO DE LA CUENTA', 'ADJETIVO', 'FECHA DE ADJETIVO', 'CALIFICACION', 'EDAD DE MORA',
    'VALOR INICIAL', 'VALOR SALDO DEUDA', 'VALOR DISPONIBLE', 'V. CUOTA MENSUAL', 'VALOR SALDO MORA',
    'TOTAL CUOTAS', 'CUOTAS CANCELADAS', 'CUOTAS EN MORA', 'CLAUSULA DE PERMANENCIA',
    'FECHA CLAUSULA DE PERMANENCIA', 'FECHA LIMITE DE PAGO', 'FECHA DE PAGO', 'CIUDAD CORRESPONDENCIA',
    'CODIGO DANE CIUDAD CORRESPONDENCIA', 'DEPARTAMENTO DE CORRESPONDENCIA', 'DIRECCION DE CORRESPONDENCIA',
    'CORREO ELECTRONICO', 'CELULAR',
)
# Only meanings documented in the template. No implicit mapping from the Core status.
CATALOGOS_DOCUMENTADOS = {
    'A': frozenset('123456789'), 'E': frozenset('01234567'), 'H': frozenset('00 01 04 05 06 07 08'.split()),
    'I': frozenset('0123456789'), 'J': frozenset('01 05 06 07 08 09 12 13 14'.split()),
    'K': frozenset('0123456'), 'M': frozenset('1 2 3 4 5 6 7 10 11 12 13 14 15'.split()),
    'O': frozenset('00 01 02 03 04 05 06 07'.split()), 'Q': frozenset('ABCDE'),
}
_COLUMNAS_FECHA = frozenset('F G L N P AB AC AD'.split())
_COLUMNAS_COP = frozenset('S T U V W'.split())
_TEXTO_NUMERICO = re.compile(r'[0-9]+\Z')
_MONEDA = re.compile(r'[0-9]+(?:\.[0-9]{1,2})?\Z')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')

_EVIDENCIA = {
    'SIN_TRAZA_DE_DESEMBOLSO_AL_CORTE': (
        'HistorialEstado / comprobante bancario', 'Giro con fecha efectiva y vinculo a la activacion',
        'Localizar el comprobante y cruzar giro, obligacion y fecha.',
    ),
    'CUOTA_CON_MOVIMIENTO_SIN_SOPORTE_CONTABLE': (
        'CuotaAmortizacion / DetalleContablePago', 'Asiento, recibo o acta que explique la diferencia',
        'Cruzar la cuota con sus asientos y acreditar la diferencia.',
    ),
    'CIERRE_OPERATIVO_AUDITADO_CON_RESIDUO_FINANCIERO': (
        'Acta del Core / soporte contable del residuo', 'Tratamiento contable acreditado del residuo',
        'Conciliar el residuo; el cierre operativo no acredita recaudo.',
    ),
    'MONTO_PAGO_NO_CONCILIA_CON_IMPUTACIONES': (
        'HistorialPago / DetalleContablePago', 'Imputacion o conciliacion del remanente al corte',
        'Cruzar el pago con los asientos sin descontar diferencias por inferencia.',
    ),
    'PLAN_REESTRUCTURADO_REQUIERE_CONCILIACION_VERSIONADA': (
        'ReestructuracionCredito / plan historico', 'Plan y saldo a la fecha efectiva del abono',
        'Localizar el plan anterior al cambio y conciliarlo contra el abono.',
    ),
}
_FALLBACK = ('Fuente acreditada del cierre', 'Trazabilidad de la causa indicada',
             'Revisar el cierre; no imputar una diferencia no acreditada.')


def filas_sinteticas():
    # The cases match the prior local Facilidat test; no customer data or relative dates.
    casos = (
        ('01', '0', '01', '1', '000', '500', '0', '7', '0', '20190801', '20190701'),
        ('02', '0', '06', '2', '030', '700', '100', '5', '1', '20190601', '20190502'),
        ('03', '1', '05', '3', '000', '0', '0', '12', '0', '20190701', '20190701'),
    )
    return tuple(
        ('5', f'ZTEST00{sufijo}', f'SINTETICA{sufijo}', 'PERSONA SINTETICA NO REMITIR',
         '0', '20190101', '20200101', '00', forma, novedad, '0', '20190101', estado,
         '20190701', '00', '00000000', 'A', dias, '1200', saldo, '0', '100', mora,
         '12', pagadas, vencidas, '0', '00000000', limite, pago, 'BOGOTA', '11001', 'BOGOTA DC',
         'DIRECCION SINTETICA', 'prueba@example.invalid', '0000000000')
        for sufijo, forma, novedad, estado, dias, saldo, mora, pagadas, vencidas, limite, pago in casos
    )


def _validar_fila(fila):
    if len(fila) != len(ENCABEZADOS_FACILIDAT):
        raise ValueError('FILA_FACILIDAT_INCOMPLETA')
    for i, valor in enumerate(fila, 1):
        col = get_column_letter(i)
        if (type(valor) is not str or not valor or valor != valor.strip() or len(valor) > 32767
                or valor.startswith(('=', '+', '-', '@', "'")) or _CONTROL.search(valor)):
            raise ValueError(f'VALOR_NO_SEGURO_{col}')
        if col in CATALOGOS_DOCUMENTADOS and valor not in CATALOGOS_DOCUMENTADOS[col]:
            raise ValueError(f'CODIGO_NO_DOCUMENTADO_{col}')
        if col in _COLUMNAS_FECHA:
            if valor == '00000000' and col in ('P', 'AB'):
                continue
            if len(valor) != 8 or not _TEXTO_NUMERICO.fullmatch(valor):
                raise ValueError(f'FECHA_FACILIDAT_INVALIDA_{col}')
            date(int(valor[:4]), int(valor[4:6]), int(valor[6:]))
        if col in _COLUMNAS_COP and not _MONEDA.fullmatch(valor):
            raise ValueError(f'IMPORTE_FACILIDAT_INVALIDO_{col}')
        if col == 'R' and (len(valor) != 3 or not _TEXTO_NUMERICO.fullmatch(valor)):
            raise ValueError('EDAD_MORA_NO_REPRESENTABLE')


def _texto(celda, valor):
    if valor is not None:
        celda.value = str(valor)
        celda.data_type = 's'
    celda.number_format = '@'


def _encabezado(hoja, titulos, widths):
    for i, (titulo, width) in enumerate(zip(titulos, widths, strict=True), 1):
        celda = hoja.cell(1, i)
        _texto(celda, titulo)
        celda.fill = PatternFill('solid', fgColor='183652')
        celda.font = Font(color='FFFFFF', bold=True)
        celda.alignment = Alignment(wrap_text=True, vertical='center')
        hoja.column_dimensions[get_column_letter(i)].width = width
    hoja.row_dimensions[1].height = 34
    hoja.freeze_panes = 'B2'


def _nuevo_libro(nombre):
    libro = Workbook()
    libro.properties.creator = 'Aprobado'
    libro.properties.title = nombre
    libro.active.title = nombre
    return libro


def _bytes(libro):
    buffer = BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def _control(libro, detalles):
    hoja = libro.create_sheet('Control')
    _encabezado(hoja, ('Control', 'Valor'), (32, 100))
    for i, (clave, valor) in enumerate(detalles, 2):
        _texto(hoja.cell(i, 1), clave)
        _texto(hoja.cell(i, 2), valor)
        hoja.cell(i, 2).alignment = Alignment(wrap_text=True, vertical='top')
        hoja.row_dimensions[i].height = 31


def xlsx_sintetico_facilidat():
    libro = _nuevo_libro('Carga')
    hoja = libro.active
    _encabezado(hoja, ENCABEZADOS_FACILIDAT, [max(17, min(35, len(x) * 0.7)) for x in ENCABEZADOS_FACILIDAT])
    for i, fila in enumerate(filas_sinteticas(), 2):
        _validar_fila(fila)
        for j, valor in enumerate(fila, 1):
            _texto(hoja.cell(i, j), valor)
    _control(libro, (
        ('Uso', 'INTERMEDIO SINTETICO - NO REMITIR. Sin datos de titulares reales.'),
        ('Origen', REFERENCIA_FACILIDAT_SHA256),
        ('Region a copiar', 'Carga!A2:AJ4, sin encabezados.'),
        ('Destino', 'Copia de trabajo de Facilidat, Hoja1!A5. Pegado especial: solo valores.'),
        ('Fechas y valores', 'Texto AAAAMMDD y cantidades de prueba, sin escala inferida para casos reales.'),
        ('Validacion del operador', 'Pendiente. El XLSX no es la plantilla Facilidat ni un archivo de transmision.'),
    ))
    return _bytes(libro)


def _importe_observado(incidencia, evidencia):
    if incidencia == 'MONTO_PAGO_NO_CONCILIA_CON_IMPUTACIONES':
        items = [d for d in evidencia.get('diferencias', ()) if d.get('al_corte') is True]
        campo, clave = 'delta_pago_menos_asientos', 'pago_id'
        alcance = 'Diferencias absolutas pago/asientos al corte; no es saldo.'
    elif incidencia == 'CIERRE_OPERATIVO_AUDITADO_CON_RESIDUO_FINANCIERO':
        items = evidencia.get('cierres_con_residuo', ())
        campo, clave = 'residuo', 'cuota_id'
        alcance = 'Residuos documentados en cuotas; no es saldo al corte.'
    else:
        items = ()
    if not items:
        return None, 'Importe de incidencia no acreditado.'
    referencias = {item[clave] for item in items}
    montos = [Decimal(item[campo]) for item in items]
    if (len(referencias) != len(items) or any(not m.is_finite() or m == 0 for m in montos)
            or campo == 'residuo' and any(m < 0 for m in montos)):
        raise ValueError('IMPORTE_DE_EVIDENCIA_INCONSISTENTE')
    return sum(map(abs, montos), Decimal('0.00')), alcance


def xlsx_excepciones(cierre, huella, soportes_pendientes=None):
    libro = _nuevo_libro('Excepciones')
    hoja = libro.active
    _encabezado(hoja, (
        'Obligacion interna', 'Producto', 'Tipo de incidencia', 'Importe involucrado (COP)', 'Alcance del importe',
        'Fuente faltante', 'Evidencia requerida', 'Accion necesaria',
    ), (22, 20, 49, 25, 46, 42, 46, 58))
    pendientes = [f for f in cierre['filas'] if f.get('incidencia_financiera')]
    if soportes_pendientes is not None:
        soporte = {f['credito_id_interno']: f for f in soportes_pendientes}
        if (len(soporte) != len(soportes_pendientes)
                or soporte.keys() != {f['credito_id_interno'] for f in pendientes}):
            raise ValueError('SOPORTES_NO_COINCIDEN_CON_CIERRE')
    else:
        soporte = {}
    for i, fila in enumerate(pendientes, 2):
        credito = fila.get('numero_obligacion_interno') or str(fila['credito_id_interno'])
        causa = fila['incidencia_financiera']
        evidencia = soporte.get(fila['credito_id_interno'], {})
        if evidencia and (evidencia.get('control') != causa or evidencia.get('obligacion') != credito):
            raise ValueError('REFERENCIA_DE_SOPORTE_NO_COINCIDE')
        monto, alcance = _importe_observado(causa, evidencia)
        fuente, esperado, accion = _EVIDENCIA.get(causa, _FALLBACK)
        valores = credito, fila['producto'], causa, monto, alcance, fuente, esperado, accion
        for j, valor in enumerate(valores, 1):
            celda = hoja.cell(i, j)
            if j == 4:
                celda.value = valor
                celda.number_format = '#,##0.00'
            else:
                _texto(celda, valor)
            celda.alignment = Alignment(wrap_text=True, vertical='top')
        hoja.row_dimensions[i].height = 34
    hoja.auto_filter.ref = f'A1:H{len(pendientes)+1}'
    _control(libro, (
        ('Uso', 'INFORME INTERNO - NO REMITIR. Los importes no son ajustes ni saldo de cartera.'),
        ('Fecha de corte', cierre['corte']),
        ('Huella del cierre', huella),
        ('Incidencias financieras', len(pendientes)),
        ('Sin importe', 'Celda vacia cuando el importe de la incidencia no consta; no significa cero.'),
    ))
    return _bytes(libro)

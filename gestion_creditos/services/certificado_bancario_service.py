import logging
import re
import unicodedata
from io import BytesIO
from typing import Dict

from django.conf import settings
from django.utils import timezone
from pypdf import PdfReader


logger = logging.getLogger(__name__)


ESTADO_PENDIENTE = 'pendiente'
ESTADO_COMPLETO = 'completo'
ESTADO_ERROR = 'error'

BANCOS_CONOCIDOS = {
    'bancolombia': 'Bancolombia',
    'davivienda': 'Davivienda',
    'bbva': 'BBVA',
    'banco de bogota': 'Banco de Bogota',
    'banco de occidente': 'Banco de Occidente',
    'banco popular': 'Banco Popular',
    'banco caja social': 'Banco Caja Social',
    'scotiabank colpatria': 'Scotiabank Colpatria',
    'colpatria': 'Scotiabank Colpatria',
    'itau': 'Itau',
    'av villas': 'AV Villas',
    'avvillas': 'AV Villas',
    'banco agrario': 'Banco Agrario',
    'nequi': 'Nequi',
    'daviplata': 'Daviplata',
    'cuentamiga': 'Banco Caja Social',
    'vicepresidencia de banca masiva': 'Banco Caja Social',
}

PALABRAS_INVALIDAS_TITULAR = {
    'deposito',
    'bajo',
    'monto',
    'caracteristicas',
    'interesar',
    'equipo',
    'nequi',
    'banco',
    'bogota',
}


def _normalizar_busqueda(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.lower()


def _normalizar_texto_crudo(texto):
    texto = (texto or '').replace('\x00', ' ')
    texto = texto.replace('\r', '\n')
    texto = re.sub(r'\n{2,}', '\n', texto)
    texto = re.sub(r'[ \t]+', ' ', texto)
    return texto.strip()


def _compactar_texto(texto):
    return re.sub(r'\s+', ' ', (texto or '')).strip()


def _titulo_nombre(nombre):
    partes = [parte for parte in re.split(r'\s+', nombre.strip()) if parte]
    return ' '.join(parte.capitalize() for parte in partes)


def _limpiar_nombre_candidato(nombre):
    nombre = re.sub(r'[\r\n\t]+', ' ', nombre or '')
    nombre = re.sub(r'[^A-Za-z\s]', ' ', nombre)
    nombre = re.sub(r'\s+', ' ', nombre).strip()
    return nombre


def _es_nombre_valido(nombre):
    if not nombre:
        return False
    nombre_limpio = re.sub(r'[^A-Za-z\s]', ' ', nombre)
    nombre_limpio = re.sub(r'\s+', ' ', nombre_limpio).strip()
    palabras = nombre_limpio.split()
    if len(palabras) < 2:
        return False
    if any(_normalizar_busqueda(p) in PALABRAS_INVALIDAS_TITULAR for p in palabras):
        return False
    return True

def _titular_luce_incompleto(nombre):
    if not nombre:
        return True
    tokens = [t for t in re.split(r'\s+', nombre.strip()) if t]
    if len(tokens) < 2:
        return True
    single_chars = sum(1 for token in tokens if len(token) == 1)
    compact = ''.join(tokens)
    # Si tiene muchos tokens de una sola letra o muy poca longitud total,
    # suele venir truncado por la extraccion del PDF.
    return single_chars >= 2 or len(compact) < 8


def _normalizar_numero(numero):
    numero = re.sub(r'[^0-9]', '', numero or '')
    return numero if len(numero) >= 6 else ''


def extraer_texto_pdf(archivo_pdf):
    if hasattr(archivo_pdf, 'seek'):
        archivo_pdf.seek(0)

    contenido = archivo_pdf.read()
    if hasattr(archivo_pdf, 'seek'):
        archivo_pdf.seek(0)

    reader = PdfReader(BytesIO(contenido))
    paginas = [(page.extract_text() or '') for page in reader.pages]
    texto_crudo = _normalizar_texto_crudo('\n'.join(paginas))
    texto_compacto = _compactar_texto(texto_crudo)
    return {
        'texto_crudo': texto_crudo,
        'texto_compacto': texto_compacto,
        'paginas': len(reader.pages),
    }


def extraer_texto_pdf_ocr(archivo_pdf) -> Dict[str, object]:
    """
    Fallback OCR para PDFs escaneados sin capa de texto.

    Requiere herramientas open source:
    - pdf2image
    - pytesseract
    - binario tesseract en el sistema
    - poppler/pdftoppm (segun entorno)
    """
    ocr_habilitado = getattr(settings, 'BANK_CERT_OCR_ENABLED', True)
    if not ocr_habilitado:
        return {
            'texto_crudo': '',
            'texto_compacto': '',
            'paginas': 0,
            'ocr_aplicado': False,
            'ocr_disponible': False,
            'ocr_error': 'OCR deshabilitado por configuracion.',
        }

    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as exc:
        return {
            'texto_crudo': '',
            'texto_compacto': '',
            'paginas': 0,
            'ocr_aplicado': False,
            'ocr_disponible': False,
            'ocr_error': f'Dependencias OCR no disponibles: {exc}',
        }

    tesseract_cmd = getattr(settings, 'TESSERACT_CMD', '').strip()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    if hasattr(archivo_pdf, 'seek'):
        archivo_pdf.seek(0)
    contenido = archivo_pdf.read()
    if hasattr(archivo_pdf, 'seek'):
        archivo_pdf.seek(0)

    dpi = int(getattr(settings, 'BANK_CERT_OCR_DPI', 300) or 300)
    idioma = getattr(settings, 'BANK_CERT_OCR_LANG', 'spa')
    max_paginas = int(getattr(settings, 'BANK_CERT_OCR_MAX_PAGES', 3) or 3)
    poppler_path = getattr(settings, 'POPPLER_PATH', None)

    try:
        paginas_img = convert_from_bytes(
            contenido,
            dpi=dpi,
            fmt='jpeg',
            poppler_path=poppler_path,
            first_page=1,
            last_page=max_paginas,
        )
    except Exception as exc:
        return {
            'texto_crudo': '',
            'texto_compacto': '',
            'paginas': 0,
            'ocr_aplicado': True,
            'ocr_disponible': False,
            'ocr_error': f'No fue posible rasterizar el PDF para OCR: {exc}',
        }

    textos = []
    for imagen in paginas_img:
        try:
            texto = pytesseract.image_to_string(imagen, lang=idioma)
            if texto:
                textos.append(texto)
        except Exception as exc:
            logger.warning('Error OCR en pagina de certificado bancario: %s', exc)

    texto_crudo = _normalizar_texto_crudo('\n'.join(textos))
    texto_compacto = _compactar_texto(texto_crudo)

    return {
        'texto_crudo': texto_crudo,
        'texto_compacto': texto_compacto,
        'paginas': len(paginas_img),
        'ocr_aplicado': True,
        'ocr_disponible': True,
        'ocr_error': '',
    }


def _extraer_banco(texto_compacto):
    texto_busqueda = _normalizar_busqueda(texto_compacto)
    for patron, nombre in BANCOS_CONOCIDOS.items():
        if patron in texto_busqueda:
            return nombre
    return ''


def _buscar_patron(texto, patrones, flags=re.IGNORECASE | re.DOTALL):
    for patron in patrones:
        match = re.search(patron, texto, flags=flags)
        if match:
            valor = (match.group(1) or '').strip()
            if valor:
                return valor
    return ''


def _extraer_titular_generico(texto_crudo, texto_compacto):
    candidatos = [
        _buscar_patron(texto_crudo, [
            r'informa\s+que\s+([A-Z\s]{8,80}?)\s*,\s*identificad',
            r'informar\s+que\s+([A-Z\s]{8,80}?)\s+identificad',
            r'certifica\s+que\s+([A-Z\s]{8,80}?)\s*,\s*identificad',
        ]),
        _buscar_patron(texto_compacto, [
            r'informa\s+que\s+([A-Z\s]{8,80}?)\s*,\s*identificad',
            r'informar\s+que\s+([A-Z\s]{8,80}?)\s+identificad',
        ], flags=re.IGNORECASE),
    ]

    for candidato in candidatos:
        candidato = re.sub(r'\s+', ' ', candidato).strip(' ,.;:-')
        if _es_nombre_valido(candidato):
            return _titulo_nombre(candidato)
    return ''


def _extraer_tipo_cuenta_generico(texto_compacto):
    texto_busqueda = _normalizar_busqueda(texto_compacto)
    if 'deposito de bajo monto' in texto_busqueda or 'deposito de bajo monto' in texto_busqueda:
        return 'Deposito de bajo monto'
    if 'cuentas de ahorros' in texto_busqueda or 'cuenta de ahorros' in texto_busqueda or 'ahorros' in texto_busqueda:
        return 'Ahorros'
    if 'corriente' in texto_busqueda:
        return 'Corriente'
    return ''


def _extraer_numero_cuenta_generico(texto_crudo, texto_compacto):
    candidatos = [
        _buscar_patron(texto_crudo, [
            r'cuentas?\s+de\s+ahorros\s+no\.?\s*([0-9][0-9\-\s]{5,25})',
            r'cuenta\s+(?:de\s+)?(?:ahorros|corriente)?\s*(?:no\.?|numero|numero|nro\.?)\s*([0-9][0-9\-\s]{5,25})',
            r'numero\s+de\s+deposito\s*(?:nequi)?\s*([0-9][0-9\-\s]{5,25})',
            r'(\d{8,12})\s+ACTIVA',
        ]),
        _buscar_patron(texto_compacto, [
            r'cuentas?\s+de\s+ahorros\s+no\.?\s*([0-9][0-9\-\s]{5,25})',
            r'cuenta\s+(?:de\s+)?(?:ahorros|corriente)?\s*(?:no\.?|numero|numero|nro\.?)\s*([0-9][0-9\-\s]{5,25})',
            r'numero\s+de\s+deposito\s*(?:nequi)?\s*([0-9][0-9\-\s]{5,25})',
            r'(\d{8,12})\s+ACTIVA',
        ], flags=re.IGNORECASE),
    ]
    for candidato in candidatos:
        numero = _normalizar_numero(candidato)
        if numero:
            return numero
    return ''


def _parsear_banco_bogota(texto_crudo, texto_compacto):
    return {
        'banco': 'Banco de Bogota',
        'tipo_cuenta': _buscar_patron(texto_compacto, [
            r'cuentas?\s+de\s+(ahorros)',
            r'cuenta\s+(corriente)',
        ], flags=re.IGNORECASE).capitalize() if _buscar_patron(texto_compacto, [r'cuentas?\s+de\s+(ahorros)', r'cuenta\s+(corriente)'], flags=re.IGNORECASE) else '',
        'numero_cuenta': _normalizar_numero(_buscar_patron(texto_crudo, [
            r'cuentas?\s+de\s+ahorros\s+no\.?\s*([0-9][0-9\-\s]{5,25})',
        ])),
        'titular': _extraer_titular_generico(texto_crudo, texto_compacto),
    }


def _parsear_nequi(texto_crudo, texto_compacto):
    numero = _buscar_patron(texto_crudo, [
        r'(\d{8,12})\s+ACTIVA',
        r'numero\s+de\s+deposito\s*(?:nequi)?\s*([0-9]{8,12})',
    ])
    if not numero:
        # Fallback: tomar el primer numero de 10 digitos antes de ACTIVA o del bloque principal.
        candidatos = re.findall(r'\b\d{8,12}\b', texto_crudo)
        candidatos = [c for c in candidatos if c not in {'1507'}]
        if candidatos:
            numero = candidatos[0]

    return {
        'banco': 'Nequi',
        'tipo_cuenta': 'Deposito de bajo monto' if 'deposito de bajo monto' in _normalizar_busqueda(texto_compacto) else '',
        'numero_cuenta': _normalizar_numero(numero),
        'titular': _extraer_titular_generico(texto_crudo, texto_compacto),
    }


def _parsear_bancolombia(texto_crudo, texto_compacto):
    numero = _buscar_patron(texto_crudo, [
        r'cuenta\s+de\s+ahorros\s+([0-9]{8,20})\s+\d{4}[/-]\d{2}[/-]\d{2}',
        r'cuenta\s+de\s+ahorros\s+([0-9]{8,20})\s+activa',
        r'cuenta\s+de\s+ahorros\s+([0-9]{8,20})',
        r'no\.\s*producto\s+fecha\s+apertura\s+estado\s+cuenta\s+de\s+ahorros\s+([0-9]{8,20})',
        r'producto\s+no\.\s*producto\s+fecha\s+apertura\s+estado\s+cuenta\s+de\s+ahorros\s+([0-9]{8,20})',
    ])

    return {
        'banco': 'Bancolombia',
        'tipo_cuenta': 'Ahorros' if 'cuenta de ahorros' in _normalizar_busqueda(texto_compacto) else '',
        'numero_cuenta': _normalizar_numero(numero),
        'titular': _extraer_titular_generico(texto_crudo, texto_compacto),
    }


def _parsear_banco_caja_social(texto_crudo, texto_compacto):
    titular = _buscar_patron(texto_crudo, [
        r'Que el\(los\)cliente\(s\)\s*(.+?)\s+Identificado',
        r'destino\s*a\s*(.+?)\s*,\s*realizada',
        r'destino\s*a\s*(.+?)(?:,|\.)',
    ])
    numero = _buscar_patron(texto_crudo, [
        r'Número:\s*([0-9][0-9\s]{5,25})',
    ])
    tipo = ''
    texto_busqueda = _normalizar_busqueda(texto_compacto)
    if 'cuenta ahorros' in texto_busqueda or 'cuenta de ahorros' in texto_busqueda or 'cuentamiga' in texto_busqueda:
        tipo = 'Ahorros'

    titular = _limpiar_nombre_candidato(titular).strip(' ,.;:-')
    if not _es_nombre_valido(titular):
        titular = ''

    return {
        'banco': 'Banco Caja Social',
        'tipo_cuenta': tipo,
        'numero_cuenta': _normalizar_numero(numero),
        'titular': _titulo_nombre(titular) if titular else '',
    }


def _aplicar_parser_por_banco(banco, texto_crudo, texto_compacto):
    if banco == 'Bancolombia':
        return _parsear_bancolombia(texto_crudo, texto_compacto)
    if banco == 'Banco de Bogota':
        return _parsear_banco_bogota(texto_crudo, texto_compacto)
    if banco == 'Nequi':
        return _parsear_nequi(texto_crudo, texto_compacto)
    if banco == 'Banco Caja Social':
        return _parsear_banco_caja_social(texto_crudo, texto_compacto)
    return {}


def parsear_certificado_bancario(texto_crudo, texto_compacto=None):
    texto_crudo = _normalizar_texto_crudo(texto_crudo)
    texto_compacto = _compactar_texto(texto_compacto or texto_crudo)

    banco = _extraer_banco(texto_compacto)
    resultado = {
        'banco': banco,
        'tipo_cuenta': '',
        'numero_cuenta': '',
        'titular': '',
    }

    # Primer intento: reglas especificas por banco.
    resultado.update({k: v for k, v in _aplicar_parser_por_banco(banco, texto_crudo, texto_compacto).items() if v})

    # Segundo intento: reglas genericas para completar faltantes.
    if not resultado['tipo_cuenta']:
        resultado['tipo_cuenta'] = _extraer_tipo_cuenta_generico(texto_compacto)
    if not resultado['numero_cuenta']:
        resultado['numero_cuenta'] = _extraer_numero_cuenta_generico(texto_crudo, texto_compacto)
    if not resultado['titular']:
        resultado['titular'] = _extraer_titular_generico(texto_crudo, texto_compacto)
    if not resultado['banco']:
        resultado['banco'] = _extraer_banco(texto_compacto)

    campos_obligatorios = ['banco', 'tipo_cuenta', 'numero_cuenta', 'titular']
    faltantes = [campo for campo in campos_obligatorios if not resultado.get(campo)]
    estado = ESTADO_COMPLETO if not faltantes else ESTADO_ERROR

    metadata = {
        'estado': estado,
        'campos_encontrados': len(campos_obligatorios) - len(faltantes),
        'faltantes': faltantes,
        'banco': resultado['banco'],
        'tipo_cuenta': resultado['tipo_cuenta'],
        'numero_cuenta': resultado['numero_cuenta'],
        'titular': resultado['titular'],
        'texto_extraido': bool(texto_compacto),
        'longitud_texto': len(texto_compacto),
    }

    if estado == ESTADO_ERROR:
        metadata['mensaje'] = 'No fue posible extraer completamente la informacion del certificado bancario.'
        metadata['soporte'] = 'Revisar PDF y reglas de parsing del banco correspondiente.'
    else:
        metadata['mensaje'] = 'Procesamiento completado correctamente.'

    return metadata


def procesar_certificado_bancario(detalle_libranza, persistir=True):
    metadata = {
        'estado': ESTADO_PENDIENTE,
        'mensaje': 'No se proceso el certificado bancario.',
    }

    archivo = getattr(detalle_libranza, 'certificado_bancario', None)
    if not archivo:
        metadata.update({
            'estado': ESTADO_ERROR,
            'mensaje': 'La solicitud no tiene certificado bancario adjunto.',
        })
    else:
        try:
            with archivo.open('rb') as pdf_file:
                extraido = extraer_texto_pdf(pdf_file)

            origen_texto = 'pdf_text'
            ocr_debug = {
                'ocr_aplicado': False,
                'ocr_disponible': True,
                'ocr_error': '',
            }

            # Fase 2: si el PDF viene escaneado (sin texto), intentar OCR.
            if not extraido.get('texto_compacto'):
                with archivo.open('rb') as pdf_file:
                    extraido_ocr = extraer_texto_pdf_ocr(pdf_file)
                ocr_debug = {
                    'ocr_aplicado': extraido_ocr.get('ocr_aplicado', False),
                    'ocr_disponible': extraido_ocr.get('ocr_disponible', False),
                    'ocr_error': extraido_ocr.get('ocr_error', ''),
                }
                if extraido_ocr.get('texto_compacto'):
                    extraido = extraido_ocr
                    origen_texto = 'ocr'

            metadata = parsear_certificado_bancario(
                texto_crudo=extraido['texto_crudo'],
                texto_compacto=extraido['texto_compacto'],
            )
            nombre_solicitud = (
                getattr(detalle_libranza, 'nombre_completo', '')
                or getattr(detalle_libranza, 'nombre', '')
            )
            nombre_solicitud = _limpiar_nombre_candidato(nombre_solicitud)
            if nombre_solicitud and _es_nombre_valido(nombre_solicitud):
                if not metadata.get('titular') or _titular_luce_incompleto(metadata.get('titular')):
                    metadata['titular'] = _titulo_nombre(nombre_solicitud)
                    if 'titular' in metadata.get('faltantes', []):
                        metadata['faltantes'] = [f for f in metadata['faltantes'] if f != 'titular']
                        metadata['campos_encontrados'] = metadata.get('campos_encontrados', 0) + 1
                    if metadata.get('faltantes') == [] and metadata.get('estado') != ESTADO_COMPLETO:
                        metadata['estado'] = ESTADO_COMPLETO
                        metadata['mensaje'] = 'Procesamiento completado correctamente.'
                    metadata['titular_fuente'] = 'fallback_solicitud'

            metadata.update({
                'paginas': extraido['paginas'],
                'archivo': getattr(archivo, 'name', ''),
                'origen_texto': origen_texto,
                **ocr_debug,
            })

            if metadata['estado'] == ESTADO_ERROR and not metadata.get('texto_extraido'):
                metadata['mensaje'] = 'No fue posible extraer texto del certificado. Sube un PDF digital o habilita OCR en el servidor.'
                metadata['soporte'] = 'Si el PDF es escaneado, valida instalacion de Tesseract + Poppler para OCR fallback.'
        except Exception as exc:
            logger.exception(
                'Error procesando certificado bancario para credito %s',
                getattr(getattr(detalle_libranza, 'credito', None), 'numero_credito', detalle_libranza.pk),
            )
            metadata = {
                'estado': ESTADO_ERROR,
                'mensaje': f'No se pudo procesar el PDF: {exc}',
                'soporte': 'Validar estructura del PDF y la extraccion de texto.',
            }

    if metadata.get('texto_extraido') and metadata['estado'] != ESTADO_COMPLETO:
        logger.warning(
            'Extraccion incompleta de certificado bancario para credito %s. Faltantes: %s. Archivo: %s',
            getattr(getattr(detalle_libranza, 'credito', None), 'numero_credito', detalle_libranza.pk),
            metadata.get('faltantes', []),
            metadata.get('archivo', ''),
        )

    if persistir:
        detalle_libranza.certificado_bancario_metadata = metadata
        detalle_libranza.certificado_bancario_estado_extraccion = metadata['estado']
        detalle_libranza.certificado_bancario_ultima_extraccion = timezone.now()
        detalle_libranza.save(update_fields=[
            'certificado_bancario_metadata',
            'certificado_bancario_estado_extraccion',
            'certificado_bancario_ultima_extraccion',
        ])

    return metadata

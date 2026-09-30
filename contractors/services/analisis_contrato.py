from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from pypdf import PdfReader


@dataclass(frozen=True)
class ResultadoAnalisisContrato:
    nombre_contratista: str = ''
    nombres: str = ''
    apellidos: str = ''
    documento_contratista: str = ''
    empresa_contratante: str = ''
    nit_empresa: str = ''
    cargo_o_servicio: str = ''
    tipo_contrato: str = ''
    fecha_inicio_contrato: date | None = None
    fecha_fin_contrato: date | None = None
    valor_total_contrato: Decimal | None = None
    valor_pagado_estimado: Decimal | None = None
    valor_pendiente_estimado: Decimal | None = None
    valor_mensual_o_honorarios: Decimal | None = None
    forma_pago: str = 'NO_IDENTIFICADA'
    frecuencia_pago: str = ''
    evidencia_forma_pago: str = ''
    confianza_forma_pago: Decimal = Decimal('0.00')
    duracion_meses_contrato: int | None = None
    confianza_general: Decimal = Decimal('0.00')
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    fuente: str = 'fallback_pdf'
    disponible: bool = True
    error_tipo: str = ''
    diagnostico: dict = field(default_factory=dict)
    fuentes_campos: dict = field(default_factory=dict)

    def datos_sugeridos(self):
        return {
            'nombre_contratista': self.nombre_contratista,
            'nombres': self.nombres,
            'apellidos': self.apellidos,
            'empresa_contratante': self.empresa_contratante,
            'nit_empresa': self.nit_empresa,
            'cargo_o_servicio': self.cargo_o_servicio,
            'tipo_contrato': self.tipo_contrato,
            'fecha_inicio_contrato': self.fecha_inicio_contrato.isoformat() if self.fecha_inicio_contrato else '',
            'fecha_fin_contrato': self.fecha_fin_contrato.isoformat() if self.fecha_fin_contrato else '',
            'valor_total_contrato': _decimal_texto(self.valor_total_contrato),
            'valor_pagado_estimado': _decimal_texto(self.valor_pagado_estimado),
            'valor_pendiente_estimado': _decimal_texto(self.valor_pendiente_estimado),
            'valor_mensual_o_honorarios': _decimal_texto(self.valor_mensual_o_honorarios),
            'forma_pago': self.forma_pago,
            'frecuencia_pago': self.frecuencia_pago,
            'forma_pago_mensual': self.forma_pago == 'MENSUAL',
            'evidencia_forma_pago': self.evidencia_forma_pago,
            'confianza_forma_pago': str(self.confianza_forma_pago),
            'fuente_forma_pago': self.fuente,
            'duracion_meses_contrato': self.duracion_meses_contrato,
        }


def leer_texto_pdf(documento):
    texto = ''
    try:
        documento.open('rb')
        documento.seek(0)
        lector = PdfReader(documento)
        if lector.is_encrypted:
            return '', 'pdf_cifrado'
        texto = '\n'.join((pagina.extract_text() or '') for pagina in lector.pages[:25])[:120000]
        return texto, '' if texto.strip() else 'pdf_without_extractable_text'
    except Exception:
        return '', 'pdf_read_error'
    finally:
        try:
            documento.seek(0)
        except (AttributeError, OSError, ValueError):
            pass


def analizar_contrato_fallback(documento, *, texto_pdf=None, motivo='') -> ResultadoAnalisisContrato:
    texto, motivo = leer_texto_pdf(documento) if texto_pdf is None else (texto_pdf, motivo)
    if not texto.strip():
        return ResultadoAnalisisContrato(
            disponible=False,
            error_tipo='pdf_sin_texto_extraible',
            advertencias=(
                'El PDF parece no tener texto extraíble. Intenta cargar un contrato digital o revisaremos manualmente.',
            ),
            diagnostico={
                'engine': 'fallback_pdf',
                'pdf_text_chars': 0,
                'reason': motivo or 'pdf_without_extractable_text',
            },
        )

    documento_contratista = _buscar(texto, r'(?:c[eé]dula(?:\s+de\s+ciudadan[ií]a)?|documento|c\.?c\.?)\s*(?:n[oº]\.?|n[uú]mero)?\s*[:#-]?\s*([\d.\-]{6,20})').replace('.', '')
    nit_empresa = _buscar(texto, r'\bNIT\s*[:#-]?\s*([\d.\-]{7,20})')
    nombre_contratista = _buscar(texto, r'(?:contratista|prestador(?:a)?(?:\s+de\s+servicios)?)\s*[:\-]\s*([^\n]{3,100})')
    if not nombre_contratista:
        nombre_contratista = _buscar(texto, r'^[ \t]*([^\n,;]{3,120}),\s*identificad[oa]\s+con\s+c[eé]dula\b')
    empresa = _buscar(texto, r'(?:contratante|empresa contratante)\s*[:\-]\s*([^\n]{3,120})')
    if not empresa:
        empresa = _buscar(texto, r'^[ \t]*([^\n,;]{3,160}),\s*identificad[oa]\s+con\s+NIT\b')
    cargo = _buscar(texto, r'(?:objeto|cargo(?:\s*/\s*actividad)?|servicio|actividad)\s*(?:del contrato)?\s*[:\-]\s*([^\n]{3,160})')
    if not cargo:
        cargo = _buscar(texto, r'servicios\s+profesionales\s+como\s+([^,;.]{2,160})')
    fecha_inicio = _buscar_fecha(texto, ('fecha de inicio', 'inicio del contrato', 'fecha inicio', 'inicia el'))
    fecha_fin = _buscar_fecha(texto, ('fecha de terminación', 'fecha de terminacion', 'fecha de finalización', 'fin del contrato', 'fecha fin', 'fecha terminación', 'fecha terminacion'))
    valor_total = _buscar_valor(texto, ('valor total del contrato', 'valor del contrato', 'valor total contrato', 'valor total'))
    if valor_total is None:
        valor_total = _importe(_buscar(texto, r'valor\s+total\s+del\s+(?:presente\s+)?contrato\s+(?:ser[aá]|es)\s+de\s*:[^\d$]{0,180}\$\s*([\d.,]+)'))
    honorarios = _buscar_valor(texto, ('honorarios mensuales', 'valor mensual', 'mensualidad'))
    if honorarios is None:
        honorarios = _importe(_buscar(texto, r'pagos?\s+mensuales\s+de\s*:?\s*\$\s*([\d.,]+)'))
    pagado = _buscar_valor(texto, ('valor pagado', 'total pagado', 'pagos realizados'))
    if pagado is None and re.search(r'\bno\s+se\s+registra\s+ning[uú]n\s+pago\s+efectuado\b', texto, re.I):
        pagado = Decimal('0')
    pendiente = _buscar_valor(texto, ('saldo pendiente', 'valor pendiente', 'saldo por cobrar'))
    forma_pago, frecuencia_pago, evidencia_pago, confianza_pago = _detectar_forma_pago(
        texto
    )
    tipo_contrato = 'PRESTACION_SERVICIOS' if re.search(r'prestaci[oó]n\s+de\s+servicios', texto, re.I) else ''

    encontrados = sum(bool(valor) for valor in (
        documento_contratista, nit_empresa, nombre_contratista, empresa, cargo,
        fecha_inicio, fecha_fin, valor_total, honorarios,
    ))
    advertencias = []
    if not documento_contratista:
        advertencias.append('No fue posible validar el documento dentro del contrato.')
    if encontrados < 3:
        advertencias.append('El PDF contiene pocos campos identificables; confirma toda la información manualmente.')

    return ResultadoAnalisisContrato(
        nombre_contratista=nombre_contratista,
        nombres=_buscar(texto, r'^nombres\s*[:\-]\s*([^\n]{2,120})'),
        apellidos=_buscar(texto, r'^apellidos\s*[:\-]\s*([^\n]{2,120})'),
        documento_contratista=documento_contratista,
        empresa_contratante=empresa,
        nit_empresa=nit_empresa,
        cargo_o_servicio=cargo,
        tipo_contrato=tipo_contrato,
        duracion_meses_contrato=_buscar_duracion(texto),
        fecha_inicio_contrato=fecha_inicio,
        fecha_fin_contrato=fecha_fin,
        valor_total_contrato=valor_total,
        valor_pagado_estimado=pagado,
        valor_pendiente_estimado=pendiente,
        valor_mensual_o_honorarios=honorarios,
        forma_pago=forma_pago,
        frecuencia_pago=frecuencia_pago,
        evidencia_forma_pago=evidencia_pago,
        confianza_forma_pago=confianza_pago,
        confianza_general=(Decimal(encontrados) / Decimal('9')).quantize(Decimal('0.01')),
        advertencias=tuple(advertencias),
        diagnostico={
            'engine': 'fallback_pdf',
            'pdf_text_chars': len(texto),
            'reason': 'fallback_completed',
        },
    )


def _buscar(texto, patron):
    coincidencia = re.search(patron, texto, re.I | re.M)
    return re.sub(r'\s+', ' ', coincidencia.group(1)).strip(' .,:;') if coincidencia else ''


def _buscar_fecha(texto, etiquetas):
    meses = dict(zip(('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'), range(1, 13)))
    for etiqueta in etiquetas:
        valor = _buscar(texto, rf'{re.escape(etiqueta)}\s*[:\-]?\s*(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+de\s+(?:{"|".join(meses)})\s+de\s+\d{{4}})')
        if valor:
            try:
                if re.match(r'^\d{4}-', valor):
                    return date.fromisoformat(valor)
                if ' de ' in valor.lower():
                    dia, mes, anio = valor.lower().split(' de ')
                    return date(int(anio), meses[mes], int(dia))
                dia, mes, anio = re.split(r'[/-]', valor)
                return date(int(anio), int(mes), int(dia))
            except ValueError:
                continue
    return None


def _buscar_valor(texto, etiquetas):
    for etiqueta in etiquetas:
        valor = _buscar(texto, rf'{re.escape(etiqueta)}\s*[:\-]?\s*\$?\s*([\d.,]+)')
        if valor:
            importe = _importe(valor)
            if importe is not None:
                return importe
    return None


def _importe(valor):
    if not valor:
        return None
    from contractors.forms import MontoContratoField
    from django.core.exceptions import ValidationError
    try:
        return MontoContratoField(max_digits=14, decimal_places=2, min_value=Decimal('0')).clean(valor)
    except (InvalidOperation, ValidationError):
        return None


def _buscar_duracion(texto):
    valor = _buscar(texto, r'duraci[oó]n(?:\s+del contrato)?(?:\s+de)?\s*[:\-]?\s*(?:[a-záéíóúñ ]{2,40}\s*\()?([0-9]{1,4})\)?\s*meses\b')
    return int(valor) if valor else None


def _detectar_forma_pago(texto):
    # El PDF puede separar la periodicidad de su sustantivo con un salto de linea.
    periodicidad = re.search(
        r'(?i)\b(?:pagos?|honorarios|remuneraci[oó]n)\s+'
        r'(mensual(?:es)?|quincenal(?:es)?|semanal(?:es)?)\b', texto,
    )
    if periodicidad:
        forma = periodicidad.group(1).upper().removesuffix('ES')
        evidencia = re.sub(r'\s+', ' ', periodicidad.group(0)).strip()
        return forma, forma, evidencia[:500], Decimal('0.80')
    patrones = (
        ('MENSUAL', r'(?i)(?:pago|honorarios|remuneraci[oó]n)[^\n]{0,80}\bmensual(?:es)?\b'),
        ('QUINCENAL', r'(?i)(?:pago|honorarios|remuneraci[oó]n)[^\n]{0,80}\bquincenal(?:es)?\b'),
        ('SEMANAL', r'(?i)(?:pago|honorarios|remuneraci[oó]n)[^\n]{0,80}\bsemanal(?:es)?\b'),
        ('POR_ENTREGABLE', r'(?i)\bpago\s+por\s+(?:cada\s+)?entregable\b'),
        ('CONTRA_FACTURA', r'(?i)\b(?:contra|previa)\s+(?:presentaci[oó]n\s+de\s+)?factura\b'),
        ('VARIABLE', r'(?i)\b(?:pago|remuneraci[oó]n|honorarios)\s+variable(?:s)?\b'),
    )
    for forma, patron in patrones:
        coincidencia = re.search(patron, texto)
        if coincidencia:
            evidencia = re.sub(r'\s+', ' ', coincidencia.group(0)).strip()
            return forma, forma, evidencia[:500], Decimal('0.80')
    return 'NO_IDENTIFICADA', '', '', Decimal('0.00')


def _decimal_texto(valor):
    return str(valor) if valor is not None else ''

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from pypdf import PdfReader


@dataclass(frozen=True)
class ResultadoAnalisisContrato:
    nombre_contratista: str = ''
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
    duracion_meses_contrato: int | None = None
    confianza_general: Decimal = Decimal('0.00')
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    fuente: str = 'fallback_pdf'
    disponible: bool = True
    error_tipo: str = ''
    diagnostico: dict = field(default_factory=dict)

    def datos_sugeridos(self):
        return {
            'nombre_contratista': self.nombre_contratista,
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
            'duracion_meses_contrato': self.duracion_meses_contrato,
        }


def analizar_contrato_fallback(documento) -> ResultadoAnalisisContrato:
    texto = ''
    try:
        documento.open('rb')
        documento.seek(0)
        lector = PdfReader(documento)
        texto = '\n'.join((pagina.extract_text() or '') for pagina in lector.pages[:25])[:120000]
    except Exception as exc:
        return ResultadoAnalisisContrato(
            disponible=False,
            error_tipo='pdf_sin_texto_extraible',
            advertencias=(
                'El análisis asistido no está disponible para este PDF. Completa y confirma los datos manualmente.',
            ),
            diagnostico={
                'engine': 'fallback_pdf',
                'pdf_text_chars': 0,
                'reason': 'pdf_read_error',
                'fallback_error_type': type(exc).__name__,
            },
        )
    finally:
        try:
            documento.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

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
                'reason': 'pdf_without_extractable_text',
            },
        )

    documento_contratista = _buscar(texto, r'(?:c[eé]dula|documento|c\.?c\.?)\s*(?:n[oº]\.?|n[uú]mero)?\s*[:#-]?\s*([\d.\-]{6,20})')
    nit_empresa = _buscar(texto, r'\bNIT\s*[:#-]?\s*([\d.\-]{7,20})')
    nombre_contratista = _buscar(texto, r'(?:contratista|prestador(?:a)?(?:\s+de\s+servicios)?)\s*[:\-]\s*([^\n]{3,100})')
    empresa = _buscar(texto, r'(?:contratante|empresa contratante)\s*[:\-]\s*([^\n]{3,120})')
    cargo = _buscar(texto, r'(?:objeto|cargo|servicio|actividad)\s*(?:del contrato)?\s*[:\-]\s*([^\n]{3,240})')
    fecha_inicio = _buscar_fecha(texto, ('fecha de inicio', 'inicio del contrato'))
    fecha_fin = _buscar_fecha(texto, ('fecha de terminación', 'fecha de finalización', 'fin del contrato'))
    valor_total = _buscar_valor(texto, ('valor total del contrato', 'valor del contrato'))
    honorarios = _buscar_valor(texto, ('honorarios mensuales', 'valor mensual', 'mensualidad'))
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
        documento_contratista=documento_contratista,
        empresa_contratante=empresa,
        nit_empresa=nit_empresa,
        cargo_o_servicio=cargo,
        tipo_contrato=tipo_contrato,
        fecha_inicio_contrato=fecha_inicio,
        fecha_fin_contrato=fecha_fin,
        valor_total_contrato=valor_total,
        valor_mensual_o_honorarios=honorarios,
        confianza_general=(Decimal(encontrados) / Decimal('9')).quantize(Decimal('0.01')),
        advertencias=tuple(advertencias),
        diagnostico={
            'engine': 'fallback_pdf',
            'pdf_text_chars': len(texto),
            'reason': 'fallback_completed',
        },
    )


def _buscar(texto, patron):
    coincidencia = re.search(patron, texto, re.I)
    return re.sub(r'\s+', ' ', coincidencia.group(1)).strip(' .,:;') if coincidencia else ''


def _buscar_fecha(texto, etiquetas):
    for etiqueta in etiquetas:
        valor = _buscar(texto, rf'{re.escape(etiqueta)}\s*[:\-]?\s*(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})')
        if valor:
            try:
                if re.match(r'^\d{4}-', valor):
                    return date.fromisoformat(valor)
                dia, mes, anio = re.split(r'[/-]', valor)
                return date(int(anio), int(mes), int(dia))
            except ValueError:
                continue
    return None


def _buscar_valor(texto, etiquetas):
    for etiqueta in etiquetas:
        valor = _buscar(texto, rf'{re.escape(etiqueta)}\s*[:\-]?\s*\$?\s*([\d.,]+)')
        if valor:
            try:
                return Decimal(re.sub(r'[^\d]', '', valor))
            except InvalidOperation:
                continue
    return None


def _decimal_texto(valor):
    return str(valor) if valor is not None else ''

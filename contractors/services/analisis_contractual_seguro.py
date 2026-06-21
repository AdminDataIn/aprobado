from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import re
from difflib import SequenceMatcher
import unicodedata

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from gestion_creditos.models import Empresa


FUENTE_EXTRAIDA = 'extraido_ia'
FUENTE_INFERIDA_DURACION = 'inferida_duracion_meses'
FUENTE_CALCULADA_TOTAL_PAGADO = 'calculada_total_menos_pagado'
FUENTE_INFERIDA_MENSUALIDAD = 'inferida_mensualidad_vigencia'
FUENTE_NO_DETERMINADA = 'no_determinada'
FUENTE_CONTRATO_VENCIDO = 'contrato_vencido'

TIPO_EMPRESA_NIT_EXACTO = 'nit_exacto'
TIPO_EMPRESA_NOMBRE_EXACTO = 'nombre_exacto'
TIPO_EMPRESA_APROXIMADA = 'coincidencia_aproximada'
TIPO_EMPRESA_CONFLICTO = 'conflicto_nit_nombre'
TIPO_EMPRESA_NO_ENCONTRADA = 'no_encontrada'


@dataclass(frozen=True)
class SugerenciaEmpresaContrato:
    empresa_id: int | None = None
    nombre: str = ''
    nit: str = ''
    tipo_coincidencia: str = TIPO_EMPRESA_NO_ENCONTRADA
    requiere_confirmacion: bool = True
    conflicto: bool = False

    def como_dict(self):
        return {
            'empresa_id': self.empresa_id,
            'nombre': self.nombre,
            'nit': self.nit,
            'tipo_coincidencia': self.tipo_coincidencia,
            'requiere_confirmacion': self.requiere_confirmacion,
            'conflicto': self.conflicto,
        }


@dataclass(frozen=True)
class ResultadoAnalisisContractualSeguro:
    datos: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    bloqueos: tuple[str, ...] = field(default_factory=tuple)
    eventos: tuple[str, ...] = field(default_factory=tuple)
    sugerencia_empresa: SugerenciaEmpresaContrato = field(default_factory=SugerenciaEmpresaContrato)
    requiere_revision_manual: bool = True

    def como_dict(self):
        return {
            'datos': self.datos,
            'metadata': self.metadata,
            'advertencias': list(self.advertencias),
            'bloqueos': list(self.bloqueos),
            'eventos': list(self.eventos),
            'sugerencia_empresa': self.sugerencia_empresa.como_dict(),
            'requiere_revision_manual': self.requiere_revision_manual,
        }


def enriquecer_analisis_contrato_prestador(resultado_ia, *, fecha_base=None):
    fecha_base = fecha_base or timezone.localdate()
    datos = resultado_ia.datos_autocompletado()
    metadata = {
        'version': 'analisis_contractual_seguro_v1',
        'fecha_base': fecha_base.isoformat(),
        'requiere_confirmacion_usuario': True,
    }
    advertencias = list(resultado_ia.advertencias or ())
    bloqueos = []
    eventos = []

    fecha_inicio = resultado_ia.fecha_inicio_contrato
    fecha_fin = resultado_ia.fecha_fin_contrato
    fuente_fecha_fin = FUENTE_EXTRAIDA if fecha_fin else FUENTE_NO_DETERMINADA
    fecha_fin_requiere_confirmacion = bool(fecha_fin)

    if not fecha_fin and fecha_inicio and resultado_ia.duracion_meses_contrato:
        fecha_fin = fecha_inicio + relativedelta(months=resultado_ia.duracion_meses_contrato)
        datos['fecha_fin_contrato'] = fecha_fin.isoformat()
        fuente_fecha_fin = FUENTE_INFERIDA_DURACION
        fecha_fin_requiere_confirmacion = True
        advertencias.append('Inferimos la fecha final a partir de la duracion del contrato. Confirma este dato antes de continuar.')
        eventos.append('CONTRATO_FECHA_FINAL_INFERIDA')

    contrato_vencido = bool(fecha_fin and fecha_fin < fecha_base)
    if contrato_vencido:
        bloqueos.append('contrato_vencido_detectado')
        advertencias.append('El contrato detectado se encuentra vencido. La capacidad contractual queda en cero y requiere revision manual.')
        eventos.append('CONTRATO_VENCIDO_DETECTADO')

    valor_pendiente, fuente_valor_pendiente, pendiente_requiere_confirmacion, eventos_pendiente, advertencias_pendiente = (
        _resolver_valor_pendiente(resultado_ia, fecha_fin=fecha_fin, fecha_base=fecha_base, contrato_vencido=contrato_vencido)
    )
    eventos.extend(eventos_pendiente)
    advertencias.extend(advertencias_pendiente)
    if valor_pendiente is not None:
        datos['valor_pendiente_estimado'] = str(valor_pendiente)

    sugerencia_empresa = sugerir_empresa_por_contrato(
        nit_empresa=resultado_ia.nit_empresa,
        nombre_empresa=resultado_ia.empresa_contratante,
    )
    if sugerencia_empresa.tipo_coincidencia == TIPO_EMPRESA_NIT_EXACTO:
        eventos.append('EMPRESA_SUGERIDA_POR_NIT')
    if sugerencia_empresa.requiere_confirmacion:
        eventos.append('EMPRESA_REQUIERE_CONFIRMACION')
    if sugerencia_empresa.conflicto:
        bloqueos.append('empresa_contrato_conflicto_nit_nombre')

    metadata.update(
        {
            'fecha_fin_fuente': fuente_fecha_fin,
            'fecha_fin_requiere_confirmacion': fecha_fin_requiere_confirmacion,
            'contrato_vencido_detectado': contrato_vencido,
            'valor_pendiente_fuente': fuente_valor_pendiente,
            'valor_pendiente_requiere_confirmacion': pendiente_requiere_confirmacion,
            'valor_pendiente_no_determinado': fuente_valor_pendiente == FUENTE_NO_DETERMINADA,
            'empresa_tipo_coincidencia': sugerencia_empresa.tipo_coincidencia,
            'empresa_requiere_confirmacion': sugerencia_empresa.requiere_confirmacion,
            'empresa_conflicto': sugerencia_empresa.conflicto,
        }
    )

    requiere_revision_manual = bool(
        bloqueos
        or fuente_fecha_fin in {FUENTE_INFERIDA_DURACION, FUENTE_NO_DETERMINADA}
        or fuente_valor_pendiente != FUENTE_EXTRAIDA
        or sugerencia_empresa.requiere_confirmacion
    )

    return ResultadoAnalisisContractualSeguro(
        datos=datos,
        metadata=metadata,
        advertencias=tuple(_deduplicar(advertencias)),
        bloqueos=tuple(_deduplicar(bloqueos)),
        eventos=tuple(_deduplicar(eventos)),
        sugerencia_empresa=sugerencia_empresa,
        requiere_revision_manual=requiere_revision_manual,
    )


def sugerir_empresa_por_contrato(*, nit_empresa='', nombre_empresa=''):
    base = (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
    )
    nit_normalizado = normalizar_nit_empresa(nit_empresa)
    nombre_normalizado = normalizar_nombre_empresa(nombre_empresa)

    if nit_normalizado:
        empresa_nit = _buscar_por_nit(base, nit_normalizado)
        if empresa_nit:
            conflicto = bool(
                nombre_normalizado
                and not _nombres_empresa_compatibles(nombre_normalizado, empresa_nit)
            )
            return _sugerencia(empresa_nit, TIPO_EMPRESA_CONFLICTO if conflicto else TIPO_EMPRESA_NIT_EXACTO, conflicto=conflicto)

    if nombre_normalizado:
        for empresa in base.order_by('nombre'):
            nombres = _nombres_normalizados_empresa(empresa)
            if nombre_normalizado in nombres:
                return _sugerencia(empresa, TIPO_EMPRESA_NOMBRE_EXACTO)

        mejor_empresa = None
        mejor_score = Decimal('0.00')
        for empresa in base.order_by('nombre'):
            score = max(
                Decimal(str(SequenceMatcher(None, nombre_normalizado, nombre).ratio()))
                for nombre in _nombres_normalizados_empresa(empresa)
                if nombre
            )
            if score > mejor_score:
                mejor_empresa = empresa
                mejor_score = score
        if mejor_empresa and mejor_score >= Decimal('0.78'):
            return _sugerencia(mejor_empresa, TIPO_EMPRESA_APROXIMADA)

    return SugerenciaEmpresaContrato()


def normalizar_nit_empresa(valor):
    return ''.join(caracter for caracter in str(valor or '') if caracter.isdigit())


def normalizar_nombre_empresa(valor):
    texto = str(valor or '').strip().lower()
    texto = ''.join(
        caracter
        for caracter in unicodedata.normalize('NFKD', texto)
        if not unicodedata.combining(caracter)
    )
    texto = re.sub(r'\b(s\.?a\.?s\.?|s\.?a\.?|ltda\.?|limitada|inc|corp|corporacion)\b', ' ', texto)
    texto = re.sub(r'[^a-z0-9 ]+', ' ', texto)
    return re.sub(r'\s+', ' ', texto).strip()


def _resolver_valor_pendiente(resultado_ia, *, fecha_fin, fecha_base, contrato_vencido):
    eventos = []
    advertencias = []
    if contrato_vencido:
        return Decimal('0.00'), FUENTE_CONTRATO_VENCIDO, True, eventos, advertencias

    total = resultado_ia.valor_total_contrato
    pagado = resultado_ia.valor_pagado_estimado
    pendiente = resultado_ia.valor_pendiente_estimado
    mensual = resultado_ia.valor_mensual_o_honorarios

    if pendiente is not None:
        return max(Decimal('0.00'), pendiente), FUENTE_EXTRAIDA, True, eventos, advertencias

    if total is not None and pagado is not None:
        eventos.append('CONTRATO_VALOR_PENDIENTE_INFERIDO')
        advertencias.append('Calculamos el valor pendiente con el total del contrato menos el valor pagado detectado. Confirma este dato.')
        return max(Decimal('0.00'), total - pagado), FUENTE_CALCULADA_TOTAL_PAGADO, True, eventos, advertencias

    if mensual is not None and fecha_fin:
        meses_restantes = _calcular_meses_restantes(fecha_fin, fecha_base)
        if meses_restantes > 0:
            valor_estimado = mensual * Decimal(meses_restantes)
            if total is not None:
                valor_estimado = min(valor_estimado, total)
            eventos.append('CONTRATO_VALOR_PENDIENTE_INFERIDO')
            advertencias.append('Estimamos el valor pendiente usando honorarios mensuales y vigencia restante. Debes confirmarlo.')
            return max(Decimal('0.00'), valor_estimado), FUENTE_INFERIDA_MENSUALIDAD, True, eventos, advertencias

    eventos.append('CONTRATO_VALOR_PENDIENTE_NO_DETERMINADO')
    advertencias.append('No fue posible determinar el valor pendiente por cobrar con evidencia suficiente. Completa y confirma este dato manualmente.')
    return None, FUENTE_NO_DETERMINADA, True, eventos, advertencias


def _calcular_meses_restantes(fecha_fin, fecha_base):
    if fecha_fin < fecha_base:
        return 0
    diferencia = relativedelta(fecha_fin, fecha_base)
    meses = diferencia.years * 12 + diferencia.months
    if diferencia.days > 0 or meses == 0:
        meses += 1
    return max(meses, 0)


def _buscar_por_nit(queryset, nit_normalizado):
    for empresa in queryset.order_by('nombre'):
        nit_empresa = normalizar_nit_empresa(empresa.nit)
        if nit_empresa == nit_normalizado:
            return empresa
        if len(nit_empresa) > 1 and nit_empresa[:-1] == nit_normalizado:
            return empresa
        if len(nit_normalizado) > 1 and nit_normalizado[:-1] == nit_empresa:
            return empresa
    return None


def _nombres_empresa_compatibles(nombre_normalizado, empresa):
    return any(
        SequenceMatcher(None, nombre_normalizado, nombre).ratio() >= 0.72
        for nombre in _nombres_normalizados_empresa(empresa)
        if nombre
    )


def _nombres_normalizados_empresa(empresa):
    return {
        normalizar_nombre_empresa(getattr(empresa, 'nombre', '')),
        normalizar_nombre_empresa(getattr(empresa, 'razon_social', '')),
    }


def _sugerencia(empresa, tipo_coincidencia, *, conflicto=False):
    return SugerenciaEmpresaContrato(
        empresa_id=empresa.id,
        nombre=empresa.razon_social or empresa.nombre,
        nit=empresa.nit,
        tipo_coincidencia=tipo_coincidencia,
        requiere_confirmacion=True,
        conflicto=conflicto,
    )


def _deduplicar(valores):
    resultado = []
    for valor in valores:
        if valor and valor not in resultado:
            resultado.append(valor)
    return resultado

"""Calendario contractual, sin inferir pagos recibidos ni ingreso neto."""
import calendar
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

import holidays
from dateutil.relativedelta import relativedelta


PATRON_DIA_MES_SIGUIENTE = (
    r'\bd[ií]a (\d{1,2}|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)'
    r'(?: \((\d{1,2})\))? (?:de cada|del) mes siguiente\b'
)
_ANIVERSARIO = 'calendario: periodos mensuales por aniversario contractual'
_AJUSTE_ANIVERSARIO = 'ajuste de aniversario: ultimo dia del mes sin cambiar el dia ancla'
_PAGO_CIERRE = r'pago: (?:al cierre|([1-9][0-9]?) dias calendario despues del cierre) de cada periodo'
_CALENDARIO_CONFLICTIVO = 'calendario flexible con clausulas incompatibles'
_NUMEROS = dict(zip('uno dos tres cuatro cinco seis siete ocho nueve diez'.split(), range(1, 11)))
_DIAS_ESCRITOS = {
    **_NUMEROS, 'un': 1,
    **dict(zip('once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte '
               'veintiuno veintidos veintitres veinticuatro veinticinco veintiseis veintisiete veintiocho veintinueve'
               .split(), range(11, 30))),
    'veintiun': 21,
}
for _decena, _valor in zip('treinta cuarenta cincuenta sesenta setenta ochenta noventa'.split(), range(30, 100, 10)):
    _DIAS_ESCRITOS[_decena] = _valor
    _DIAS_ESCRITOS.update({f'{_decena} y {unidad}': _valor + n
                           for unidad, n in _NUMEROS.items() if n < 10})
_DIAS_PATRON = '|'.join(_DIAS_ESCRITOS)
_VARIANTE_ANIVERSARIO = re.compile(
    r'(?:los )?periodos mensuales se (?:contaran|computaran|calcularan) por aniversario'
    r'(?: mensual| contractual)? (?:desde|a partir de) la fecha de inicio(?: del contrato)?'
    r'|cada mensualidad corresponde(?:ra)? al periodo comprendido entre'
    r' (?:dos )?aniversarios contractuales(?: consecutivos)?'
)
_PREFIJO_PAGO = (
    r'(?:el pago se (?:realizara|efectuara|hara)|los honorarios se pagaran|'
    r'el contratante pagara|se pagara)\s*,?\s+'
)
_CADA_PERIODO = r'cada periodo(?: mensual)?(?: de servicio)?'
_VARIANTE_CIERRE = re.compile(
    rf'{_PREFIJO_PAGO}(?:a periodo vencido,?\s+)?(?:al cierre|el ultimo dia) de {_CADA_PERIODO}'
)
_VARIANTE_DIAS = re.compile(
    rf'{_PREFIJO_PAGO}(?P<numero>[1-9][0-9]?|{_DIAS_PATRON})'
    r'(?:\s*\(\s*(?P<cifra>[0-9]{1,2})\s*\))?'
    rf' dias? calendario (?:despues de (?:terminado|finalizado|cerrado) {_CADA_PERIODO}'
    rf'|(?:despues|a partir) del cierre de {_CADA_PERIODO})'
)
_CONTEXTO_FLEXIBLE = re.compile(r'\b(?:aniversarios?|dia ancla|cada periodo|periodo vencido)\b')
_CONDICION_O_NEGACION = re.compile(
    r'\b(?:no|nunca|salvo|excepto|si|siempre que|a menos que|sujeto a|previa aprobacion|'
    r'previa aceptacion|dentro de)\b'
)
_ANCLA_ALTERNATIVA = re.compile(
    r'\b(?:dia ancla|aniversario(?: mensual| contractual)?)\b.*\b(?:dia \d+|dias? habiles?)\b'
    r'|\bdia \d+\b.*\b(?:dia ancla|aniversario(?: mensual| contractual)?)\b'
)
_CONDICION_CONTINUACION = re.compile(
    r'^(?:salvo|excepto|siempre que|a menos que|sujeto a|previa aprobacion|previa aceptacion|'
    r'si se (?:aprueba|acepta|presenta))\b'
)
_PATRON_PERIODO = (
    r'(\d{4}-\d{2}-\d{2}) a (\d{4}-\d{2}-\d{2});\s*'
    r'vence (\d{4}-\d{2}-\d{2});\s*valor cop ([1-9][0-9]{0,11}(?:\.[0-9]{2})?)'
)
_PREFIJO_DETALLE = 'Calendario documentado SHA256:'
_MAX_PERIODOS = 600


@dataclass(frozen=True)
class FlujoContractual:
    inicio: date
    fin: date
    fecha_pago: date
    valor: Decimal


@dataclass(frozen=True)
class HorizonteContractual:
    corte: date
    flujos: tuple = ()
    motivo: str = ''
    valor_pagado_al_corte_documento: Decimal | None = None
    fecha_corte_documento: date | None = None
    valor_pagado_actual_declarado: Decimal | None = None
    valor_pagado_actual_verificado: Decimal | None = None
    pendiente_confirmacion: bool = False

    @property
    def disponible(self):
        return not self.motivo

    @property
    def periodos_totales(self):
        return len(self.flujos)

    @property
    def periodos_causados(self):
        return sum(f.fin <= self.corte for f in self.flujos)

    @property
    def periodos_exigibles(self):
        return sum(f.fecha_pago <= self.corte for f in self.flujos)

    @property
    def flujos_futuros(self):
        return tuple(f for f in self.flujos if f.fecha_pago > self.corte)

    @property
    def periodos_futuros(self):
        return len(self.flujos_futuros)

    @property
    def horizonte_crediticio_disponible(self):
        return self.periodos_futuros

    @property
    def fecha_ultimo_flujo_contractual(self):
        return self.flujos[-1].fecha_pago if self.flujos else None

    @property
    def valor_programado_a_fecha(self):
        return sum((f.valor for f in self.flujos if f.fin <= self.corte), Decimal('0'))

    @property
    def valor_exigible_a_fecha(self):
        return sum((f.valor for f in self.flujos if f.fecha_pago <= self.corte), Decimal('0'))

    @property
    def flujo_contractual_futuro(self):
        return sum((f.valor for f in self.flujos_futuros), Decimal('0'))


def _sin_tildes(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto or '').lower())
                   if not unicodedata.combining(c))


def _clausulas_flexibles(texto):
    """Accept whole statements; normalize known equivalents, not isolated keywords."""
    texto = re.sub(r'\s+', ' ', _sin_tildes(texto))
    reglas = []
    incompatible = False
    anterior_es_regla = False
    for frase in re.split(r'[.;!]', texto):
        frase = re.sub(r'\s*:\s*', ': ', frase.strip())
        if not frase:
            continue
        cantidad_previa = len(reglas)
        if frase == _CALENDARIO_CONFLICTIVO:
            incompatible = True
        elif frase in (_ANIVERSARIO, _AJUSTE_ANIVERSARIO) or re.fullmatch(_PAGO_CIERRE, frase):
            reglas.append(frase)
        elif _VARIANTE_ANIVERSARIO.fullmatch(frase):
            reglas.append(_ANIVERSARIO)
        elif _VARIANTE_CIERRE.fullmatch(frase):
            reglas.append('pago: al cierre de cada periodo')
        elif dias := _VARIANTE_DIAS.fullmatch(frase):
            numero, cifra = dias['numero'], dias['cifra']
            valor = int(numero) if numero.isdecimal() else _DIAS_ESCRITOS[numero]
            if cifra is not None and valor != int(cifra):
                return (_CALENDARIO_CONFLICTIVO,)
            reglas.append(f'pago: {valor} dias calendario despues del cierre de cada periodo')
        elif (_CONTEXTO_FLEXIBLE.search(frase) and _CONDICION_O_NEGACION.search(frase)
              or _ANCLA_ALTERNATIVA.search(frase)
              or anterior_es_regla and _CONDICION_CONTINUACION.search(frase)):
            incompatible = True
        anterior_es_regla = len(reglas) > cantidad_previa
    if incompatible and (_ANIVERSARIO in reglas or any(r.startswith('pago:') for r in reglas)
                         or _CALENDARIO_CONFLICTIVO in texto):
        return (_CALENDARIO_CONFLICTIVO,)
    return tuple(dict.fromkeys(reglas))


def periodos_documentados_en_texto(texto):
    """Return None for no schedule, and [] for a declared but unreadable schedule."""
    lineas = _sin_tildes(texto).splitlines()
    filas = []
    declarado = False
    for linea in lineas:
        linea = ' '.join(linea.split())
        if linea == 'calendario de pagos documentado:':
            declarado = True
        elif linea.startswith('periodo documentado:'):
            declarado = True
            fila = re.fullmatch(rf'periodo documentado:\s*{_PATRON_PERIODO}\.?', linea)
            if not fila or len(filas) >= _MAX_PERIODOS:
                return []
            filas.append(list(fila.groups()))
    return filas if declarado else None


def marcador_periodos_documentados(periodos):
    contenido = json.dumps(periodos, ensure_ascii=True, separators=(',', ':'))
    return f'{_PREFIJO_DETALLE}{hashlib.sha256(contenido.encode("ascii")).hexdigest()}'


def usa_calendario_flexible(evidencia):
    texto = str(evidencia or '')
    return texto.startswith(_PREFIJO_DETALLE) or any(
        regla in (_ANIVERSARIO, _CALENDARIO_CONFLICTIVO) or regla.startswith('pago:')
        for regla in _clausulas_flexibles(texto)
    )


def evidencia_calendario_en_texto(texto):
    texto = re.sub(r'\s+', ' ', str(texto or ''))
    patron = (r'primeros (?:\d{1,2}|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)'
              r'(?: \(\d{1,2}\))? d[ií]as h[aá]biles del mes siguiente'
              r'|pagos? (?:el )?[uú]ltimo d[ií]a (?:de cada|del) mes'
              r'|' + PATRON_DIA_MES_SIGUIENTE)
    legado = '; '.join(m.group(0) for m in re.finditer(patron, texto, re.I))[:400]
    clausulas = _clausulas_flexibles(texto)
    if not clausulas:
        return legado
    evidencia = '; '.join([*clausulas, *([legado] if legado else [])])
    # Leave room for the existing textual payment prefix; never truncate new clauses.
    return evidencia if len(evidencia) <= 400 else ''


def extraer_regla_pago(texto):
    texto = re.sub(r'\s+', ' ', _sin_tildes(texto))
    clausulas = _clausulas_flexibles(texto)
    if _CALENDARIO_CONFLICTIVO in clausulas:
        return None
    patron = r'primeros (\d{1,2}|' + '|'.join(_NUMEROS) + r')(?: \((\d{1,2})\))? dias habiles del mes siguiente'
    matches = list(re.finditer(patron, texto))
    fijos = list(re.finditer(PATRON_DIA_MES_SIGUIENTE, texto))
    fin_mes = re.search(r'(?:pago|pagos) (?:el )?ultimo dia (?:de cada|del) mes', texto)
    cierres = {int(m[1] or 0) for regla in clausulas if (m := re.fullmatch(_PAGO_CIERRE, regla))}
    if len(cierres) > 1 or sum(bool(regla) for regla in (matches, fijos, fin_mes, cierres)) > 1:
        return None
    if cierres:
        return ('CIERRE_PERIODO', cierres.pop())
    for coincidencias, tipo, limite in (
        (matches, 'HABILES_MES_SIGUIENTE', 15), (fijos, 'DIA_MES_SIGUIENTE', 31),
    ):
        dias = set()
        for match in coincidencias:
            valor = _NUMEROS.get(match[1]) or int(match[1])
            if (match[2] and int(match[2]) != valor) or not 1 <= valor <= limite:
                return None
            dias.add(valor)
        if len(dias) > 1:
            return None
        if dias:
            return (tipo, next(iter(dias)))
    if fin_mes:
        return ('FIN_MES', 0)
    return None


@lru_cache(maxsize=64)
def _festivos(anio):
    return frozenset(holidays.country_holidays('CO', years=[anio]))


def _fecha_pago(fin, regla):
    if regla[0] == 'CIERRE_PERIODO':
        return fin + timedelta(days=regla[1])
    if regla[0] == 'FIN_MES':
        return fin
    if regla[0] == 'DIA_MES_SIGUIENTE':
        siguiente = fin + timedelta(days=1)
        return date(siguiente.year, siguiente.month, regla[1])
    dia = fin + timedelta(days=1)
    restantes = regla[1]
    while True:
        if dia.weekday() < 5 and dia not in _festivos(dia.year):
            restantes -= 1
        if restantes == 0:
            return dia
        dia += timedelta(days=1)


def _flujos_documentados(periodos, *, inicio, fin, duracion_meses, valor_total):
    if not isinstance(periodos, list) or not 0 < len(periodos) <= _MAX_PERIODOS:
        return ()
    if duracion_meses is not None and duracion_meses != len(periodos):
        return ()
    flujos = []
    try:
        for fila in periodos:
            if (not isinstance(fila, list) or len(fila) != 4 or not all(isinstance(v, str) for v in fila)
                    or not re.fullmatch(_PATRON_PERIODO, '{} a {}; vence {}; valor cop {}'.format(*fila))):
                return ()
            desde, hasta, vence = map(date.fromisoformat, fila[:3])
            esperado = flujos[-1].fin + timedelta(days=1) if flujos else inicio
            if (desde != esperado or not desde <= hasta <= fin or vence < hasta
                    or (hasta - desde).days >= 31):
                return ()
            if flujos and (vence.year, vence.month) <= (flujos[-1].fecha_pago.year, flujos[-1].fecha_pago.month):
                return ()
            flujos.append(FlujoContractual(desde, hasta, vence, Decimal(fila[3])))
        if flujos[-1].fin == fin and sum(f.valor for f in flujos) == Decimal(str(valor_total)):
            return tuple(flujos)
    except (ArithmeticError, ValueError):
        pass
    return ()


def calcular_horizonte_contractual(*, inicio, fin, periodicidad, valor_periodico,
                                 evidencia_calendario, corte, duracion_meses=None,
                                 periodos_documentados=None, valor_total_contractual=None,
                                 valor_pagado_al_corte_documento=None, fecha_corte_documento=None,
                                 valor_pagado_actual_declarado=None, valor_pagado_actual_verificado=None):
    valores = dict(corte=corte, valor_pagado_al_corte_documento=valor_pagado_al_corte_documento,
        fecha_corte_documento=fecha_corte_documento,
        valor_pagado_actual_declarado=valor_pagado_actual_declarado,
        valor_pagado_actual_verificado=valor_pagado_actual_verificado)
    if not inicio or not fin or fin < inicio or periodicidad != 'MENSUAL':
        return HorizonteContractual(**valores, motivo='Faltan fechas o periodicidad mensual verificable.')
    evidencia = str(evidencia_calendario or '')
    if periodos_documentados is not None or evidencia.startswith(_PREFIJO_DETALLE):
        flujos = ()
        if periodos_documentados is not None and evidencia == marcador_periodos_documentados(periodos_documentados):
            flujos = _flujos_documentados(periodos_documentados, inicio=inicio, fin=fin,
                                        duracion_meses=duracion_meses, valor_total=valor_total_contractual)
        return HorizonteContractual(**valores, flujos=flujos, motivo='' if flujos else
                                    'El calendario documentado no concilia vigencia, vencimientos o importes.')

    clausulas = _clausulas_flexibles(evidencia)
    if _CALENDARIO_CONFLICTIVO in clausulas:
        return HorizonteContractual(**valores, motivo='Las clausulas del calendario contractual son incompatibles.')
    aniversario = _ANIVERSARIO in clausulas
    alineado = inicio.day == 1 and fin.day == calendar.monthrange(fin.year, fin.month)[1]
    if not alineado and not aniversario:
        return HorizonteContractual(**valores, pendiente_confirmacion=True,
                                    motivo='Falta documentar los periodos por aniversario o el calendario de periodos parciales.')
    try:
        limite = fin + timedelta(days=1)
        cantidad = (limite.year - inicio.year) * 12 + limite.month - inicio.month
        if not 1 <= cantidad <= _MAX_PERIODOS or inicio + relativedelta(months=cantidad) != limite:
            return HorizonteContractual(**valores, motivo='La vigencia no cierra periodos mensuales completos; documenta los parciales.')
        # Compute every boundary from the original anchor, never from the previous month.
        fronteras = [inicio + relativedelta(months=i) for i in range(cantidad + 1)]
    except (OverflowError, ValueError):
        return HorizonteContractual(**valores, motivo='La vigencia contractual contiene fechas fuera de rango.')
    if duracion_meses is not None and duracion_meses != cantidad:
        return HorizonteContractual(**valores, motivo='Duracion contractual inconsistente.')
    if aniversario and any(fecha.day != inicio.day for fecha in fronteras) and _AJUSTE_ANIVERSARIO not in clausulas:
        return HorizonteContractual(**valores, pendiente_confirmacion=True,
                                    motivo='Falta documentar el ajuste de aniversario para meses sin el dia pactado.')
    if valor_periodico is None or not Decimal(str(valor_periodico)).is_finite() or Decimal(str(valor_periodico)) <= 0:
        return HorizonteContractual(**valores, motivo='Falta valor periodico contractual valido.')
    regla = extraer_regla_pago(evidencia)
    if regla is None or (not alineado and regla[0] != 'CIERRE_PERIODO'):
        return HorizonteContractual(**valores, pendiente_confirmacion=True,
                                    motivo='Falta una regla precisa de fecha de pago contractual.')
    flujos = []
    for comienzo, siguiente in zip(fronteras, fronteras[1:]):
        cierre = siguiente - timedelta(days=1)
        try:
            fecha_pago = _fecha_pago(cierre, regla)
        except (OverflowError, ValueError):
            return HorizonteContractual(**valores, motivo='La regla contractual contiene una fecha de pago inexistente.')
        flujos.append(FlujoContractual(comienzo, cierre, fecha_pago, Decimal(str(valor_periodico))))
    return HorizonteContractual(**valores, flujos=tuple(flujos))

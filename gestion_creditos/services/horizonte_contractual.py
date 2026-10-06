"""Calendario contractual, sin inferir pagos recibidos ni ingreso neto."""
import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

import holidays
from dateutil.relativedelta import relativedelta


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


def evidencia_calendario_en_texto(texto):
    texto = re.sub(r'\s+', ' ', str(texto or ''))
    patron = (r'primeros (?:\d{1,2}|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)'
              r'(?: \(\d{1,2}\))? d[ií]as h[aá]biles del mes siguiente'
              r'|pagos? (?:el )?[uú]ltimo d[ií]a (?:de cada|del) mes')
    return '; '.join(m.group(0) for m in re.finditer(patron, texto, re.I))[:400]


def extraer_regla_pago(texto):
    texto = ''.join(c for c in unicodedata.normalize('NFD', str(texto or '').lower())
                    if not unicodedata.combining(c))
    texto = re.sub(r'\s+', ' ', texto)
    numeros = {'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
               'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10}
    patron = r'primeros (\d{1,2}|' + '|'.join(numeros) + r')(?: \((\d{1,2})\))? dias habiles del mes siguiente'
    matches = list(re.finditer(patron, texto))
    dias = set()
    for match in matches:
        valor = numeros.get(match[1]) or int(match[1])
        if match[2] and int(match[2]) != valor:
            return None
        dias.add(valor)
    fin_mes = re.search(r'(?:pago|pagos) (?:el )?ultimo dia (?:de cada|del) mes', texto)
    if matches and fin_mes:
        return None
    if len(dias) == 1 and 1 <= next(iter(dias)) <= 15:
        return ('HABILES_MES_SIGUIENTE', next(iter(dias)))
    if not matches and fin_mes:
        return ('FIN_MES', 0)
    return None


@lru_cache(maxsize=64)
def _festivos(anio):
    return frozenset(holidays.country_holidays('CO', years=[anio]))


def _fecha_pago(fin, regla):
    if regla[0] == 'FIN_MES':
        return fin
    dia = fin + timedelta(days=1)
    restantes = regla[1]
    while True:
        if dia.weekday() < 5 and dia not in _festivos(dia.year):
            restantes -= 1
        if restantes == 0:
            return dia
        dia += timedelta(days=1)


def calcular_horizonte_contractual(*, inicio, fin, periodicidad, valor_periodico,
                                 evidencia_calendario, corte, duracion_meses=None,
                                 valor_pagado_al_corte_documento=None, fecha_corte_documento=None,
                                 valor_pagado_actual_declarado=None, valor_pagado_actual_verificado=None):
    valores = dict(corte=corte, valor_pagado_al_corte_documento=valor_pagado_al_corte_documento,
        fecha_corte_documento=fecha_corte_documento,
        valor_pagado_actual_declarado=valor_pagado_actual_declarado,
        valor_pagado_actual_verificado=valor_pagado_actual_verificado)
    if not inicio or not fin or fin < inicio or periodicidad != 'MENSUAL':
        return HorizonteContractual(**valores, motivo='Faltan fechas o periodicidad mensual verificable.')
    if inicio.day != 1 or fin.day != calendar.monthrange(fin.year, fin.month)[1]:
        return HorizonteContractual(**valores, motivo='El contrato requiere calendario de periodos parciales confirmado.')
    cantidad = (fin.year-inicio.year)*12 + fin.month-inicio.month+1
    if cantidad > 600 or (duracion_meses is not None and duracion_meses != cantidad):
        return HorizonteContractual(**valores, motivo='Duracion contractual inconsistente.')
    if valor_periodico is None or not Decimal(str(valor_periodico)).is_finite() or Decimal(str(valor_periodico)) <= 0:
        return HorizonteContractual(**valores, motivo='Falta valor periodico contractual valido.')
    regla = extraer_regla_pago(evidencia_calendario)
    if regla is None:
        return HorizonteContractual(**valores, motivo='Falta una regla precisa de fecha de pago contractual.')
    flujos = []
    for indice in range(cantidad):
        comienzo = inicio + relativedelta(months=indice)
        cierre = comienzo + relativedelta(months=1) - timedelta(days=1)
        flujos.append(FlujoContractual(comienzo, cierre, _fecha_pago(cierre, regla), Decimal(str(valor_periodico))))
    return HorizonteContractual(**valores, flujos=tuple(flujos))

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db.models import QuerySet
from django.utils import timezone

from gestion_creditos.models import AsesorComercial, Credito, Empresa
from gestion_creditos.services.advisors import (
    filter_creditos_by_asesor,
    filter_creditos_by_empresa,
)


PERIODOS_VALIDOS = {
    'este_mes',
    'mes_anterior',
    'ultimos_3_meses',
    'este_anio',
    'todo',
}
ESTADOS_OBLIGACION_VALIDOS = {'TODAS', 'VENCIDA', 'VENCE_HOY', 'VENCE_PRONTO'}


@dataclass(frozen=True)
class AdminDashboardFilters:
    fecha_desde: date | None
    fecha_hasta: date | None
    empresa: Empresa | None
    estado: str
    linea: str
    asesor: AsesorComercial | None
    periodo: str
    obligacion_estado: str
    errores: tuple[str, ...]
    empresa_raw: str = ''
    asesor_raw: str = ''

    def aplicar_dimensiones_credito(self, queryset: QuerySet) -> QuerySet:
        if self.linea:
            queryset = queryset.filter(linea=self.linea)
        if self.estado:
            queryset = queryset.filter(estado=self.estado)
        queryset = filter_creditos_by_asesor(queryset, self.asesor)
        queryset = filter_creditos_by_empresa(queryset, self.empresa)
        return queryset.distinct()

    def aplicar_fecha_credito(self, queryset: QuerySet, campo: str) -> QuerySet:
        if campo not in {'fecha_solicitud', 'fecha_desembolso'}:
            raise ValueError('Campo temporal de credito no permitido.')
        return self._aplicar_rango(queryset, campo, usar_date_lookup=True)

    def aplicar_fecha_recaudo(self, queryset: QuerySet) -> QuerySet:
        return self._aplicar_rango(queryset, 'fecha_aplicacion', usar_date_lookup=True)

    def aplicar_fecha_vencimiento(self, queryset: QuerySet, campo='fecha_vencimiento') -> QuerySet:
        if campo not in {'fecha_vencimiento', 'cuota_fecha_vencimiento'}:
            raise ValueError('Campo de vencimiento no permitido.')
        return self._aplicar_rango(queryset, campo, usar_date_lookup=False)

    def _aplicar_rango(self, queryset, campo, *, usar_date_lookup):
        sufijo = '__date' if usar_date_lookup else ''
        if self.fecha_desde:
            queryset = queryset.filter(**{f'{campo}{sufijo}__gte': self.fecha_desde})
        if self.fecha_hasta:
            queryset = queryset.filter(**{f'{campo}{sufijo}__lte': self.fecha_hasta})
        return queryset


def parse_admin_dashboard_filters(request=None):
    params = request.GET if request is not None else {}
    errores = []
    hoy = timezone.localdate()

    periodo = (params.get('periodo') or 'todo').strip().lower()
    if periodo not in PERIODOS_VALIDOS:
        errores.append('El periodo seleccionado no es valido.')
        periodo = 'todo'

    fecha_desde_raw = (params.get('fecha_desde') or '').strip()
    fecha_hasta_raw = (params.get('fecha_hasta') or '').strip()
    errores_fecha_iniciales = len(errores)
    fecha_desde = _parse_fecha(fecha_desde_raw, 'fecha inicial', errores)
    fecha_hasta = _parse_fecha(fecha_hasta_raw, 'fecha final', errores)

    if len(errores) > errores_fecha_iniciales:
        fecha_desde = None
        fecha_hasta = None
    elif not fecha_desde_raw and not fecha_hasta_raw:
        fecha_desde, fecha_hasta = _rango_periodo(periodo, hoy)
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        errores.append('La fecha inicial no puede ser posterior a la fecha final.')
        fecha_desde = None
        fecha_hasta = None

    empresa_raw = (params.get('empresa') or '').strip()
    empresa = _resolver_empresa(empresa_raw)
    if empresa_raw and empresa is None:
        errores.append('La empresa seleccionada no existe.')

    asesor_raw = (params.get('asesor') or '').strip()
    asesor = None
    if asesor_raw:
        if asesor_raw.isdigit():
            asesor = AsesorComercial.objects.filter(pk=int(asesor_raw), activo=True).first()
        if asesor is None:
            errores.append('El ejecutivo seleccionado no es valido.')

    estado = (params.get('estado') or '').strip().upper()
    estados_validos = {value for value, _label in Credito.EstadoCredito.choices}
    if estado and estado not in estados_validos:
        errores.append('El estado de credito seleccionado no es valido.')
        estado = ''

    linea = (params.get('linea') or params.get('producto') or '').strip().upper()
    lineas_validas = {value for value, _label in Credito.LineaCredito.choices}
    if linea and linea not in lineas_validas:
        errores.append('La linea de credito seleccionada no es valida.')
        linea = ''

    obligacion_estado = (params.get('obligacion') or 'TODAS').strip().upper()
    if obligacion_estado not in ESTADOS_OBLIGACION_VALIDOS:
        errores.append('El estado operativo de obligacion no es valido.')
        obligacion_estado = 'TODAS'

    return AdminDashboardFilters(
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        empresa=empresa,
        estado=estado,
        linea=linea,
        asesor=asesor,
        periodo=periodo,
        obligacion_estado=obligacion_estado,
        errores=tuple(errores),
        empresa_raw=empresa_raw,
        asesor_raw=asesor_raw,
    )


def _resolver_empresa(value):
    if not value:
        return None
    queryset = Empresa.objects.all()
    if value.isdigit():
        return queryset.filter(pk=int(value)).first()
    return queryset.filter(nombre=value).order_by('pk').first()


def _parse_fecha(value, etiqueta, errores):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errores.append(f'La {etiqueta} no tiene un formato valido.')
        return None


def _rango_periodo(periodo, hoy):
    if periodo == 'este_mes':
        return hoy.replace(day=1), hoy
    if periodo == 'mes_anterior':
        inicio_mes = hoy.replace(day=1)
        fin_anterior = inicio_mes - relativedelta(days=1)
        return fin_anterior.replace(day=1), fin_anterior
    if periodo == 'ultimos_3_meses':
        return (hoy - relativedelta(months=2)).replace(day=1), hoy
    if periodo == 'este_anio':
        return hoy.replace(month=1, day=1), hoy
    return None, None

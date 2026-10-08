"""Oferta PROD v1 sobre score existente y bandas persistidas; no consulta centrales."""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError

from contractors.models import BandaScorePrestador
from contractors.score.dto import ResultadoScorePrestador
from contractors.score.politica import buscar_banda, validar_politica_score_completa


VERSION = 'prestadores-prod-v1'
PARAMETROS = {
    'monto_minimo': Decimal('1000000'), 'monto_maximo': Decimal('10000000'),
    'plazo_minimo_meses': 1, 'plazo_maximo_meses': 8,
    'tasa_mensual': Decimal('2.2000'), 'porcentaje_originacion': Decimal('10.0000'),
    'porcentaje_iva_originacion': Decimal('19.0000'),
    'porcentaje_fondo_garantia': Decimal('2.0000'),
    'porcentaje_seguro_vida_primera_cuota': Decimal('0.3711'),
}


def validar_configuracion_prod(configuracion):
    if configuracion.version != VERSION or any(
        Decimal(str(getattr(configuracion, campo))) != esperado
        for campo, esperado in PARAMETROS.items()
    ):
        raise ValidationError('La configuracion no coincide con prestadores-prod-v1 aprobada.')


@dataclass(frozen=True)
class IngresoNetoValido:
    monto: Decimal
    fuente: str
    fecha_corte: date


@dataclass(frozen=True)
class OfertaCalculada:
    banda: str
    monto: Decimal = Decimal('0.00')
    plazo: int = 0
    cuota: Decimal = Decimal('0.00')
    cuota_maxima: Decimal = Decimal('0.00')
    motivo: str = ''
    estado: str = 'SIN_OFERTA'
    banda_id: int | None = None
    version_score: str = ''
    version_politica: str = ''
    version_configuracion_financiera: str = ''
    capacidad_componente_score: Decimal | None = None
    fecha_ultima_cuota_credito: date | None = None
    fecha_ultimo_flujo_contractual: date | None = None
    score: Decimal | None = None
    monto_maximo_banda: Decimal | None = None
    plazo_maximo_banda: int | None = None
    horizonte_disponible: bool = False

    @property
    def capacidad_crediticia_oferta(self):
        return self.cuota_maxima

    def como_dict(self):
        return {
            clave: format(valor, 'f') if isinstance(valor, Decimal)
            else valor.isoformat() if isinstance(valor, date) else valor
            for clave, valor in {**asdict(self), 'capacidad_crediticia_oferta': self.capacidad_crediticia_oferta}.items()
        }


def preparar_oferta(*, score, politica, ingreso_neto, obligaciones_mensuales, monto_solicitado,
                    plazo_solicitado, horizonte, configuracion, corte):
    from contractors.services.capacidad_contractual import simular_credito_prestador_informativo
    from contractors.services.horizonte_simulacion import (
        fecha_ultima_cuota_proyectada, plazo_maximo_respaldado,
    )

    validar_configuracion_prod(configuracion)
    validar_politica_score_completa(politica)
    if (not isinstance(score, ResultadoScorePrestador)
            or score.version_score != politica.version_score
            or score.version_politica != politica.version_politica
            or not configuracion.pk
            or politica.configuracion_financiera_id != configuracion.pk):
        raise ValidationError('Score, politica y configuracion financiera no corresponden a la misma version.')
    contexto = dict(
        score=score.score_final,
        version_score=score.version_score, version_politica=score.version_politica,
        version_configuracion_financiera=configuracion.version,
        capacidad_componente_score=score.variables_calculadas.get('capacidad_disponible'),
        horizonte_disponible=horizonte.disponible,
        fecha_ultimo_flujo_contractual=horizonte.fecha_ultimo_flujo_contractual,
    )
    if score.score_final is None:
        return OfertaCalculada(banda='', estado='NO_EVALUABLE', motivo='No hay score existente evaluable.', **contexto)
    valor_score = Decimal(str(score.score_final))
    if not valor_score.is_finite() or not 0 <= valor_score <= 1000:
        raise ValidationError('Score de entrada invalido.')
    banda = buscar_banda(politica, valor_score)
    if banda is None or banda.nombre != score.banda:
        raise ValidationError('La banda persistida no corresponde al resultado del score.')
    contexto.update(banda=banda.nombre, banda_id=banda.pk,
                    monto_maximo_banda=banda.monto_maximo, plazo_maximo_banda=banda.plazo_maximo)
    if ingreso_neto is None:
        return OfertaCalculada(estado='NO_EVALUABLE', motivo='Falta ingreso neto valido para riesgo; no se infiere del contrato.', **contexto)
    if (not isinstance(ingreso_neto, IngresoNetoValido) or not ingreso_neto.fuente.strip()
            or not ingreso_neto.fecha_corte or ingreso_neto.fecha_corte > corte):
        raise ValidationError('Se requiere ingreso neto valido con fuente y fecha de corte.')
    ingreso = Decimal(str(ingreso_neto.monto))
    if obligaciones_mensuales is None:
        return OfertaCalculada(estado='NO_EVALUABLE', motivo='Carga mensual HDC incompleta.', **contexto)
    obligaciones = Decimal(str(obligaciones_mensuales))
    monto = Decimal(str(monto_solicitado))
    if any(not v.is_finite() or v < 0 for v in (ingreso, obligaciones, monto)):
        raise ValidationError('Importes de capacidad invalidos.')
    if not isinstance(plazo_solicitado, int) or isinstance(plazo_solicitado, bool) or plazo_solicitado < 1:
        raise ValidationError('Plazo solicitado invalido.')
    if not horizonte.disponible or horizonte.corte != corte:
        raise ValidationError('Horizonte contractual no valido para el corte.')
    if (score.bloqueos or score.requiere_revision_manual
            or banda.nombre == BandaScorePrestador.Nombre.REVISION
            or banda.resultado != BandaScorePrestador.Resultado.PREAPROBADO_READ_ONLY):
        return OfertaCalculada(motivo='El score o la banda no habilita oferta automatica.', **contexto)
    plazo = plazo_maximo_respaldado(horizonte, min(
        plazo_solicitado, configuracion.plazo_maximo_meses,
        politica.plazo_maximo_politica, banda.plazo_maximo,
    ))
    limite = max(Decimal('0'), ingreso-obligaciones) * Decimal('0.30')
    techo = min(monto, configuracion.monto_maximo, politica.monto_maximo_politica, banda.monto_maximo)
    if plazo < configuracion.plazo_minimo_meses or techo < configuracion.monto_minimo:
        return OfertaCalculada(cuota_maxima=limite, motivo='Sin plazo respaldado o monto minimo ofertable.', **contexto)

    def cuota(centavos):
        return simular_credito_prestador_informativo(
            monto=Decimal(centavos)/100, plazo_meses=plazo, configuracion=configuracion,
        ).cuota_mensual

    inferior = int(configuracion.monto_minimo*100)
    superior = int(techo*100)
    if cuota(inferior) > limite:
        return OfertaCalculada(cuota_maxima=limite, motivo='Capacidad inferior al minimo; no se eleva.', **contexto)
    # Busqueda monotona en centavos usando la cuota real, incluidos todos los cargos.
    while inferior < superior:
        medio = (inferior+superior+1)//2
        if cuota(medio) <= limite:
            inferior = medio
        else:
            superior = medio-1
    return OfertaCalculada(
        monto=Decimal(inferior)/100, plazo=plazo, cuota=cuota(inferior), cuota_maxima=limite,
        estado='OFERTA_CALCULADA', fecha_ultima_cuota_credito=fecha_ultima_cuota_proyectada(corte, plazo),
        **contexto,
    )

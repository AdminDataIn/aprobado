from dataclasses import dataclass, field
from decimal import Decimal


def _decimal_texto(valor):
    return format(valor, 'f') if isinstance(valor, Decimal) else valor


@dataclass(frozen=True)
class ComponenteScorePrestador:
    nombre: str
    disponible: bool
    score: Decimal | None
    peso_configurado: Decimal
    peso_aplicado: Decimal = Decimal('0')
    razones: tuple[str, ...] = field(default_factory=tuple)
    alertas: tuple[str, ...] = field(default_factory=tuple)
    fuente: str = ''

    def como_dict(self):
        return {
            'nombre': self.nombre,
            'disponible': self.disponible,
            'score': _decimal_texto(self.score),
            'peso_configurado': _decimal_texto(self.peso_configurado),
            'peso_aplicado': _decimal_texto(self.peso_aplicado),
            'razones': list(self.razones),
            'alertas': list(self.alertas),
            'fuente': self.fuente,
        }


@dataclass(frozen=True)
class PenalizacionScorePrestador:
    nombre: str
    puntos: Decimal
    razon: str

    def como_dict(self):
        return {
            'nombre': self.nombre,
            'puntos': _decimal_texto(self.puntos),
            'razon': self.razon,
        }


@dataclass(frozen=True)
class ResultadoScorePrestador:
    score_base: Decimal | None
    penalizaciones: tuple[PenalizacionScorePrestador, ...]
    score_final: Decimal | None
    banda: str | None
    componentes: tuple[ComponenteScorePrestador, ...]
    variables_calculadas: dict
    razones: tuple[str, ...]
    alertas: tuple[str, ...]
    bloqueos: tuple[str, ...]
    version_score: str
    version_politica: str
    monto_maximo_sugerido: Decimal | None = None
    plazo_maximo_sugerido: int | None = None
    requiere_revision_manual: bool = True
    fuente: str = 'score_prestadores_read_only_v2'

    def como_dict(self):
        return {
            'score_base': _decimal_texto(self.score_base),
            'penalizaciones': [item.como_dict() for item in self.penalizaciones],
            'score_final': _decimal_texto(self.score_final),
            'banda': self.banda,
            'componentes': [item.como_dict() for item in self.componentes],
            'variables_calculadas': {
                clave: _decimal_texto(valor)
                for clave, valor in self.variables_calculadas.items()
            },
            'razones': list(self.razones),
            'alertas': list(self.alertas),
            'bloqueos': list(self.bloqueos),
            'version_score': self.version_score,
            'version_politica': self.version_politica,
            'monto_maximo_sugerido': _decimal_texto(self.monto_maximo_sugerido),
            'plazo_maximo_sugerido': self.plazo_maximo_sugerido,
            'requiere_revision_manual': self.requiere_revision_manual,
            'fuente': self.fuente,
        }

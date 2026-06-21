from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class EntradaScoreInternoPrestador:
    solicitud_id: int | None = None
    componentes: dict[str, Any] = field(default_factory=dict)
    datacredito_status: str = 'PENDIENTE'

    @property
    def application_id(self):
        return self.solicitud_id


@dataclass(frozen=True)
class ComponenteScorePrestador:
    nombre: str
    peso: Decimal
    valor: Decimal | None
    puntaje_ponderado: Decimal = Decimal('0.00')
    estado: str = 'EVALUADO'
    razon: str = ''

    def como_dict(self):
        return {
            'nombre': self.nombre,
            'peso': str(self.peso),
            'valor': str(self.valor) if self.valor is not None else None,
            'puntaje_ponderado': str(self.puntaje_ponderado),
            'estado': self.estado,
            'razon': self.razon,
        }


@dataclass(frozen=True)
class PenalizacionScorePrestador:
    nombre: str
    valor: Decimal
    penalizacion: Decimal
    razon: str

    def como_dict(self):
        return {
            'nombre': self.nombre,
            'valor': str(self.valor),
            'penalizacion': str(self.penalizacion),
            'razon': self.razon,
        }


@dataclass(frozen=True)
class BandaScorePrestador:
    nombre: str
    minimo: Decimal
    maximo: Decimal
    monto_maximo: Decimal
    plazo_maximo_meses: int
    decision: str

    def como_dict(self):
        return {
            'nombre': self.nombre,
            'minimo': str(self.minimo),
            'maximo': str(self.maximo),
            'monto_maximo': str(self.monto_maximo),
            'plazo_maximo_meses': self.plazo_maximo_meses,
            'decision': self.decision,
        }


@dataclass(frozen=True)
class ResultadoScoreInternoPrestador:
    version_configuracion: str
    score_final: Decimal
    banda: BandaScorePrestador
    decision_preliminar: str
    monto_maximo_sugerido: Decimal
    plazo_maximo_sugerido: int
    componentes: tuple[ComponenteScorePrestador, ...] = field(default_factory=tuple)
    componentes_pendientes: tuple[str, ...] = field(default_factory=tuple)
    penalizaciones: tuple[PenalizacionScorePrestador, ...] = field(default_factory=tuple)
    razones: tuple[str, ...] = field(default_factory=tuple)
    requiere_revision_manual: bool = False
    datacredito_status: str = 'PENDIENTE'
    fuente: str = 'score_interno_read_only'
    pesos_usados: dict[str, str] = field(default_factory=dict)
    topes_aplicados: dict[str, Any] = field(default_factory=dict)
    regla_cuota_ingreso: dict[str, Any] = field(default_factory=dict)
    tasa_mensual: Decimal | None = None
    tasa_efectiva_anual: Decimal | None = None
    capacidad_financiera: dict[str, Any] = field(default_factory=dict)
    advertencias_tecnicas: tuple[str, ...] = field(default_factory=tuple)

    def como_dict(self):
        return {
            'version_configuracion': self.version_configuracion,
            'score_final': str(self.score_final),
            'banda': self.banda.como_dict(),
            'decision_preliminar': self.decision_preliminar,
            'monto_maximo_sugerido': str(self.monto_maximo_sugerido),
            'plazo_maximo_sugerido': self.plazo_maximo_sugerido,
            'componentes': [componente.como_dict() for componente in self.componentes],
            'componentes_pendientes': list(self.componentes_pendientes),
            'penalizaciones': [penalizacion.como_dict() for penalizacion in self.penalizaciones],
            'razones': list(self.razones),
            'requiere_revision_manual': self.requiere_revision_manual,
            'datacredito_status': self.datacredito_status,
            'fuente': self.fuente,
            'pesos_usados': self.pesos_usados,
            'topes_aplicados': self.topes_aplicados,
            'regla_cuota_ingreso': self.regla_cuota_ingreso,
            'tasa_mensual': str(self.tasa_mensual) if self.tasa_mensual is not None else None,
            'tasa_efectiva_anual': str(self.tasa_efectiva_anual) if self.tasa_efectiva_anual is not None else None,
            'capacidad_financiera': self.capacidad_financiera,
            'advertencias_tecnicas': list(self.advertencias_tecnicas),
        }

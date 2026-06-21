from dataclasses import dataclass, field


NIVEL_RIESGO_BAJO = 'BAJO'
NIVEL_RIESGO_MEDIO = 'MEDIO'
NIVEL_RIESGO_ALTO = 'ALTO'
NIVEL_RIESGO_NO_DISPONIBLE = 'NO_DISPONIBLE'

FUENTE_MOCK = 'mock'
FUENTE_PROVEEDOR_REAL = 'proveedor_real'
FUENTE_DATACREDITO_REAL = 'datacredito_real'
FUENTE_NO_CONFIGURADO = 'no_configurado'

ESTADO_DATACREDITO_DISPONIBLE = 'DISPONIBLE'
ESTADO_DATACREDITO_PENDIENTE = 'PENDIENTE'
ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY = 'BLOQUEADO_READ_ONLY'


@dataclass(frozen=True)
class EntradaConsultaDatacreditoPrestador:
    tipo_documento: str
    numero_documento: str
    solicitud_id: int | None = None

    @property
    def application_id(self):
        return self.solicitud_id


@dataclass(frozen=True)
class AlertaDatacreditoPrestador:
    codigo: str
    nivel: str
    mensaje: str

    def como_dict(self):
        return {
            'codigo': self.codigo,
            'nivel': self.nivel,
            'mensaje': self.mensaje,
        }


@dataclass(frozen=True)
class ResumenMoraDatacredito:
    mora_severa: bool = False
    mora_actual: bool = False
    obligaciones_abiertas: int | None = None
    obligaciones_en_mora: int | None = None

    def como_dict(self):
        return {
            'mora_severa': self.mora_severa,
            'mora_actual': self.mora_actual,
            'obligaciones_abiertas': self.obligaciones_abiertas,
            'obligaciones_en_mora': self.obligaciones_en_mora,
        }


@dataclass(frozen=True)
class ScoreExternoDatacredito:
    score_externo: int | None = None
    score_normalizado_0_1000: int | None = None

    def como_dict(self):
        return {
            'score_externo': self.score_externo,
            'score_normalizado_0_1000': self.score_normalizado_0_1000,
        }


@dataclass(frozen=True)
class ResultadoDatacreditoPrestador:
    disponible: bool
    fuente: str
    score_externo: int | None = None
    score_normalizado_0_1000: int | None = None
    mora_severa: bool = False
    mora_actual: bool = False
    obligaciones_abiertas: int | None = None
    obligaciones_en_mora: int | None = None
    nivel_riesgo: str = NIVEL_RIESGO_NO_DISPONIBLE
    saldo_mora: int | None = None
    valor_cuota_total: int | None = None
    porcentaje_cuota_vs_ingreso: str | None = None
    ingreso_estimado: int | None = None
    viabilidad: bool | None = None
    rating_recaudos: str | None = None
    monto_sugerido_datacredito: int | None = None
    alertas_resumen: tuple[str, ...] = field(default_factory=tuple)
    alertas: tuple[AlertaDatacreditoPrestador, ...] = field(default_factory=tuple)
    requiere_revision_manual: bool = True
    error_tipo: str | None = None
    metadata_segura: dict = field(default_factory=dict)

    @property
    def status(self):
        if self.mora_severa:
            return ESTADO_DATACREDITO_BLOQUEADO_READ_ONLY
        if self.disponible:
            return ESTADO_DATACREDITO_DISPONIBLE
        return ESTADO_DATACREDITO_PENDIENTE

    def como_dict(self):
        return {
            'disponible': self.disponible,
            'fuente': self.fuente,
            'score_externo': self.score_externo,
            'score_normalizado_0_1000': self.score_normalizado_0_1000,
            'mora_severa': self.mora_severa,
            'mora_actual': self.mora_actual,
            'obligaciones_abiertas': self.obligaciones_abiertas,
            'obligaciones_en_mora': self.obligaciones_en_mora,
            'nivel_riesgo': self.nivel_riesgo,
            'saldo_mora': self.saldo_mora,
            'valor_cuota_total': self.valor_cuota_total,
            'porcentaje_cuota_vs_ingreso': self.porcentaje_cuota_vs_ingreso,
            'ingreso_estimado': self.ingreso_estimado,
            'viabilidad': self.viabilidad,
            'rating_recaudos': self.rating_recaudos,
            'monto_sugerido_datacredito': self.monto_sugerido_datacredito,
            'alertas_resumen': list(self.alertas_resumen),
            'alertas': [alerta.como_dict() for alerta in self.alertas],
            'requiere_revision_manual': self.requiere_revision_manual,
            'error_tipo': self.error_tipo,
            'metadata_segura': self.metadata_segura,
            'status': self.status,
        }

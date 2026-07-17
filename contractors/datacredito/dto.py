from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ResultadoNormalizadoDatacreditoPrestador:
    score_externo: int | None = None
    rango_score: str | None = None
    total_obligaciones: int | None = None
    saldo_total: str | None = None
    cuota_mensual_total: str | None = None
    obligaciones_vigentes: int | None = None
    obligaciones_en_mora: int | None = None
    mora_maxima_dias: int | None = None
    consultas_recientes: int | None = None
    cupos_rotativos: str | None = None
    porcentaje_utilizacion: str | None = None
    antiguedad_historial_meses: int | None = None
    alertas: tuple[str, ...] = field(default_factory=tuple)
    servicio_fuente: str = ''
    fecha_consulta: str | None = None

    def como_dict(self):
        resultado = asdict(self)
        resultado['alertas'] = list(self.alertas)
        return resultado

    @classmethod
    def desde_dict(cls, datos):
        datos = datos if isinstance(datos, dict) else {}
        permitidos = {
            campo: datos.get(campo)
            for campo in cls.__dataclass_fields__
            if campo != 'alertas'
        }
        permitidos['alertas'] = tuple(
            str(alerta)[:120] for alerta in (datos.get('alertas') or [])
        )
        return cls(**permitidos)


@dataclass(frozen=True)
class ResultadoConsultaDatacreditoPrestador:
    estado: str
    reutilizado: bool = False
    snapshot_id: str | None = None
    servicio: str = ''
    consultado_en: str | None = None
    vigente_hasta: str | None = None
    resultado_normalizado: ResultadoNormalizadoDatacreditoPrestador | None = None
    error_codigo: str | None = None
    requiere_revision_manual: bool = True
    diagnostico_seguro: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoProveedorDatacreditoPrestador:
    estado_snapshot: str
    resultado_normalizado: ResultadoNormalizadoDatacreditoPrestador
    codigo_http: int | None = None
    codigo_funcional: str = ''

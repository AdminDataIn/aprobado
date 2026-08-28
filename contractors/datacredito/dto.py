from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ResultadoNormalizadoDatacreditoPrestador:
    score_externo: int | None = None
    rango_score: str | None = None
    total_obligaciones: int | None = None
    saldo_total: str | None = None
    cuota_mensual_total: str | None = None
    obligaciones_vigentes: int | None = None
    obligaciones_cerradas: int | None = None
    obligaciones_en_mora: int | None = None
    mora_maxima_dias: int | None = None
    saldo_mora: str | None = None
    mora_actual: bool | None = None
    mora_severa: bool | None = None
    consultas_recientes: int | None = None
    cupos_rotativos: str | None = None
    porcentaje_utilizacion: str | None = None
    antiguedad_historial_meses: int | None = None
    productos_activos: int | None = None
    tarjetas_revolventes: int | None = None
    creditos_activos: int | None = None
    comportamiento_pago: str | None = None
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


@dataclass(frozen=True)
class ResultadoCentralesPrestador:
    decisor: ResultadoConsultaDatacreditoPrestador | None = None
    historial: ResultadoConsultaDatacreditoPrestador | None = None
    estado_global: str = 'NO_EVALUABLE'
    completa: bool = False
    requiere_revision_manual: bool = True
    errores: tuple[str, ...] = field(default_factory=tuple)
    alertas: tuple[str, ...] = field(default_factory=tuple)
    snapshot_ids: dict = field(default_factory=dict)
    fuente: str = 'centrales_prestadores_dual_v1'

    def como_dict_seguro(self):
        return {
            'estado_global': self.estado_global,
            'completa': self.completa,
            'requiere_revision_manual': self.requiere_revision_manual,
            'errores': list(self.errores),
            'alertas': list(self.alertas),
            'snapshot_ids': dict(self.snapshot_ids),
            'fuente': self.fuente,
            'decisor': _resumen_consulta_seguro(self.decisor),
            'historial': _resumen_consulta_seguro(self.historial),
        }


def _resumen_consulta_seguro(resultado):
    if resultado is None:
        return None
    normalizado = resultado.resultado_normalizado
    return {
        'estado': resultado.estado,
        'reutilizado': resultado.reutilizado,
        'snapshot_id': resultado.snapshot_id,
        'servicio': resultado.servicio,
        'consultado_en': resultado.consultado_en,
        'error_codigo': resultado.error_codigo,
        'score_externo': getattr(normalizado, 'score_externo', None),
        'obligaciones_vigentes': getattr(normalizado, 'obligaciones_vigentes', None),
        'obligaciones_en_mora': getattr(normalizado, 'obligaciones_en_mora', None),
        'saldo_total': getattr(normalizado, 'saldo_total', None),
        'saldo_mora': getattr(normalizado, 'saldo_mora', None),
        'cuota_mensual_total': getattr(normalizado, 'cuota_mensual_total', None),
        'mora_actual': getattr(normalizado, 'mora_actual', None),
        'mora_severa': getattr(normalizado, 'mora_severa', None),
        'consultas_recientes': getattr(normalizado, 'consultas_recientes', None),
    }

from dataclasses import dataclass, field
from decimal import Decimal

from contractors.models import (
    BandaScorePrestador,
    ConfiguracionScorePrestador,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    PredecisionPrestadorAudit,
)
from contractors.score.motor import evaluar_score_prestador
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar
from contractors.services.validacion_contractual import validar_contrato_prestador
from contractors.services.centrales_riesgo import (
    ESTADO_COMPLETA,
    ESTADO_NO_EVALUABLE,
    ESTADO_PARCIAL,
    ESTADO_REVISION_MANUAL,
)
from integrations.models import ConsultaDatacreditoSnapshot


RESULTADO_PREAPROBABLE = 'PREAPROBABLE'
RESULTADO_REQUIERE_REVISION = 'REQUIERE_REVISION'
RESULTADO_NO_EVALUABLE = 'NO_EVALUABLE'


@dataclass(frozen=True)
class ResultadoPredecisionPrestador:
    solicitud_id: int
    resultado: str
    puntaje_informativo: int | None
    razones: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    datos_faltantes: list[str] = field(default_factory=list)
    resumen: str = ''
    capacidad_contractual: object | None = None
    fuente: str = 'predecision_prestadores_read_only'


@dataclass(frozen=True)
class ResultadoPredecisionFormalPrestador:
    resultado: str
    eligible: bool
    requiere_revision_manual: bool
    score_resultado: object | None
    razones: tuple[str, ...] = field(default_factory=tuple)
    alertas: tuple[str, ...] = field(default_factory=tuple)
    bloqueos: tuple[str, ...] = field(default_factory=tuple)
    monto_maximo_sugerido: Decimal | None = None
    plazo_maximo_sugerido: int | None = None
    fuente: str = 'predecision_formal_prestadores_read_only_v2'

    def como_dict(self):
        return {
            'resultado': self.resultado,
            'eligible': self.eligible,
            'requiere_revision_manual': self.requiere_revision_manual,
            'razones': list(self.razones),
            'alertas': list(self.alertas),
            'bloqueos': list(self.bloqueos),
            'monto_maximo_sugerido': (
                format(self.monto_maximo_sugerido, 'f')
                if self.monto_maximo_sugerido is not None else None
            ),
            'plazo_maximo_sugerido': self.plazo_maximo_sugerido,
            'score_resultado': (
                self.score_resultado.como_dict() if self.score_resultado else None
            ),
            'fuente': self.fuente,
        }


def evaluar_predecision_prestador(solicitud, documentos_completos=False):
    capacidad = evaluar_capacidad_contractual_preliminar(
        solicitud,
        documentos_completos=documentos_completos,
    )
    datos_faltantes = list(capacidad.bloqueos)
    alertas = list(capacidad.advertencias)
    razones = []

    if datos_faltantes:
        resultado = RESULTADO_NO_EVALUABLE
        puntaje = None
        resumen = 'No evaluable: faltan datos minimos para una evaluacion preliminar.'
        razones.append('La solicitud no tiene datos suficientes para calcular la capacidad preliminar.')
    elif alertas:
        resultado = RESULTADO_REQUIERE_REVISION
        puntaje = _calcular_puntaje_informativo(capacidad, documentos_completos)
        resumen = 'Requiere revision: existen alertas operativas o documentales.'
        razones.append('La solicitud puede revisarse internamente, pero no queda preaprobable.')
    else:
        resultado = RESULTADO_PREAPROBABLE
        puntaje = _calcular_puntaje_informativo(capacidad, documentos_completos)
        resumen = 'Preaprobable de forma informativa para revision staff.'
        razones.append('Datos minimos completos, documentos completos y sin alertas preliminares.')

    return ResultadoPredecisionPrestador(
        solicitud_id=solicitud.id,
        resultado=resultado,
        puntaje_informativo=puntaje,
        razones=razones,
        alertas=alertas,
        datos_faltantes=datos_faltantes,
        resumen=resumen,
        capacidad_contractual=capacidad,
    )


def evaluar_predecision_formal_prestador(
    *, solicitud, politica, datacredito=None, centrales=None
):
    if politica.usa_fuentes_duales:
        return _evaluar_predecision_dual(
            solicitud=solicitud,
            politica=politica,
            centrales=centrales,
        )
    return _evaluar_predecision_fuente_unica(
        solicitud=solicitud,
        politica=politica,
        datacredito=datacredito,
    )


def _evaluar_predecision_fuente_unica(*, solicitud, politica, datacredito):
    estado_dc = str(getattr(datacredito, 'estado', '') or '')
    if estado_dc in {'ERROR_TRANSITORIO', 'ERROR_PERMANENTE'}:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
            razones=('DataCredito no pudo completar la consulta de forma controlada.',),
        )
    if estado_dc in {
        'AUTORIZACION_REQUERIDA', 'NO_CONFIGURADO', 'SIN_CACHE', 'EN_PROCESO', ''
    }:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('DataCredito no esta disponible para una evaluacion formal.',),
        )

    documentos = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    if not set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(documentos):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('Los documentos minimos obligatorios no estan completos.',),
        )
    if obtener_autorizacion_datacredito_vigente(solicitud) is None:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('No existe autorizacion DataCredito vigente para esta evaluacion.',),
        )
    if estado_dc != 'EXITOSO' or not getattr(datacredito, 'snapshot_id', None):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('La evaluacion requiere un snapshot DataCredito exitoso y vigente.',),
        )
    if (
        not politica.activa
        or not politica.configuracion_financiera_id
        or not politica.configuracion_financiera.activo
        or not politica.configuracion_financiera.version
    ):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('La politica no tiene configuracion financiera activa y versionada.',),
        )

    contrato = validar_contrato_prestador(solicitud)
    if contrato.bloqueos:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            razones=contrato.razones,
            alertas=contrato.alertas,
            bloqueos=contrato.bloqueos,
        )
    if contrato.requiere_revision_manual or not contrato.capacidad_automatica:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            razones=contrato.razones,
            alertas=contrato.alertas,
        )

    score = evaluar_score_prestador(solicitud, politica, datacredito)
    bloqueos = list(score.bloqueos)
    relacion = score.variables_calculadas.get('relacion_cuota_ingreso')
    if relacion is not None and Decimal(relacion) > politica.cuota_ingreso_maxima:
        razon = 'capacidad:relacion_cuota_ingreso_supera_limite'
        if politica.accion_exceso_capacidad == ConfiguracionScorePrestador.AccionExcesoCapacidad.BLOQUEAR:
            bloqueos.append(razon)
        else:
            return _resultado_formal(
                PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
                score=score,
                razones=(razon,),
                bloqueos=tuple(bloqueos),
            )
    if bloqueos:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            score=score,
            razones=('La solicitud presenta bloqueos verificables de politica o identidad.',),
            bloqueos=tuple(dict.fromkeys(bloqueos)),
        )
    if score.score_final is None:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            score=score,
            razones=score.razones or ('No fue posible calcular un score completo.',),
        )
    if score.requiere_revision_manual:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            score=score,
            razones=score.razones or ('La politica requiere revision manual.',),
        )
    if score.banda == BandaScorePrestador.Nombre.REVISION:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            score=score,
            razones=('La banda de score corresponde a revision manual.',),
        )
    return _resultado_formal(
        PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
        score=score,
        razones=('Datos completos, capacidad dentro de politica y score evaluable.',),
        eligible=True,
        requiere_revision_manual=False,
    )


def _evaluar_predecision_dual(*, solicitud, politica, centrales):
    if centrales is None:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('No existe un resultado consolidado de centrales de riesgo.',),
        )
    if centrales.estado_global == ESTADO_NO_EVALUABLE:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=(
                'Las fuentes requeridas no permiten completar la evaluacion formal.',
                *centrales.errores,
            ),
            alertas=centrales.alertas,
        )

    documentos = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    if not set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(documentos):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('Los documentos minimos obligatorios no estan completos.',),
        )
    if obtener_autorizacion_datacredito_vigente(solicitud) is None:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('No existe autorizacion DataCredito vigente para esta evaluacion.',),
        )
    if (
        not politica.activa
        or not politica.configuracion_financiera_id
        or not politica.configuracion_financiera.activo
        or not politica.configuracion_financiera.version
    ):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            razones=('La politica no tiene configuracion financiera activa y versionada.',),
        )

    fuentes_requeridas = (
        ('MiDecisor', centrales.decisor, politica.requiere_midecisor),
        ('HDCPlus', centrales.historial, politica.requiere_hdcplus),
    )
    faltantes = [
        nombre
        for nombre, resultado, requerido in fuentes_requeridas
        if requerido and not _consulta_exitosa_con_snapshot(resultado)
    ]
    if faltantes:
        resultado = (
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL
            if centrales.estado_global in {ESTADO_REVISION_MANUAL, ESTADO_PARCIAL}
            else PredecisionPrestadorAudit.Resultado.NO_EVALUABLE
        )
        return _resultado_formal(
            resultado,
            razones=(
                'No estan disponibles todas las fuentes requeridas: '
                + ', '.join(faltantes)
                + '.',
            ),
            alertas=centrales.alertas,
        )

    contrato = validar_contrato_prestador(solicitud)
    if contrato.bloqueos:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            razones=contrato.razones,
            alertas=contrato.alertas,
            bloqueos=contrato.bloqueos,
        )
    if contrato.requiere_revision_manual or not contrato.capacidad_automatica:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            razones=contrato.razones,
            alertas=contrato.alertas,
        )

    score = evaluar_score_prestador(solicitud, politica, centrales)
    bloqueos = list(score.bloqueos)
    relacion = score.variables_calculadas.get('relacion_cuota_ingreso')
    if relacion is not None and Decimal(relacion) > politica.cuota_ingreso_maxima:
        razon = 'capacidad:relacion_cuota_ingreso_supera_limite'
        if (
            politica.accion_exceso_capacidad
            == ConfiguracionScorePrestador.AccionExcesoCapacidad.BLOQUEAR
        ):
            bloqueos.append(razon)
        else:
            return _resultado_formal(
                PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
                score=score,
                razones=(razon,),
                alertas=centrales.alertas,
                bloqueos=tuple(bloqueos),
            )
    if bloqueos:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            score=score,
            razones=('La solicitud presenta bloqueos verificables de politica o identidad.',),
            alertas=centrales.alertas,
            bloqueos=tuple(dict.fromkeys(bloqueos)),
        )
    if score.score_final is None:
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            score=score,
            razones=score.razones or ('No fue posible calcular un score completo.',),
            alertas=centrales.alertas,
        )
    if (
        centrales.estado_global != ESTADO_COMPLETA
        or centrales.requiere_revision_manual
        or score.requiere_revision_manual
        or score.banda == BandaScorePrestador.Nombre.REVISION
    ):
        return _resultado_formal(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            score=score,
            razones=('La politica o la banda de score requiere revision manual.',),
            alertas=centrales.alertas,
        )
    return _resultado_formal(
        PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
        score=score,
        razones=(
            'MiDecisor y HDCPlus disponibles, capacidad dentro de politica y score evaluable.',
        ),
        alertas=centrales.alertas,
        eligible=True,
        requiere_revision_manual=False,
    )


def _consulta_exitosa_con_snapshot(resultado):
    return bool(
        resultado is not None
        and resultado.estado == ConsultaDatacreditoSnapshot.Estado.EXITOSO
        and resultado.snapshot_id
    )


def _resultado_formal(
    resultado, *, score=None, razones=(), alertas=(), bloqueos=(), eligible=False,
    requiere_revision_manual=True,
):
    alertas_score = score.alertas if score else ()
    bloqueos_score = score.bloqueos if score else ()
    return ResultadoPredecisionFormalPrestador(
        resultado=resultado,
        eligible=eligible,
        requiere_revision_manual=requiere_revision_manual,
        score_resultado=score,
        razones=tuple(dict.fromkeys(razones)),
        alertas=tuple(dict.fromkeys((*alertas_score, *alertas))),
        bloqueos=tuple(dict.fromkeys((*bloqueos_score, *bloqueos))),
        monto_maximo_sugerido=(score.monto_maximo_sugerido if score else None),
        plazo_maximo_sugerido=(score.plazo_maximo_sugerido if score else None),
    )


def _calcular_puntaje_informativo(capacidad, documentos_completos):
    puntaje = 70
    if documentos_completos:
        puntaje += 15
    if capacidad.porcentaje_compromiso_valor_pendiente is not None:
        porcentaje = capacidad.porcentaje_compromiso_valor_pendiente
        if porcentaje <= 40:
            puntaje += 15
        elif porcentaje <= 70:
            puntaje += 8
    return min(puntaje, 100)

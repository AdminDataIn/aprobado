from dataclasses import dataclass, field

from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_preliminar


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

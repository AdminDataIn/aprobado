from contractors.datacredito.dto import (
    AlertaDatacreditoPrestador,
    FUENTE_MOCK,
    NIVEL_RIESGO_ALTO,
    NIVEL_RIESGO_BAJO,
    NIVEL_RIESGO_MEDIO,
    NIVEL_RIESGO_NO_DISPONIBLE,
    ResultadoDatacreditoPrestador,
)
from contractors.datacredito.normalizador import construir_metadata_segura


ESCENARIO_BUENO = 'bueno'
ESCENARIO_MEDIO = 'medio'
ESCENARIO_MORA_SEVERA = 'mora_severa'
ESCENARIO_NO_DISPONIBLE = 'no_disponible'


def consultar_datacredito_mock(entrada, *, escenario=ESCENARIO_BUENO):
    escenario = (escenario or ESCENARIO_BUENO).strip().lower()
    if escenario == ESCENARIO_BUENO:
        return _resultado_mock(
            entrada,
            escenario=escenario,
            score=880,
            nivel_riesgo=NIVEL_RIESGO_BAJO,
            obligaciones_abiertas=2,
            obligaciones_en_mora=0,
        )
    if escenario == ESCENARIO_MEDIO:
        return _resultado_mock(
            entrada,
            escenario=escenario,
            score=690,
            nivel_riesgo=NIVEL_RIESGO_MEDIO,
            obligaciones_abiertas=4,
            obligaciones_en_mora=0,
            requiere_revision_manual=True,
        )
    if escenario == ESCENARIO_MORA_SEVERA:
        return _resultado_mock(
            entrada,
            escenario=escenario,
            score=430,
            nivel_riesgo=NIVEL_RIESGO_ALTO,
            mora_severa=True,
            mora_actual=True,
            obligaciones_abiertas=5,
            obligaciones_en_mora=2,
            requiere_revision_manual=True,
            alertas=(
                AlertaDatacreditoPrestador(
                    codigo='mora_severa',
                    nivel='ALTO',
                    mensaje='Mora severa simulada para evaluacion read-only.',
                ),
            ),
        )
    if escenario == ESCENARIO_NO_DISPONIBLE:
        return ResultadoDatacreditoPrestador(
            disponible=False,
            fuente=FUENTE_MOCK,
            nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
            requiere_revision_manual=True,
            error_tipo='mock_no_disponible',
            metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_MOCK, escenario=escenario),
        )

    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_MOCK,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo='escenario_mock_no_valido',
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_MOCK, escenario=escenario),
    )


def _resultado_mock(
    entrada,
    *,
    escenario,
    score,
    nivel_riesgo,
    mora_severa=False,
    mora_actual=False,
    obligaciones_abiertas=None,
    obligaciones_en_mora=None,
    requiere_revision_manual=False,
    alertas=(),
):
    return ResultadoDatacreditoPrestador(
        disponible=True,
        fuente=FUENTE_MOCK,
        score_externo=score,
        score_normalizado_0_1000=score,
        mora_severa=mora_severa,
        mora_actual=mora_actual,
        obligaciones_abiertas=obligaciones_abiertas,
        obligaciones_en_mora=obligaciones_en_mora,
        nivel_riesgo=nivel_riesgo,
        alertas=tuple(alertas),
        requiere_revision_manual=requiere_revision_manual,
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_MOCK, escenario=escenario),
    )

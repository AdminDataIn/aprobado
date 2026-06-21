from dataclasses import dataclass
from decimal import Decimal


MOTIVO_MAXIMO_CREDITOS_ACTIVOS_SUPERADO = 'maximo_creditos_activos_superado'
MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO = 'minimo_pagado_no_cumplido'
MOTIVO_DATOS_DE_PAGO_INSUFICIENTES = 'datos_de_pago_insuficientes'


@dataclass(frozen=True)
class ResultadoElegibilidad:
    aprobado: bool
    motivo: str | None = None


def evaluar_minimo_pagado(porcentaje_pagado, porcentaje_requerido) -> ResultadoElegibilidad:
    if porcentaje_pagado is None:
        return ResultadoElegibilidad(False, MOTIVO_DATOS_DE_PAGO_INSUFICIENTES)

    if Decimal(str(porcentaje_pagado)) < Decimal(str(porcentaje_requerido)):
        return ResultadoElegibilidad(False, MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO)

    return ResultadoElegibilidad(True)


def evaluar_creditos_activos(
    *,
    creditos_activos_actuales,
    maximo_creditos_activos_simultaneos=2,
    creditos_nuevos=1,
) -> ResultadoElegibilidad:
    proyectados = int(creditos_activos_actuales) + int(creditos_nuevos)
    if proyectados > int(maximo_creditos_activos_simultaneos):
        return ResultadoElegibilidad(False, MOTIVO_MAXIMO_CREDITOS_ACTIVOS_SUPERADO)
    return ResultadoElegibilidad(True)

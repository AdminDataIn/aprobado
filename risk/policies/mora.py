from dataclasses import dataclass
from decimal import Decimal


MOTIVO_MORA_ACTIVA_RELEVANTE = 'mora_activa_relevante'


@dataclass(frozen=True)
class ResultadoMora:
    aprobado: bool
    motivo: str | None = None
    credito_bloqueante_id: int | None = None
    dias_mora: int = 0


def evaluar_mora_relevante(creditos, *, dias_minimos_relevantes=1) -> ResultadoMora:
    dias_minimos_relevantes = int(dias_minimos_relevantes)
    for credito in creditos:
        estado = getattr(credito, 'estado', None)
        dias_mora = int(getattr(credito, 'dias_en_mora', 0) or 0)
        saldo = getattr(credito, 'saldo_pendiente', Decimal('0.00')) or Decimal('0.00')
        if estado == getattr(credito.EstadoCredito, 'EN_MORA', 'EN_MORA') and saldo > 0:
            if dias_mora >= dias_minimos_relevantes or dias_mora == 0:
                return ResultadoMora(
                    aprobado=False,
                    motivo=MOTIVO_MORA_ACTIVA_RELEVANTE,
                    credito_bloqueante_id=getattr(credito, 'id', None),
                    dias_mora=dias_mora,
                )

    return ResultadoMora(aprobado=True)

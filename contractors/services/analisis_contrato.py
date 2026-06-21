from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ResultadoAnalisisContratoIA:
    fecha_inicio_contrato: date | None = None
    fecha_fin_contrato: date | None = None
    valor_total_contrato: Decimal | None = None
    valor_pagado_estimado: Decimal | None = None
    valor_pendiente_estimado: Decimal | None = None
    empresa_contratante: str = ''
    cargo_rol: str = ''
    confidence: Decimal = Decimal('0.00')
    campos_requieren_confirmacion: tuple[str, ...] = field(default_factory=tuple)

    def como_dict(self) -> dict[str, Any]:
        return {
            'fecha_inicio_contrato': self.fecha_inicio_contrato,
            'fecha_fin_contrato': self.fecha_fin_contrato,
            'valor_total_contrato': self.valor_total_contrato,
            'valor_pagado_estimado': self.valor_pagado_estimado,
            'valor_pendiente_estimado': self.valor_pendiente_estimado,
            'empresa_contratante': self.empresa_contratante,
            'cargo_rol': self.cargo_rol,
            'confidence': self.confidence,
            'campos_requieren_confirmacion': self.campos_requieren_confirmacion,
        }


def analizar_contrato_con_ia(documento) -> ResultadoAnalisisContratoIA:
    """
    Punto de extension para conectar analisis con IA sobre contrato PDF.

    En esta fase no llama API externa, no hace OCR/parser local y no registra el
    contenido del contrato en logs. La informacion contractual sigue siendo
    confirmada por el usuario en el formulario.
    """
    return ResultadoAnalisisContratoIA(
        campos_requieren_confirmacion=(
            'fecha_inicio_contrato',
            'fecha_fin_contrato',
            'valor_total_contrato',
            'valor_pagado_estimado',
            'valor_pendiente_estimado',
            'empresa_contratante',
            'cargo_rol',
        ),
    )

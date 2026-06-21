"""Limite simple para politicas de riesgo.

Esto es solo una frontera de dominio, no un motor generico de reglas. Agrega
politicas concretas aqui solo cuando una regla real de producto necesite una
ruta compartida de decision de riesgo.
"""

from dataclasses import dataclass, field
from typing import Any

from risk.policies.capacidad import evaluar_capacidad
from risk.policies.elegibilidad import evaluar_creditos_activos, evaluar_minimo_pagado
from risk.policies.mora import evaluar_mora_relevante
from risk.policies.porcentaje_pagado import calcular_porcentaje_pagado


@dataclass(frozen=True)
class DecisionPolitica:
    aprobado: bool
    codigo: str
    motivos: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class MotorPoliticasRiesgo:
    """Fachada liviana para politicas explicitas de riesgo."""

    def evaluar(self, codigo_politica: str, contexto: dict[str, Any]) -> DecisionPolitica:
        if codigo_politica == 'porcentaje_pagado':
            porcentaje = calcular_porcentaje_pagado(contexto.get('credito'))
            return DecisionPolitica(
                aprobado=porcentaje is not None,
                codigo=codigo_politica,
                motivos=() if porcentaje is not None else ('datos_de_pago_insuficientes',),
                metadata={'paid_percentage': porcentaje},
            )

        if codigo_politica == 'mora':
            resultado = evaluar_mora_relevante(contexto.get('creditos', ()))
            return DecisionPolitica(
                aprobado=resultado.aprobado,
                codigo=codigo_politica,
                motivos=() if resultado.aprobado else (resultado.motivo,),
                metadata={
                    'blocking_credit_id': resultado.credito_bloqueante_id,
                    'days_past_due': resultado.dias_mora,
                },
            )

        if codigo_politica == 'capacidad':
            resultado = evaluar_capacidad(
                ingreso_mensual=contexto.get('ingreso_mensual'),
                cuota_actual=contexto.get('cuota_actual', 0),
                cuota_proyectada=contexto.get('cuota_proyectada', 0),
                porcentaje_maximo=contexto.get('porcentaje_maximo', 50),
                requerir_datos=contexto.get('requerir_datos', False),
            )
            return DecisionPolitica(
                aprobado=resultado.aprobado,
                codigo=codigo_politica,
                motivos=() if resultado.aprobado or not resultado.motivo else (resultado.motivo,),
                metadata={
                    'current_installment': resultado.cuota_actual,
                    'projected_installment': resultado.cuota_proyectada,
                    'committed_percentage': resultado.porcentaje_comprometido,
                    'maximum_capacity': resultado.capacidad_maxima,
                    'residual_capacity': resultado.capacidad_residual,
                },
            )

        if codigo_politica == 'minimo_pagado':
            resultado = evaluar_minimo_pagado(
                contexto.get('porcentaje_pagado'),
                contexto.get('porcentaje_requerido'),
            )
            return DecisionPolitica(
                aprobado=resultado.aprobado,
                codigo=codigo_politica,
                motivos=() if resultado.aprobado else (resultado.motivo,),
            )

        if codigo_politica == 'creditos_activos':
            resultado = evaluar_creditos_activos(
                creditos_activos_actuales=contexto.get('creditos_activos_actuales', 0),
                maximo_creditos_activos_simultaneos=contexto.get('maximo_creditos_activos_simultaneos', 2),
                creditos_nuevos=contexto.get('creditos_nuevos', 1),
            )
            return DecisionPolitica(
                aprobado=resultado.aprobado,
                codigo=codigo_politica,
                motivos=() if resultado.aprobado else (resultado.motivo,),
            )

        raise ValueError(f"Politica de riesgo no soportada: {codigo_politica}")


# Alias de compatibilidad con PR inicial.
PolicyDecision = DecisionPolitica


class RiskPolicyEngine(MotorPoliticasRiesgo):
    def evaluate(self, policy_code: str, context: dict[str, Any]) -> PolicyDecision:
        return self.evaluar(policy_code, context)

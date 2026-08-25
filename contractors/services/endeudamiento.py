from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


Q2 = Decimal('0.01')
Q4 = Decimal('0.0001')


@dataclass(frozen=True)
class ResultadoCargaFinancieraPrestador:
    ingreso_contractual: Decimal
    cuota_existente: Decimal
    cuota_nueva: Decimal
    cuota_total: Decimal
    relacion_cuota_ingreso: Decimal | None
    ingreso_disponible: Decimal
    otros_compromisos: Decimal = Decimal('0.00')

    def cuota_nueva_maxima(self, limite):
        limite_decimal = _decimal(limite)
        if limite_decimal is None or limite_decimal < 0:
            raise ValueError('limite_cuota_ingreso_invalido')
        return max(
            Decimal('0'),
            (
                self.ingreso_contractual * limite_decimal
            ) - self.cuota_existente - self.otros_compromisos,
        ).quantize(Q2, rounding=ROUND_HALF_UP)

    def capacidad_disponible(self, limite):
        return self.cuota_nueva_maxima(limite)


def calcular_carga_financiera_prestador(
    *, ingreso_contractual, cuota_existente, cuota_nueva,
    otros_compromisos=Decimal('0'),
):
    ingreso = _decimal(ingreso_contractual)
    existente = _decimal(cuota_existente)
    nueva = _decimal(cuota_nueva)
    otros = _decimal(otros_compromisos)
    if ingreso is None or ingreso <= 0:
        raise ValueError('ingreso_contractual_invalido')
    if existente is None or existente < 0:
        raise ValueError('cuota_existente_invalida')
    if nueva is None or nueva <= 0:
        raise ValueError('cuota_nueva_invalida')
    if otros is None or otros < 0:
        raise ValueError('otros_compromisos_invalidos')
    total = (existente + otros + nueva).quantize(Q2, rounding=ROUND_HALF_UP)
    return ResultadoCargaFinancieraPrestador(
        ingreso_contractual=ingreso.quantize(Q2, rounding=ROUND_HALF_UP),
        cuota_existente=existente.quantize(Q2, rounding=ROUND_HALF_UP),
        cuota_nueva=nueva.quantize(Q2, rounding=ROUND_HALF_UP),
        cuota_total=total,
        relacion_cuota_ingreso=(total / ingreso).quantize(Q4, rounding=ROUND_HALF_UP),
        ingreso_disponible=max(Decimal('0'), ingreso - existente - otros).quantize(
            Q2,
            rounding=ROUND_HALF_UP,
        ),
        otros_compromisos=otros.quantize(Q2, rounding=ROUND_HALF_UP),
    )


def _decimal(valor):
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None

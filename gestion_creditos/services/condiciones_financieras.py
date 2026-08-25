import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError


CENTAVO = Decimal('0.01')
CIEN = Decimal('100')
VERSION_FORMULA_PRESTADORES = 'prestadores_francesa_decimal_v1'


def redondear_moneda(valor):
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def normalizar_porcentaje(valor):
    return Decimal(str(valor)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ComponentesFinancierosCredito:
    monto_base: Decimal
    porcentaje_comision: Decimal
    comision: Decimal
    porcentaje_iva: Decimal
    iva: Decimal
    porcentaje_seguro: Decimal
    seguro_vida: Decimal
    porcentaje_fondo: Decimal
    fondo_garantia: Decimal
    otros_componentes: dict = field(default_factory=dict)
    otros_costos_total: Decimal = Decimal('0.00')
    capital_total_financiado: Decimal = Decimal('0.00')
    tasa_mensual: Decimal = Decimal('0.0000')
    plazo: int = 0
    cuota_aprobada: Decimal = Decimal('0.00')
    total_intereses: Decimal = Decimal('0.00')
    total_a_pagar: Decimal = Decimal('0.00')
    version_formula: str = VERSION_FORMULA_PRESTADORES
    version_configuracion: str = ''
    version_score: str = ''
    version_politica: str = ''

    def payload_hash(self):
        payload = asdict(self)
        payload['otros_componentes'] = {
            str(clave): str(redondear_moneda(valor))
            for clave, valor in sorted(self.otros_componentes.items())
        }
        for clave, valor in tuple(payload.items()):
            if isinstance(valor, Decimal):
                payload[clave] = str(valor)
        return payload

    def calcular_hash(self):
        contenido = json.dumps(
            self.payload_hash(), sort_keys=True, separators=(',', ':'),
        )
        return hashlib.sha256(contenido.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class CuotaFinancieraCalculada:
    numero: int
    capital: Decimal
    interes: Decimal
    valor: Decimal
    saldo: Decimal


def calcular_componentes_financieros(
    *,
    monto_base,
    porcentaje_comision,
    porcentaje_iva,
    porcentaje_seguro,
    porcentaje_fondo,
    tasa_mensual,
    plazo,
    otros_componentes=None,
    version_formula=VERSION_FORMULA_PRESTADORES,
    version_configuracion='',
    version_score='',
    version_politica='',
):
    monto = redondear_moneda(monto_base)
    plazo = int(plazo)
    if monto <= 0 or plazo <= 0:
        raise ValidationError('Monto y plazo deben ser mayores a cero.')

    pct_comision = normalizar_porcentaje(porcentaje_comision)
    pct_iva = normalizar_porcentaje(porcentaje_iva)
    pct_seguro = normalizar_porcentaje(porcentaje_seguro)
    pct_fondo = normalizar_porcentaje(porcentaje_fondo)
    tasa = normalizar_porcentaje(tasa_mensual)
    if min(pct_comision, pct_iva, pct_seguro, pct_fondo, tasa) < 0:
        raise ValidationError('Los porcentajes financieros no pueden ser negativos.')

    comision = redondear_moneda(monto * pct_comision / CIEN)
    iva = redondear_moneda(comision * pct_iva / CIEN)
    seguro = redondear_moneda(monto * pct_seguro / CIEN)
    fondo = redondear_moneda(monto * pct_fondo / CIEN)
    otros = {
        str(clave): redondear_moneda(valor)
        for clave, valor in (otros_componentes or {}).items()
    }
    if any(valor < 0 for valor in otros.values()):
        raise ValidationError('Los otros componentes no pueden ser negativos.')
    otros_total = redondear_moneda(sum(otros.values(), Decimal('0.00')))
    capital = redondear_moneda(monto + comision + iva + seguro + fondo + otros_total)
    tasa_decimal = tasa / CIEN
    if tasa_decimal:
        factor = (Decimal('1') + tasa_decimal) ** plazo
        cuota = redondear_moneda(
            capital * ((tasa_decimal * factor) / (factor - Decimal('1')))
        )
    else:
        cuota = redondear_moneda(capital / Decimal(plazo))
    total = redondear_moneda(cuota * Decimal(plazo))
    intereses = redondear_moneda(total - capital)

    return ComponentesFinancierosCredito(
        monto_base=monto,
        porcentaje_comision=pct_comision,
        comision=comision,
        porcentaje_iva=pct_iva,
        iva=iva,
        porcentaje_seguro=pct_seguro,
        seguro_vida=seguro,
        porcentaje_fondo=pct_fondo,
        fondo_garantia=fondo,
        otros_componentes=otros,
        otros_costos_total=otros_total,
        capital_total_financiado=capital,
        tasa_mensual=tasa,
        plazo=plazo,
        cuota_aprobada=cuota,
        total_intereses=intereses,
        total_a_pagar=total,
        version_formula=str(version_formula),
        version_configuracion=str(version_configuracion),
        version_score=str(version_score),
        version_politica=str(version_politica),
    )


def generar_plan_financiero(componentes):
    tasa = componentes.tasa_mensual / CIEN
    saldo = componentes.capital_total_financiado
    cuotas = []
    for numero in range(1, componentes.plazo + 1):
        if numero == componentes.plazo:
            capital = saldo
            interes = redondear_moneda(componentes.cuota_aprobada - capital)
        else:
            interes = redondear_moneda(saldo * tasa)
            capital = redondear_moneda(componentes.cuota_aprobada - interes)
        if capital <= 0 or interes < 0:
            raise ValidationError('Las condiciones financieras no generan un plan valido.')
        saldo = redondear_moneda(saldo - capital)
        if numero == componentes.plazo:
            saldo = Decimal('0.00')
        cuotas.append(CuotaFinancieraCalculada(
            numero=numero,
            capital=capital,
            interes=interes,
            valor=componentes.cuota_aprobada,
            saldo=saldo,
        ))
    return cuotas


def validar_paridad_componentes(componentes, plan, tolerancia=CENTAVO):
    capital = redondear_moneda(sum((cuota.capital for cuota in plan), Decimal('0')))
    intereses = redondear_moneda(sum((cuota.interes for cuota in plan), Decimal('0')))
    total = redondear_moneda(sum((cuota.valor for cuota in plan), Decimal('0')))
    comparaciones = {
        'capital_total_financiado': (capital, componentes.capital_total_financiado),
        'total_intereses': (intereses, componentes.total_intereses),
        'total_a_pagar': (total, componentes.total_a_pagar),
    }
    diferencias = [
        nombre for nombre, (generado, aprobado) in comparaciones.items()
        if abs(generado - aprobado) > tolerancia
    ]
    if diferencias:
        raise ValidationError(
            'El plan generado no coincide con el snapshot aprobado: '
            + ', '.join(diferencias)
        )
    return True


def validar_consistencia_componentes(componentes, tolerancia=CENTAVO):
    recalculado = calcular_componentes_financieros(
        monto_base=componentes.monto_base,
        porcentaje_comision=componentes.porcentaje_comision,
        porcentaje_iva=componentes.porcentaje_iva,
        porcentaje_seguro=componentes.porcentaje_seguro,
        porcentaje_fondo=componentes.porcentaje_fondo,
        tasa_mensual=componentes.tasa_mensual,
        plazo=componentes.plazo,
        otros_componentes=componentes.otros_componentes,
        version_formula=componentes.version_formula,
        version_configuracion=componentes.version_configuracion,
        version_score=componentes.version_score,
        version_politica=componentes.version_politica,
    )
    campos_monetarios = (
        'monto_base', 'comision', 'iva', 'seguro_vida', 'fondo_garantia',
        'otros_costos_total', 'capital_total_financiado', 'cuota_aprobada',
        'total_intereses', 'total_a_pagar',
    )
    diferencias = [
        campo for campo in campos_monetarios
        if abs(getattr(componentes, campo) - getattr(recalculado, campo)) > tolerancia
    ]
    if (
        componentes.tasa_mensual != recalculado.tasa_mensual
        or componentes.plazo != recalculado.plazo
    ):
        diferencias.append('tasa_o_plazo')
    if diferencias:
        raise ValidationError(
            'El snapshot financiero no es consistente con su formula: '
            + ', '.join(diferencias)
        )
    return True

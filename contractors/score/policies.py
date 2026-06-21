from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError

from contractors.score.dto import BandaScorePrestador


PUNTAJE_MINIMO = Decimal('0')
PUNTAJE_MAXIMO = Decimal('1000')


def decimal_configuracion(valor, defecto='0'):
    if valor is None:
        return Decimal(defecto)
    return Decimal(str(valor))


def normalizar_puntaje(valor):
    valor = decimal_configuracion(valor)
    if valor < PUNTAJE_MINIMO:
        return PUNTAJE_MINIMO
    if valor > PUNTAJE_MAXIMO:
        return PUNTAJE_MAXIMO
    return valor.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def validar_configuracion_score(configuracion):
    _validar_pesos(configuracion)
    _validar_bandas(configuracion)
    return True


def obtener_bandas(configuracion):
    return tuple(
        BandaScorePrestador(
            nombre=banda['nombre'],
            minimo=decimal_configuracion(banda['minimo']),
            maximo=decimal_configuracion(banda['maximo']),
            monto_maximo=decimal_configuracion(banda['monto_maximo']),
            plazo_maximo_meses=int(banda['plazo_maximo_meses']),
            decision=banda['decision'],
        )
        for banda in configuracion.get('bandas', ())
    )


def resolver_banda(score, configuracion):
    score = normalizar_puntaje(score)
    for banda in obtener_bandas(configuracion):
        if banda.minimo <= score <= banda.maximo:
            return banda
    raise ValidationError('score_sin_banda_configurada')


def calcular_puntaje_capacidad_contractual(capacidad_contractual, configuracion):
    componente = configuracion['componentes']['capacidad']
    capacidad_maxima = decimal_configuracion(capacidad_contractual.get('capacidad_maxima_estimada'))
    monto_solicitado = decimal_configuracion(capacidad_contractual.get('monto_solicitado'))

    if capacidad_maxima <= Decimal('0.00'):
        return None

    uso = (monto_solicitado / capacidad_maxima).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    for punto in componente.get('puntos_por_uso', ()):
        if uso <= decimal_configuracion(punto['maximo_ratio']):
            return normalizar_puntaje(punto['score'])
    return normalizar_puntaje(componente.get('valor_default'))


def _validar_pesos(configuracion):
    total = Decimal('0.00')
    for datos in configuracion.get('componentes', {}).values():
        if datos.get('penaliza'):
            continue
        total += decimal_configuracion(datos.get('peso'))

    if total != Decimal('1.00'):
        raise ValidationError('pesos_score_no_suman_1_00')


def _validar_bandas(configuracion):
    bandas = sorted(obtener_bandas(configuracion), key=lambda banda: banda.minimo)
    if not bandas:
        raise ValidationError('bandas_score_requeridas')

    esperado = Decimal('0')
    for banda in bandas:
        if banda.minimo != esperado:
            raise ValidationError('bandas_score_no_cubren_rango')
        if banda.maximo < banda.minimo:
            raise ValidationError('banda_score_invalida')
        esperado = banda.maximo + Decimal('1')

    if bandas[-1].maximo != Decimal('1000'):
        raise ValidationError('bandas_score_no_cubren_rango')

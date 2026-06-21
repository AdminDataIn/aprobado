from decimal import Decimal, ROUND_HALF_UP

from contractors.score.configuracion import obtener_configuracion_score_prestadores
from contractors.score.dto import (
    ComponenteScorePrestador,
    EntradaScoreInternoPrestador,
    PenalizacionScorePrestador,
    ResultadoScoreInternoPrestador,
)
from contractors.score.policies import (
    decimal_configuracion,
    normalizar_puntaje,
    resolver_banda,
    validar_configuracion_score,
)


ESTADO_EVALUADO = 'EVALUADO'
ESTADO_PENDIENTE = 'PENDIENTE'


def evaluar_score_interno_prestador(
    entrada: EntradaScoreInternoPrestador,
    configuracion=None,
) -> ResultadoScoreInternoPrestador:
    configuracion = configuracion or obtener_configuracion_score_prestadores()
    validar_configuracion_score(configuracion)

    componentes = []
    componentes_pendientes = []
    penalizaciones = []
    suma_ponderada = Decimal('0.00')
    suma_pesos_evaluados = Decimal('0.00')

    for nombre, datos_componente in configuracion.get('componentes', {}).items():
        if datos_componente.get('penaliza'):
            penalizacion = _evaluar_penalizacion(nombre, datos_componente, entrada)
            if penalizacion:
                penalizaciones.append(penalizacion)
            continue

        componente = _evaluar_componente(nombre, datos_componente, entrada)
        componentes.append(componente)

        if componente.estado == ESTADO_PENDIENTE:
            componentes_pendientes.append(nombre)
            continue

        suma_ponderada += componente.puntaje_ponderado
        suma_pesos_evaluados += componente.peso

    if suma_pesos_evaluados > Decimal('0.00'):
        score_base = suma_ponderada / suma_pesos_evaluados
    else:
        score_base = Decimal('0.00')

    score_con_penalizaciones = score_base + sum(
        (penalizacion.penalizacion for penalizacion in penalizaciones),
        Decimal('0.00'),
    )
    score_final = normalizar_puntaje(score_con_penalizaciones)
    banda = resolver_banda(score_final, configuracion)
    razones = _razones_resultado(componentes_pendientes, penalizaciones)
    requiere_revision_manual = bool(componentes_pendientes) or banda.nombre == 'REVISION'
    capacidad_financiera = _calcular_capacidad_financiera(entrada, configuracion, banda)
    advertencias_tecnicas = []
    if capacidad_financiera.get('estado') == 'NO_EVALUADA':
        advertencias_tecnicas.append('capacidad_financiera_sin_ingreso_u_obligaciones_formales')

    return ResultadoScoreInternoPrestador(
        version_configuracion=configuracion['version'],
        score_final=score_final,
        banda=banda,
        decision_preliminar=banda.decision,
        monto_maximo_sugerido=capacidad_financiera.get('monto_final', banda.monto_maximo),
        plazo_maximo_sugerido=capacidad_financiera.get('plazo_final', banda.plazo_maximo_meses),
        componentes=tuple(componentes),
        componentes_pendientes=tuple(componentes_pendientes),
        penalizaciones=tuple(penalizaciones),
        razones=tuple(razones),
        requiere_revision_manual=requiere_revision_manual,
        datacredito_status=entrada.datacredito_status,
        pesos_usados=_pesos_usados(configuracion),
        topes_aplicados=_topes_aplicados(configuracion, banda, capacidad_financiera),
        regla_cuota_ingreso=_regla_cuota_ingreso(configuracion),
        tasa_mensual=_tasa_mensual(configuracion),
        tasa_efectiva_anual=_tasa_efectiva_anual(configuracion),
        capacidad_financiera=capacidad_financiera,
        advertencias_tecnicas=tuple(advertencias_tecnicas),
    )


def _evaluar_componente(nombre, datos_componente, entrada):
    peso = decimal_configuracion(datos_componente.get('peso'))
    valor = entrada.componentes.get(nombre, datos_componente.get('valor_default'))
    if valor is None:
        return ComponenteScorePrestador(
            nombre=nombre,
            peso=peso,
            valor=None,
            estado=datos_componente.get('estado_si_no_disponible', ESTADO_PENDIENTE),
            razon=f'{nombre}_pendiente',
        )

    valor = normalizar_puntaje(valor)
    return ComponenteScorePrestador(
        nombre=nombre,
        peso=peso,
        valor=valor,
        puntaje_ponderado=(valor * peso).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        estado=ESTADO_EVALUADO,
    )


def _evaluar_penalizacion(nombre, datos_componente, entrada):
    valor = entrada.componentes.get(nombre, datos_componente.get('valor_default'))
    if valor is None:
        return None

    valor = normalizar_puntaje(valor)
    umbral = decimal_configuracion(datos_componente.get('umbral_penalizacion'))
    if valor >= umbral:
        return None

    penalizacion = decimal_configuracion(datos_componente.get('penalizacion'))
    return PenalizacionScorePrestador(
        nombre=nombre,
        valor=valor,
        penalizacion=penalizacion,
        razon=f'{nombre}_bajo_umbral',
    )


def _razones_resultado(componentes_pendientes, penalizaciones):
    razones = []
    razones.extend(f'componente_pendiente:{nombre}' for nombre in componentes_pendientes)
    razones.extend(penalizacion.razon for penalizacion in penalizaciones)
    return razones


def _pesos_usados(configuracion):
    return {
        nombre: str(decimal_configuracion(datos.get('peso')))
        for nombre, datos in configuracion.get('componentes', {}).items()
    }


def _topes_aplicados(configuracion, banda, capacidad_financiera):
    producto = configuracion.get('producto') or {}
    return {
        'monto_maximo_producto': str(decimal_configuracion(producto.get('monto_maximo'), '0.00')),
        'plazo_maximo_producto': int(producto.get('plazo_maximo_meses') or 0),
        'monto_maximo_banda': str(banda.monto_maximo),
        'plazo_maximo_banda': banda.plazo_maximo_meses,
        'monto_por_capacidad_financiera': str(capacidad_financiera.get('monto_por_capacidad', Decimal('0.00'))),
        'monto_final': str(capacidad_financiera.get('monto_final', banda.monto_maximo)),
        'plazo_final': capacidad_financiera.get('plazo_final', banda.plazo_maximo_meses),
    }


def _regla_cuota_ingreso(configuracion):
    ratio = decimal_configuracion(
        (configuracion.get('reglas_criticas') or {}).get('cuota_ingreso_maximo'),
        '0.00',
    )
    return {
        'cuota_ingreso_maximo': str(ratio),
        'formula': 'cuota_proyectada / ingreso_disponible <= cuota_ingreso_maximo',
        'ingreso_disponible': 'ingreso_neto - obligaciones_mensuales',
    }


def _tasa_mensual(configuracion):
    return decimal_configuracion((configuracion.get('producto') or {}).get('tasa_mensual'), '0.00')


def _tasa_efectiva_anual(configuracion):
    tasa_mensual = _tasa_mensual(configuracion) / Decimal('100')
    tea = ((Decimal('1') + tasa_mensual) ** Decimal('12') - Decimal('1')) * Decimal('100')
    return tea.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _calcular_capacidad_financiera(entrada, configuracion, banda):
    componentes = entrada.componentes or {}
    ingreso_neto = _decimal_opcional(componentes.get('ingreso_neto'))
    obligaciones = _decimal_opcional(componentes.get('obligaciones_mensuales'))
    monto_solicitado = _decimal_opcional(componentes.get('monto_solicitado'))
    plazo_solicitado = _entero_opcional(componentes.get('plazo_solicitado'))
    meses_restantes = _entero_opcional(componentes.get('meses_restantes_contrato'))
    valor_pendiente = _decimal_opcional(componentes.get('valor_pendiente_cobrar'))

    producto = configuracion.get('producto') or {}
    plazo_producto = int(producto.get('plazo_maximo_meses') or banda.plazo_maximo_meses)
    plazo_base = min(valor for valor in (banda.plazo_maximo_meses, plazo_producto) if valor > 0) if banda.plazo_maximo_meses else 0
    if plazo_solicitado:
        plazo_base = min(plazo_base, plazo_solicitado) if plazo_base else plazo_solicitado
    if meses_restantes:
        plazo_base = min(plazo_base, meses_restantes) if plazo_base else meses_restantes

    monto_base = banda.monto_maximo
    monto_producto = decimal_configuracion(producto.get('monto_maximo'), '0.00')
    if monto_producto > Decimal('0.00'):
        monto_base = min(monto_base, monto_producto)
    if monto_solicitado:
        monto_base = min(monto_base, monto_solicitado)
    if valor_pendiente:
        monto_base = min(monto_base, valor_pendiente)

    if ingreso_neto is None or obligaciones is None or plazo_base <= 0:
        return {
            'estado': 'NO_EVALUADA',
            'monto_final': monto_base,
            'plazo_final': plazo_base,
            'monto_por_capacidad': Decimal('0.00'),
            'razon': 'ingreso_neto_u_obligaciones_no_disponibles',
        }

    ingreso_disponible = max(Decimal('0.00'), ingreso_neto - obligaciones)
    ratio_maximo = decimal_configuracion(
        (configuracion.get('reglas_criticas') or {}).get('cuota_ingreso_maximo'),
        '0.00',
    )
    cuota_maxima = (ingreso_disponible * ratio_maximo).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    monto_por_capacidad = _valor_presente_desde_cuota(
        cuota=cuota_maxima,
        tasa_mensual_porcentual=_tasa_mensual(configuracion),
        plazo_meses=plazo_base,
    )
    monto_final = min(monto_base, monto_por_capacidad).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    cuota_estimada = _cuota_desde_monto(
        monto=monto_final,
        tasa_mensual_porcentual=_tasa_mensual(configuracion),
        plazo_meses=plazo_base,
    )
    relacion = Decimal('0.00')
    if ingreso_disponible > Decimal('0.00'):
        relacion = (cuota_estimada / ingreso_disponible).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    return {
        'estado': 'EVALUADA',
        'ingreso_neto': str(ingreso_neto),
        'obligaciones_mensuales': str(obligaciones),
        'ingreso_disponible': str(ingreso_disponible),
        'cuota_maxima': str(cuota_maxima),
        'cuota_estimada': str(cuota_estimada),
        'relacion_cuota_ingreso': str(relacion),
        'monto_por_capacidad': monto_por_capacidad,
        'monto_final': monto_final,
        'plazo_final': plazo_base,
        'viable': monto_final > Decimal('0.00') and relacion <= ratio_maximo,
    }


def _valor_presente_desde_cuota(*, cuota, tasa_mensual_porcentual, plazo_meses):
    if plazo_meses <= 0:
        return Decimal('0.00')
    tasa = tasa_mensual_porcentual / Decimal('100')
    if tasa == Decimal('0.00'):
        return (cuota * Decimal(plazo_meses)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    factor_descuento = Decimal('1') / ((Decimal('1') + tasa) ** plazo_meses)
    factor = (Decimal('1') - factor_descuento) / tasa
    return (cuota * factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _cuota_desde_monto(*, monto, tasa_mensual_porcentual, plazo_meses):
    if plazo_meses <= 0:
        return Decimal('0.00')
    tasa = tasa_mensual_porcentual / Decimal('100')
    if tasa == Decimal('0.00'):
        return (monto / Decimal(plazo_meses)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    factor_descuento = Decimal('1') / ((Decimal('1') + tasa) ** plazo_meses)
    cuota = monto * (tasa / (Decimal('1') - factor_descuento))
    return cuota.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _decimal_opcional(valor):
    if valor in (None, ''):
        return None
    return Decimal(str(valor))


def _entero_opcional(valor):
    if valor in (None, ''):
        return None
    return int(valor)

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from integrations.datacredito.dto import (
    ESTADO_APELLIDO_NO_COINCIDE,
    ESTADO_CONFIGURACION_BLOQUEADA,
    ESTADO_CONFIGURACION_VENCIDA,
    ESTADO_ERROR_CREDENCIAL_SERVICIO,
    ESTADO_ERROR_TECNICO,
    ESTADO_ERROR_TEMPORAL,
    ESTADO_EXITOSA_CON_INFORMACION,
    ESTADO_EXITOSA_SIN_INFORMACION,
    ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    FUENTE_HISTORIAL_CREDITO,
    FUENTE_MIDECISOR,
    FUENTE_SCORE_HISTORIA_CREDITO,
    FUENTE_SCORE_MIDECISOR,
    NIVEL_RIESGO_ALTO,
    NIVEL_RIESGO_BAJO,
    NIVEL_RIESGO_MEDIO,
    NIVEL_RIESGO_NO_DISPONIBLE,
    ResultadoDatacreditoNormalizado,
)


COMPORTAMIENTO_PAGO = {
    'N': 'al_dia',
    '1': 'mora_30',
    '2': 'mora_60',
    '3': 'mora_90',
    '4': 'mora_120',
    '5': 'mora_150',
    '6': 'mora_180',
    'C': 'cartera_castigada',
    'D': 'dudoso_recaudo',
}
CODIGOS_MORA_SEVERA = {'3', '4', '5', '6', 'C', 'D'}
VERSION_NORMALIZADOR = 'datacredito-normalizado-v3'

MIDECISOR_ERRORES = {
    'SOLICITUD_INVALIDA': ('solicitud_funcional_invalida', 'ERROR_SOLICITUD'),
    'CONSULTA_FALLIDA': ('consulta_funcional_fallida', 'RESPUESTA_FUNCIONAL'),
    'RESPUESTA_INCONSISTENTE': ('respuesta_funcional_inconsistente', 'RESPUESTA_INVALIDA'),
    'ALCANCE_PN_NO_CONFIRMADO': ('tx17_pn_no_confirmado', 'RESPUESTA_INVALIDA'),
    ESTADO_ERROR_TEMPORAL: ('excepcion_interna_proveedor', 'ERROR_PROVEEDOR'),
    ESTADO_ERROR_TECNICO: ('respuesta_funcional_indeterminada', 'RESPUESTA_INVALIDA'),
}

# Tabla 41 del manual HDC: pago total/canceladas; no inferir cierre por saldo cero.
HDC_CUENTAS_CERRADAS = {'03', '08', '09', '11', '12', '13', '15', '16', '17'}
HDC_CUENTAS_VIGENTES = {'01', '02', '05', '06', '14'}
HDC_DESCRIPCIONES_VIGENTES = {'AL DIA', 'EN MORA', 'DUDOSO RECAUDO', 'CASTIGADA', 'INSOLUTA'}

HDC_ESTADOS = {
    '02': ESTADO_ERROR_CREDENCIAL_SERVICIO,
    '04': ESTADO_ERROR_TECNICO,
    '05': ESTADO_ERROR_TECNICO,
    '06': ESTADO_APELLIDO_NO_COINCIDE,
    '09': ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    '10': ESTADO_APELLIDO_NO_COINCIDE,
    '12': ESTADO_CONFIGURACION_BLOQUEADA,
    '13': ESTADO_EXITOSA_CON_INFORMACION,
    '14': ESTADO_EXITOSA_SIN_INFORMACION,
    '17': ESTADO_CONFIGURACION_VENCIDA,
    '18': ESTADO_CONFIGURACION_BLOQUEADA,
    '23': ESTADO_ERROR_TEMPORAL,
}


def normalizar_midecisor_pn(raw):
    datos = _como_dict(raw)
    content = _path(datos, 'content') or {}
    respuesta = _path(content, 'respuesta') or {}
    validacion = _path(respuesta, 'validacion') or {}
    info_transaccion = _path(content, 'infoTransaccion') or {}
    codigos = _codigos_midecisor(info_transaccion)
    con_informacion = _normalizar_bool(_path(validacion, 'conInformacion'))
    codigo_hc = codigos.get('HC')
    codigo_tx = codigos.get('TX')
    estado = _estado_midecisor(
        codigo_hc=codigo_hc, codigo_tx=codigo_tx, con_informacion=con_informacion,
        status=_path(datos, 'status'), status_content=_path(content, 'status'),
        estructura_valida=(
            isinstance(content, Mapping) and isinstance(info_transaccion, Mapping)
            and not codigos.get('_invalido')
        ),
        validacion_presente=(
            isinstance(validacion, Mapping) and 'conInformacion' in validacion
            and _modulos_midecisor_validos(respuesta)
        ),
        contiene_informacion=_midecisor_contiene_informacion(respuesta),
    )
    error_codigo, error_tipo = MIDECISOR_ERRORES.get(estado, (None, None))
    if estado != ESTADO_EXITOSA_CON_INFORMACION:
        return ResultadoDatacreditoNormalizado(
            disponible=False, fuente=FUENTE_MIDECISOR, servicio=FUENTE_MIDECISOR,
            estado=estado, con_informacion=con_informacion,
            codigo_respuesta=codigo_hc, response_code=codigo_hc,
            requiere_revision_manual=True, error_tipo=error_tipo,
            metadata_segura={
                'estado': estado, 'codigo_hc': codigo_hc, 'codigo_tx': codigo_tx,
                'error_codigo': error_codigo,
            },
        )
    return _extraer_campos_midecisor_pn(
        datos, estado=estado, con_informacion=con_informacion,
        codigo_hc=codigo_hc, codigo_tx=codigo_tx,
    )


def _extraer_campos_midecisor_pn(datos, *, estado, con_informacion, codigo_hc, codigo_tx):
    # Shared projection only; authorization/classification remain at the caller.
    content = _path(datos, 'content') or {}
    respuesta = _path(content, 'respuesta') or {}
    riesgo = _path(respuesta, 'informacionRiesgo') or {}
    endeudamiento = _path(respuesta, 'endeudamiento') or {}
    comportamiento = _path(respuesta, 'comportamientoCrediticio') or {}
    indicadores = _path(comportamiento, 'indicadoresValores') or {}
    info_transaccion = _path(content, 'infoTransaccion') or {}
    error_codigo, error_tipo = MIDECISOR_ERRORES.get(estado, (None, None))
    score = _entero(_path(riesgo, 'score') if riesgo else _buscar_valor(datos, ('score', 'puntaje', 'scoreDecisor')))
    score_normalizado = _normalizar_score(score)
    viabilidad = _valor_limpio(_path(riesgo, 'viabilidad') if riesgo else _buscar_valor(datos, ('viabilidad', 'viable')))
    monto_sugerido = _entero(_path(riesgo, 'montoSugerido') if riesgo else _buscar_valor(datos, ('montoSugerido', 'monto_sugerido', 'valorAprobado', 'cupo')))
    rating_recaudo = _valor_limpio(_path(riesgo, 'ratingRecaudos') if riesgo else _buscar_valor(datos, ('ratingRecaudos', 'rating_recaudos')))
    alertas = _path(riesgo, 'alertas') or []
    alertas_resumen, requiere_cumplimiento = _resumir_alertas_midecisor(alertas)
    saldo_mora = _decimal(_path(indicadores, 'saldoMora') if indicadores else _buscar_valor(datos, ('saldoMora', 'saldo_mora', 'valorMora', 'saldoEnMora')))
    vector = _path(comportamiento, 'comportamientoPago', 'vectorComportamiento')
    mora_vector = detectar_mora_severa_desde_vector(vector)
    mora_severa = _resolver_mora_severa(mora_vector=mora_vector, saldo_mora=saldo_mora, con_informacion=con_informacion)
    mora_actual = True if saldo_mora is not None and saldo_mora > 0 else (mora_severa if mora_severa else None)
    disponible = estado == ESTADO_EXITOSA_CON_INFORMACION
    requiere_manual = (
        estado != ESTADO_EXITOSA_CON_INFORMACION
        or score_normalizado is None
        or requiere_cumplimiento
    )

    return ResultadoDatacreditoNormalizado(
        disponible=disponible,
        fuente=FUENTE_MIDECISOR,
        servicio=FUENTE_MIDECISOR,
        estado=estado,
        con_informacion=con_informacion,
        codigo_respuesta=str(codigo_hc) if codigo_hc is not None else None,
        descripcion_respuesta=_valor_limpio(_path(content, 'response') or _path(info_transaccion, 'msjExcepcion')),
        score=score,
        score_midecisor=score,
        fuente_score=FUENTE_SCORE_MIDECISOR if score is not None else None,
        score_normalizado_0_1000=score_normalizado,
        viable=_interpretar_viabilidad(viabilidad),
        monto_sugerido=monto_sugerido,
        saldo_actual=_decimal(_path(indicadores, 'saldoActual')),
        saldo_mora=saldo_mora,
        valor_cuota_total=_decimal(_path(indicadores, 'valorCuota')),
        creditos_vigentes=_entero(_path(indicadores, 'creditosVigentes')),
        creditos_cerrados=_entero(_path(indicadores, 'creditosCerrados')),
        porcentaje_deuda=_decimal(_path(indicadores, 'porcentajeDeuda')),
        ingreso_estimado=_decimal(_path(endeudamiento, 'ingreso')),
        porcentaje_cuota_vs_ingreso=_decimal(_path(endeudamiento, 'porcentajeCuotaVsIngreso')),
        nivel_riesgo=_nivel_desde_score(score),
        mora_severa=mora_severa,
        mora_actual=mora_actual,
        response_code=str(codigo_hc) if codigo_hc is not None else None,
        viabilidad=viabilidad,
        rating_recaudo=rating_recaudo,
        cantidad_alertas=len(alertas) if isinstance(alertas, list) else 0,
        requiere_revision_cumplimiento=requiere_cumplimiento,
        bloqueo_automatico=False,
        requiere_revision_manual=requiere_manual,
        error_tipo=error_tipo,
        alertas_resumen=alertas_resumen,
        metadata_segura={
            'fuente': FUENTE_MIDECISOR,
            'tipo': 'persona_natural',
            'estado': estado,
            'codigo_hc': str(codigo_hc) if codigo_hc is not None else None,
            'codigo_tx': str(codigo_tx) if codigo_tx is not None else None,
            'error_codigo': error_codigo,
            'score_detectado': score is not None,
            'cantidad_alertas': len(alertas) if isinstance(alertas, list) else 0,
            'requiere_revision_cumplimiento': requiere_cumplimiento,
            'rating_recaudos': rating_recaudo,
        },
    )


def normalizar_midecisor_pj(raw):
    datos = _como_dict(raw)
    score = extraer_score_decisor(datos)
    nivel_riesgo_raw = _buscar_valor(datos, ('nivelRiesgo', 'nivel_riesgo', 'riesgo'))
    embargos = _buscar_bool(datos, ('embargos', 'tieneEmbargos', 'procesoEmbargo'))
    liquidacion = _buscar_bool(datos, ('liquidacion', 'enLiquidacion', 'procesoLiquidacion'))
    response_code = _buscar_valor(datos, ('responseCode', 'response_code', 'codigoRespuesta'))
    alertas = []
    if embargos:
        alertas.append('embargos_reportados')
    if liquidacion:
        alertas.append('liquidacion_reportada')

    return ResultadoDatacreditoNormalizado(
        disponible=True,
        fuente=FUENTE_MIDECISOR,
        servicio=FUENTE_MIDECISOR,
        estado=ESTADO_EXITOSA_CON_INFORMACION,
        con_informacion=True,
        codigo_respuesta=str(response_code) if response_code is not None else None,
        score=score,
        score_midecisor=score,
        fuente_score=FUENTE_SCORE_MIDECISOR if score is not None else None,
        score_normalizado_0_1000=_normalizar_score(score),
        nivel_riesgo=_normalizar_nivel_riesgo(nivel_riesgo_raw) or _nivel_desde_score(score),
        mora_severa=None,
        mora_actual=None,
        embargos=embargos,
        liquidacion=liquidacion,
        response_code=str(response_code) if response_code is not None else None,
        alertas_resumen=tuple(alertas),
        metadata_segura={
            'fuente': FUENTE_MIDECISOR,
            'tipo': 'persona_juridica',
            'score_detectado': score is not None,
        },
    )


def normalizar_historial_credito(raw):
    datos = _como_dict(raw)
    hdc_estructura = _estructura_hdc_segura(datos)
    hdc_resumen = extraer_resumen_hdcplus(datos)
    product_result = _path(datos, 'ReportHDCplus', 'productResult') or {}
    response_code = (
        _path(product_result, 'responseCode')
        or _buscar_valor(datos, ('responseCode', 'response_code', 'codigoRespuesta'))
    )
    response_code = str(response_code).zfill(2) if response_code is not None and str(response_code).isdigit() else (
        str(response_code) if response_code is not None else None
    )
    response_desc = _path(product_result, 'responseDesc') or _buscar_valor(datos, ('responseDesc', 'response_description'))
    estado = HDC_ESTADOS.get(response_code, ESTADO_ERROR_TECNICO)
    con_informacion = True if estado == ESTADO_EXITOSA_CON_INFORMACION else (
        False if estado in {ESTADO_EXITOSA_SIN_INFORMACION, ESTADO_IDENTIFICACION_NO_ENCONTRADA} else None
    )
    scores_hdc = _extraer_scores_hdc(datos)
    score_legacy = None if scores_hdc else _entero(_buscar_valor(datos, ('scoreCrediticio', 'puntajeCredito')))
    vector = _buscar_valor(datos, ('vector', 'comportamientoPago', 'paymentBehavior', 'comportamiento_pago'))
    mora_severa = detectar_mora_severa_desde_vector(vector)
    saldo_mora = _decimal(_buscar_valor(datos, ('saldoMora', 'saldo_mora', 'saldoEnMora')))
    saldo_total_hdc = _decimal(hdc_resumen.get('saldo_total_hdc'))
    saldo_mora_hdc = _decimal(hdc_resumen.get('saldo_mora_hdc'))
    cuota_total_hdc = _decimal(hdc_resumen.get('cuota_total_hdc'))
    max_mora_dias = _entero(hdc_resumen.get('max_mora_dias'))
    obligaciones_en_mora = _entero(hdc_resumen.get('liabilities_en_mora'))
    obligaciones_castigadas = _entero(hdc_resumen.get('liabilities_castigadas'))
    obligaciones_vigentes = _entero(hdc_resumen.get('liabilities_vigentes'))
    total_obligaciones = _entero(hdc_resumen.get('total_liabilities'))
    if saldo_mora is None:
        saldo_mora = saldo_mora_hdc
    if mora_severa is not True and (
        (max_mora_dias is not None and max_mora_dias >= 90)
        or (obligaciones_castigadas or 0) > 0
    ):
        mora_severa = True
    elif mora_severa is None and total_obligaciones is not None:
        mora_severa = False
    if saldo_mora is not None and saldo_mora > 0:
        mora_actual = True
    elif mora_severa is True:
        mora_actual = True
    elif obligaciones_en_mora is not None:
        mora_actual = obligaciones_en_mora > 0
    else:
        mora_actual = None

    disponible = estado == ESTADO_EXITOSA_CON_INFORMACION
    requiere_manual = (estado != ESTADO_EXITOSA_CON_INFORMACION
                       or not hdc_resumen['carga_mensual_completa'])

    return ResultadoDatacreditoNormalizado(
        disponible=disponible,
        fuente=FUENTE_HISTORIAL_CREDITO,
        servicio=FUENTE_HISTORIAL_CREDITO,
        estado=estado,
        con_informacion=con_informacion,
        codigo_respuesta=response_code,
        descripcion_respuesta=_valor_limpio(response_desc),
        score=score_legacy,
        scores_hdc=scores_hdc,
        fuente_score=FUENTE_SCORE_HISTORIA_CREDITO if scores_hdc else None,
        score_normalizado_0_1000=None,
        nivel_riesgo=NIVEL_RIESGO_ALTO if mora_severa else NIVEL_RIESGO_NO_DISPONIBLE,
        saldo_actual=saldo_total_hdc,
        saldo_mora=saldo_mora,
        valor_cuota_total=cuota_total_hdc,
        creditos_vigentes=obligaciones_vigentes,
        creditos_cerrados=_entero(hdc_resumen.get('liabilities_cerradas')),
        mora_severa=mora_severa,
        mora_actual=mora_actual,
        response_code=response_code,
        requiere_revision_manual=requiere_manual,
        error_tipo=None if disponible else _error_tipo_desde_estado(estado),
        alertas_resumen=(('mora_severa_detectada',) if mora_severa else tuple()) + (
            ('hdc:carga_mensual_incompleta',) if not hdc_resumen['carga_mensual_completa'] else tuple()
        ),
        metadata_segura={
            'fuente': FUENTE_HISTORIAL_CREDITO,
            'estado': estado,
            'response_code': response_code,
            'scores_hdc_detectados': len(scores_hdc),
            'hdc_estructura': hdc_estructura,
            'hdc_resumen': hdc_resumen,
        },
    )


def extraer_score_decisor(raw):
    datos = _como_dict(raw)
    return _entero(_path(datos, 'content', 'respuesta', 'informacionRiesgo', 'score') or _buscar_valor(
        datos,
        ('score', 'puntaje', 'scoreDecisor', 'puntajeDecisor', 'calificacion'),
    ))


def extraer_score_historial(raw):
    # Historia de Credito puede traer varios modelos. No se elige uno como principal.
    datos = _como_dict(raw)
    scores = _extraer_scores_hdc(datos)
    return scores[0]['score_value'] if scores else None


def detectar_mora_severa_desde_vector(vector):
    if vector is None:
        return None

    if isinstance(vector, Mapping):
        if 'comportamiento' in vector:
            return detectar_mora_severa_desde_vector(vector.get('comportamiento'))
        return _any_true(detectar_mora_severa_desde_vector(valor) for valor in vector.values())
    if isinstance(vector, str):
        iterable = list(vector)
    elif isinstance(vector, (list, tuple, set)):
        iterable = vector
    else:
        iterable = [vector]

    hay_dato = False
    for codigo in iterable:
        if isinstance(codigo, Mapping):
            resultado = detectar_mora_severa_desde_vector(codigo)
            if resultado is True:
                return True
            if resultado is False:
                hay_dato = True
            continue
        codigo_normalizado = str(codigo).strip().upper()
        if not codigo_normalizado:
            continue
        hay_dato = True
        if codigo_normalizado in CODIGOS_MORA_SEVERA:
            return True
        if codigo_normalizado.isdigit() and int(codigo_normalizado) >= 3:
            return True
    return False if hay_dato else None


def mapear_comportamiento_pago(codigo):
    return COMPORTAMIENTO_PAGO.get(str(codigo).strip().upper(), 'no_disponible')


def _como_dict(raw):
    if raw is None:
        return {}
    if hasattr(raw, 'raw_sanitizado'):
        return raw.raw_sanitizado or {}
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _path(datos, *claves):
    actual = datos
    for clave in claves:
        if not isinstance(actual, Mapping):
            return None
        actual = actual.get(clave)
    return actual


def _codigos_midecisor(info_transaccion):
    codigos = {}
    items = _path(info_transaccion, 'codigosRespuesta')
    if not isinstance(items, list):
        return {'_invalido': True}
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get('clave'), str):
            codigos['_invalido'] = True
            continue
        clave = item['clave'].strip().upper()
        if clave not in {'HC', 'TX'}:
            continue
        valor = _normalizar_codigo_midecisor(item.get('valor'))
        if valor is None or (clave in codigos and codigos[clave] != valor):
            codigos['_invalido'] = True
        elif clave not in codigos:
            codigos[clave] = valor
    return codigos


def _normalizar_codigo_midecisor(valor):
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        return None
    texto = str(valor).strip()
    return texto.zfill(2) if re.fullmatch(r'[0-9]{1,2}', texto) else None


def _midecisor_contiene_informacion(respuesta):
    def contiene(valor):
        if isinstance(valor, Mapping):
            for clave, dato in valor.items():
                if clave == 'conInformacion' and dato is not None and _normalizar_bool(dato) is not False:
                    return True
                if clave in {'score', 'puntaje', 'scoreDecisor', 'viabilidad', 'ratingRecaudos'} and _normalizar_ausente(dato) is not None:
                    return True
                if clave in {'saldoActual', 'saldoMora', 'valorCuota', 'creditosVigentes',
                             'creditosCerrados', 'montoSugerido', 'ingreso',
                             'porcentajeDeuda', 'porcentajeCuotaVsIngreso'}:
                    importe = _decimal(dato)
                    if _normalizar_ausente(dato) is not None and (
                        importe is None or not importe.is_finite() or importe != 0
                    ):
                        return True
                if contiene(dato):
                    return True
        elif isinstance(valor, list) and valor:
            return True
        return False

    return isinstance(respuesta, Mapping) and any(
        contiene(valor) for clave, valor in respuesta.items() if clave != 'validacion'
    )


def _modulos_midecisor_validos(respuesta):
    if not isinstance(respuesta, Mapping):
        return False
    for nombre in ('validacion', 'informacionRiesgo', 'comportamientoCrediticio', 'endeudamiento'):
        if respuesta.get(nombre) is not None and not isinstance(respuesta[nombre], Mapping):
            return False
    indicadores = _path(respuesta, 'comportamientoCrediticio', 'indicadoresValores')
    if indicadores is not None and not isinstance(indicadores, Mapping):
        return False
    for modulo, claves in (
        ('informacionRiesgo', ('score', 'montoSugerido')),
        ('endeudamiento', ('ingreso',)),
    ):
        for clave in claves:
            dato = _normalizar_ausente(_path(respuesta, modulo, clave))
            if dato is not None:
                valor = _decimal(dato)
                if valor is None or not valor.is_finite():
                    return False
    for clave in ('saldoActual', 'saldoMora', 'valorCuota', 'creditosVigentes',
                  'creditosCerrados', 'porcentajeDeuda'):
        dato = _normalizar_ausente(_path(indicadores, clave))
        if dato is not None:
            valor = _decimal(dato)
            if valor is None or not valor.is_finite():
                return False
    return True


def _estado_midecisor(*, codigo_hc, codigo_tx, con_informacion, status=None,
                     status_content=None, estructura_valida=True,
                     validacion_presente=True, contiene_informacion=False):
    if not estructura_valida:
        return ESTADO_ERROR_TECNICO
    # Manual MiDecisor, p. 12: TX describes the transaction, not creditworthiness.
    if codigo_tx == '23':
        return ESTADO_ERROR_TEMPORAL
    if codigo_tx in {'20', '21', '22', '24', '25'}:
        return 'SOLICITUD_INVALIDA'
    if codigo_tx in {f'{numero:02d}' for numero in range(9, 17)}:
        return 'CONSULTA_FALLIDA'
    if codigo_tx == '17':
        # The documented scope is company validation; PN needs confirmation.
        return 'ALCANCE_PN_NO_CONFIRMADO'
    if codigo_tx not in {f'{numero:02d}' for numero in range(1, 9)}:
        return ESTADO_ERROR_TECNICO
    status = ' '.join(status.split()).upper() if isinstance(status, str) else ''
    status_content = ' '.join(status_content.split()).upper() if isinstance(status_content, str) else ''
    if status != 'ACCEPTED' or status_content != '202 ACCEPTED':
        return 'RESPUESTA_INCONSISTENTE'
    if not validacion_presente or con_informacion is None:
        return ESTADO_ERROR_TECNICO
    if codigo_hc == '13':
        if con_informacion is True:
            return ESTADO_EXITOSA_CON_INFORMACION
        if not contiene_informacion:
            return ESTADO_EXITOSA_SIN_INFORMACION
        return 'RESPUESTA_INCONSISTENTE'
    return ESTADO_ERROR_TECNICO


def _resumir_alertas_midecisor(alertas):
    if not isinstance(alertas, list) or not alertas:
        return tuple(), False
    tipos = set()
    for alerta in alertas:
        texto = ''
        if isinstance(alerta, Mapping):
            texto = str(alerta.get('alerta') or '')
        else:
            texto = str(alerta or '')
        texto_normalizado = texto.lower()
        if 'coincidencia solo por nombre' in texto_normalizado:
            tipos.add('coincidencia_solo_nombre')
        elif texto_normalizado:
            tipos.add('alerta_no_clasificada')
    resumen = [f'alertas_midecisor:{len(alertas)}']
    resumen.extend(sorted(tipos))
    return tuple(resumen), bool(alertas)


def _extraer_scores_hdc(datos):
    scores = []
    for item in _iterar_mappings(datos):
        if not isinstance(item, Mapping):
            continue
        score_value = _entero(item.get('scoreValue') or item.get('score_value') or item.get('score'))
        if score_value is None:
            continue
        nombre_modelo = item.get('modelName') or item.get('nombreModelo') or item.get('nombre_modelo') or item.get('name')
        tipo_modelo = item.get('modelType') or item.get('tipoModelo') or item.get('tipo_modelo') or item.get('type')
        scores.append(
            {
                'nombre_modelo': _valor_limpio(nombre_modelo),
                'tipo_modelo': _valor_limpio(tipo_modelo),
                'score_value': score_value,
                'fuente': FUENTE_SCORE_HISTORIA_CREDITO,
            }
        )
    return tuple(scores)


def _estructura_hdc_segura(datos):
    return resumir_estructura_hdc_segura(datos)


def resumir_estructura_hdc_segura(datos):
    report = _path(datos, 'ReportHDCplus') or {}
    models = _lista_segura(_path(report, 'models'))
    obligations = _lista_segura(_path(report, 'obligations'))
    basic_information = _path(report, 'basicInformation')
    payment_behavior_detectado = _buscar_valor(
        datos,
        ('paymentBehavior', 'comportamientoPago', 'payment_behavior', 'vector', 'comportamiento_pago'),
    ) is not None
    conteos = {
        seccion: _contar_seccion_hdc(report, seccion)
        for seccion in (
            'productResult',
            'identifyingAttributes',
            'basicInformation',
            'savings',
            'checkingAccounts',
            'currentAccounts',
            'creditCards',
            'financialSector',
            'realSector',
            'portfolio',
            'obligations',
            'liabilities',
            'location',
            'inquiryFootprints',
            'models',
            'paymentBehavior',
            'globalDebt',
            'globalIndebtedness',
            'accounts',
            'alerts',
            'agregatedInfo',
            'agregatedInfoMicrocredit',
        )
    }
    return {
        'has_ReportHDCplus': isinstance(report, Mapping) and bool(report),
        'top_level_keys': _claves_seguras(datos),
        'report_hdcplus_keys': _claves_seguras(report),
        'conteos': conteos,
        'models_detectados': len(models),
        'obligations_detectadas': len(obligations),
        'basic_information_detectada': basic_information is not None,
        'payment_behavior_detectado': payment_behavior_detectado,
        'campos_mora_detectados': _campos_detectados(
            datos,
            {
                'saldoMora',
                'saldo_mora',
                'saldoEnMora',
                'valorMora',
                'mora',
                'pastDueAmount',
                'past_due_amount',
            },
        ),
    }


def extraer_resumen_hdcplus(datos):
    report = _path(datos, 'ReportHDCplus') or {}
    product_result = _path(report, 'productResult') or {}
    liabilities = _lista_segura(_path(report, 'liabilities'))
    savings = _lista_segura(_path(report, 'savings'))
    global_indebtedness = _lista_segura(_path(report, 'globalIndebtedness'))
    inquiry_footprints = _lista_segura(_path(report, 'inquiryFootprints'))
    alerts = _lista_segura(_path(report, 'alerts'))
    agregated_info = _path(report, 'agregatedInfo')
    agregated_info_microcredit = _path(report, 'agregatedInfoMicrocredit')

    resumen_liabilities = _resumir_liabilities_hdc(
        liabilities, estructura_completa=isinstance(_path(report, 'liabilities'), list),
    )
    resumen_huellas = _resumir_huellas_hdc(inquiry_footprints)
    resumen_global = _resumir_endeudamiento_global_hdc(global_indebtedness)
    resumen_savings = _resumir_savings_hdc(savings)

    return {
        'hdc_disponible': isinstance(report, Mapping) and bool(report),
        'response_code': _valor_limpio(_path(product_result, 'responseCode')),
        'consulta_efectiva': _valor_limpio(_path(product_result, 'responseCode')) == '13',
        'total_savings': resumen_savings['total_savings'],
        'savings_activas': resumen_savings['savings_activas'],
        'savings_cerradas': resumen_savings['savings_cerradas'],
        'total_liabilities': resumen_liabilities['total_liabilities'],
        'liabilities_vigentes': resumen_liabilities['liabilities_vigentes'],
        'liabilities_cerradas': resumen_liabilities['liabilities_cerradas'],
        'liabilities_estado_desconocido': resumen_liabilities['liabilities_estado_desconocido'],
        'carga_mensual_completa': resumen_liabilities['carga_mensual_completa'],
        'obligaciones_incompletas': resumen_liabilities['obligaciones_incompletas'],
        'version_normalizador': VERSION_NORMALIZADOR,
        'liabilities_al_dia': resumen_liabilities['liabilities_al_dia'],
        'liabilities_en_mora': resumen_liabilities['liabilities_en_mora'],
        'liabilities_castigadas': resumen_liabilities['liabilities_castigadas'],
        'saldo_total_hdc': _decimal_a_texto(resumen_liabilities['saldo_total_hdc']),
        'saldo_mora_hdc': _decimal_a_texto(resumen_liabilities['saldo_mora_hdc']),
        'cuota_total_hdc': _decimal_a_texto(resumen_liabilities['cuota_total_hdc']),
        'max_mora_dias': resumen_liabilities['max_mora_dias'],
        'max_cuotas_vencidas': resumen_liabilities['max_cuotas_vencidas'],
        'max_mora_categoria': resumen_liabilities['max_mora_categoria'],
        'huellas_consulta': resumen_huellas['huellas_consulta'],
        'huellas_ultimos_3_meses': resumen_huellas['huellas_ultimos_3_meses'],
        'huellas_ultimos_6_meses': resumen_huellas['huellas_ultimos_6_meses'],
        'alertas_hdc': len(alerts),
        'alertas_codigos': _valores_unicos(alerts, ('alertCode', 'source')),
        'sectores_detectados': sorted(set(resumen_liabilities['sectores_detectados']) | set(resumen_huellas['sectores_detectados']) | set(resumen_global['sectores_detectados'])),
        'tipos_cartera_detectados': sorted(set(resumen_liabilities['tipos_cartera_detectados']) | set(resumen_global['tipos_cartera_detectados'])),
        'roles_deudor_detectados': resumen_liabilities['roles_deudor_detectados'],
        'eventos_pago_detectados': resumen_liabilities['eventos_pago_detectados'],
        'estados_cuenta_detectados': resumen_liabilities['estados_cuenta_detectados'],
        'endeudamiento_global_detectado': bool(global_indebtedness),
        'endeudamiento_global_registros': len(global_indebtedness),
        'capital_global_hdc': _decimal_a_texto(resumen_global['capital_global_hdc']),
        'ultimo_corte_global': resumen_global['ultimo_corte_global'],
        'resumen_agregado_detectado': isinstance(agregated_info, Mapping) and bool(agregated_info),
        'resumen_microcredito_detectado': isinstance(agregated_info_microcredit, Mapping) and bool(agregated_info_microcredit),
        'resumen_agregado_keys': _claves_seguras(agregated_info),
        'resumen_microcredito_keys': _claves_seguras(agregated_info_microcredit),
        'requiere_revision_manual_hdc': True,
    }


def _resumir_liabilities_hdc(liabilities, *, estructura_completa=True):
    total = len(liabilities)
    al_dia = 0
    en_mora = 0
    castigadas = 0
    pago_total = 0
    saldo_total = Decimal('0')
    saldo_mora = Decimal('0')
    cuota_total = Decimal('0')
    max_mora_dias = 0
    max_cuotas_vencidas = 0
    sectores = set()
    tipos = set()
    roles = set()
    eventos = set()
    estados = set()
    cerradas = desconocidas = incompletas = 0
    carga_completa = estructura_completa
    saldos_completos = mora_completa = estructura_completa

    for liability in liabilities:
        if not isinstance(liability, Mapping):
            carga_completa = saldos_completos = mora_completa = False
            incompletas += 1
            desconocidas += 1
            continue
        account = liability.get('account') if isinstance(liability.get('account'), Mapping) else {}
        status = liability.get('status') or {}
        status_account = status.get('account') if isinstance(status, Mapping) else {}
        status_payment = status.get('payment') if isinstance(status, Mapping) else {}
        estado = _valor_limpio(_path(status_account or {}, 'businessAccountStatusDesc'))
        evento = _valor_limpio(_path(status_payment or {}, 'businessBureauEventDesc'))
        estado_norm = _normalizar_texto_hdc(estado)
        evento_norm = _normalizar_texto_hdc(evento)
        codigo_raw = _path(status_account or {}, 'businessAccountStatus')
        codigo_estado = str(codigo_raw).zfill(2) if codigo_raw is not None else ''
        es_cerrada = codigo_estado in HDC_CUENTAS_CERRADAS or (
            not codigo_estado and (estado_norm == 'PAGO TOTAL' or estado_norm.startswith('CANCELADA'))
        )
        es_vigente = codigo_estado in HDC_CUENTAS_VIGENTES or (
            not codigo_estado and estado_norm in HDC_DESCRIPCIONES_VIGENTES
        )
        if es_cerrada:
            cerradas += 1
        elif not es_vigente:
            desconocidas += 1
            carga_completa = False

        if estado:
            estados.add(estado)
        if evento:
            eventos.add(evento)
        _agregar_si_existe(sectores, account.get('economicSectorName'))
        _agregar_si_existe(tipos, account.get('accountTypeDesc') or account.get('subAccountTypeDesc') or account.get('subAccountTypeName'))
        _agregar_si_existe(roles, account.get('stateOfAccountHolderDesc') or account.get('tradeHolderIndicator'))

        valores = _lista_segura(liability.get('values'))
        saldo_obligacion = _sumar_importes_hdc(valores, 'debtBalance')
        mora_obligacion = _sumar_importes_hdc(valores, 'businessValueBalanceOverdue')
        cuota_obligacion = _sumar_importes_hdc(valores, 'valueMonthlyPayment')
        if saldo_obligacion is None:
            saldos_completos = False
        if mora_obligacion is None:
            mora_completa = False
        if not es_cerrada and (not es_vigente or cuota_obligacion is None):
            carga_completa = False
            incompletas += 1
        cuotas_vencidas = max(((_entero(valor.get('installmentsOverdue')) or 0) for valor in valores if isinstance(valor, Mapping)), default=0)
        mora_dias = max(((_entero(valor.get('delinquencyMaturation')) or 0) for valor in valores if isinstance(valor, Mapping)), default=0)
        saldo_total += saldo_obligacion or Decimal('0')
        saldo_mora += mora_obligacion or Decimal('0')
        if es_vigente and not es_cerrada and cuota_obligacion is not None:
            cuota_total += cuota_obligacion
        max_cuotas_vencidas = max(max_cuotas_vencidas, cuotas_vencidas)
        max_mora_dias = max(max_mora_dias, mora_dias)

        es_castigada = 'CASTIG' in estado_norm or 'CASTIG' in evento_norm
        es_pago_total = 'PAGO TOTAL' in estado_norm
        es_mora = (
            'MORA' in estado_norm
            or 'MORA' in evento_norm
            or (mora_obligacion is not None and mora_obligacion > 0)
            or cuotas_vencidas > 0
            or mora_dias > 0
        )
        if es_castigada:
            castigadas += 1
        if es_mora:
            en_mora += 1
        if es_pago_total:
            pago_total += 1
        if not es_castigada and not es_mora and ('AL DIA' in estado_norm or 'AL DIA' in evento_norm):
            al_dia += 1

    return {
        'total_liabilities': total,
        'liabilities_vigentes': total - cerradas - desconocidas,
        'liabilities_cerradas': cerradas,
        'liabilities_estado_desconocido': desconocidas,
        'carga_mensual_completa': carga_completa,
        'obligaciones_incompletas': incompletas,
        'liabilities_al_dia': al_dia,
        'liabilities_en_mora': en_mora,
        'liabilities_castigadas': castigadas,
        'saldo_total_hdc': saldo_total if saldos_completos else None,
        'saldo_mora_hdc': saldo_mora if mora_completa else None,
        'cuota_total_hdc': cuota_total if carga_completa else None,
        'max_mora_dias': max_mora_dias,
        'max_cuotas_vencidas': max_cuotas_vencidas,
        'max_mora_categoria': _categoria_mora(max_mora_dias=max_mora_dias, castigadas=castigadas),
        'sectores_detectados': sorted(sectores),
        'tipos_cartera_detectados': sorted(tipos),
        'roles_deudor_detectados': sorted(roles),
        'eventos_pago_detectados': sorted(eventos),
        'estados_cuenta_detectados': sorted(estados),
    }


def _sumar_importes_hdc(valores, campo):
    if not valores:
        return None
    total = Decimal('0')
    for valor in valores:
        importe = _decimal(valor.get(campo)) if isinstance(valor, Mapping) else None
        if importe is None or not importe.is_finite() or importe < 0:
            return None
        total += importe
    return total


def _resumir_savings_hdc(savings):
    activas = 0
    cerradas = 0
    for item in savings:
        if not isinstance(item, Mapping):
            continue
        estado = _normalizar_texto_hdc(
            _buscar_valor(item, ('businessAccountStatusDesc', 'accountStatusDesc', 'statusDesc', 'status'))
        )
        if 'ACTIV' in estado or 'AL DIA' in estado or 'VIGENT' in estado:
            activas += 1
        if 'CERR' in estado or 'SALD' in estado or 'CANCEL' in estado:
            cerradas += 1
    return {
        'total_savings': len(savings),
        'savings_activas': activas,
        'savings_cerradas': cerradas,
    }


def _resumir_huellas_hdc(huellas):
    fechas = [_parsear_fecha_hdc(huella.get('inquiryDate')) for huella in huellas if isinstance(huella, Mapping)]
    fechas = [fecha for fecha in fechas if fecha is not None]
    referencia = max(fechas) if fechas else date.today()
    sectores = set()
    for huella in huellas:
        if not isinstance(huella, Mapping):
            continue
        _agregar_si_existe(sectores, huella.get('economicSectorName'))
    return {
        'huellas_consulta': len(huellas),
        'huellas_ultimos_3_meses': sum(1 for fecha in fechas if fecha >= referencia - timedelta(days=90)),
        'huellas_ultimos_6_meses': sum(1 for fecha in fechas if fecha >= referencia - timedelta(days=180)),
        'sectores_detectados': sorted(sectores),
    }


def _resumir_endeudamiento_global_hdc(registros):
    capital = Decimal('0')
    sectores = set()
    tipos = set()
    fechas = []
    for registro in registros:
        if not isinstance(registro, Mapping):
            continue
        capital += _decimal(registro.get('capitalValue')) or Decimal('0')
        _agregar_si_existe(sectores, registro.get('sourceGlobalIndebtednessDesc'))
        _agregar_si_existe(tipos, registro.get('typeOfCreditDesc'))
        fecha = _parsear_fecha_hdc(registro.get('cutoffDate'))
        if fecha:
            fechas.append(fecha)
    return {
        'capital_global_hdc': capital,
        'sectores_detectados': sorted(sectores),
        'tipos_cartera_detectados': sorted(tipos),
        'ultimo_corte_global': max(fechas).isoformat() if fechas else None,
    }


def _valores_unicos(items, claves):
    valores = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for clave in claves:
            _agregar_si_existe(valores, item.get(clave))
    return sorted(valores)


def _agregar_si_existe(conjunto, valor):
    valor = _valor_limpio(valor)
    if valor:
        conjunto.add(valor)


def _categoria_mora(*, max_mora_dias, castigadas):
    if castigadas:
        return 'castigada'
    if max_mora_dias >= 120:
        return 'mora_120_o_mas'
    if max_mora_dias >= 90:
        return 'mora_90'
    if max_mora_dias >= 60:
        return 'mora_60'
    if max_mora_dias >= 30:
        return 'mora_30'
    return None


def _parsear_fecha_hdc(valor):
    texto = _valor_limpio(valor)
    if not texto:
        return None
    texto = texto[:10]
    for formato in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _normalizar_texto_hdc(valor):
    texto = _valor_limpio(valor)
    if not texto:
        return ''
    reemplazos = str.maketrans('ÁÉÍÓÚÜÑ', 'AEIOUUN')
    return texto.upper().translate(reemplazos)


def _decimal_a_texto(valor):
    if isinstance(valor, Decimal):
        return str(valor.quantize(Decimal('1'))) if valor == valor.to_integral_value() else str(valor)
    return str(valor) if valor is not None else None


def _claves_seguras(valor):
    if not isinstance(valor, Mapping):
        return []
    return sorted(str(clave) for clave in valor.keys())


def _contar_seccion_hdc(report, seccion):
    if not isinstance(report, Mapping):
        return 0
    valor = report.get(seccion)
    if isinstance(valor, list):
        return len(valor)
    if isinstance(valor, Mapping):
        return len(valor)
    return 1 if valor is not None else 0


def _lista_segura(valor):
    return valor if isinstance(valor, list) else []


def _campos_detectados(datos, nombres):
    encontrados = set()
    nombres_normalizados = {str(nombre).lower() for nombre in nombres}

    def recorrer(valor):
        if isinstance(valor, Mapping):
            for clave, subvalor in valor.items():
                if str(clave).lower() in nombres_normalizados:
                    encontrados.add(str(clave))
                recorrer(subvalor)
        elif isinstance(valor, list):
            for item in valor:
                recorrer(item)

    recorrer(datos)
    return sorted(encontrados)


def _iterar_mappings(valor):
    if isinstance(valor, Mapping):
        yield valor
        for item in valor.values():
            yield from _iterar_mappings(item)
    elif isinstance(valor, list):
        for item in valor:
            yield from _iterar_mappings(item)


def _buscar_valor(datos, claves):
    if not isinstance(datos, Mapping):
        return None
    for clave in claves:
        if clave in datos:
            valor = _normalizar_ausente(datos[clave])
            if valor is not None:
                return valor
    for valor in datos.values():
        if isinstance(valor, Mapping):
            encontrado = _buscar_valor(valor, claves)
            if encontrado is not None:
                return encontrado
        elif isinstance(valor, list):
            for item in valor:
                encontrado = _buscar_valor(item, claves)
                if encontrado is not None:
                    return encontrado
    return None


def _decimal(valor):
    valor = _normalizar_ausente(valor)
    if valor is None:
        return None
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _entero(valor):
    decimal = _decimal(valor)
    if decimal is None:
        return None
    return int(decimal)


def _buscar_bool(datos, claves):
    valor = _buscar_valor(datos, claves)
    return _normalizar_bool(valor)


def _normalizar_bool(valor):
    valor = _normalizar_ausente(valor)
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return None
    texto = str(valor).strip().lower()
    if texto in {'1', 'true', 'si', 'sí', 's', 'yes', 'y'}:
        return True
    if texto in {'0', 'false', 'no', 'n'}:
        return False
    return None


def _valor_limpio(valor):
    valor = _normalizar_ausente(valor)
    return str(valor) if valor is not None else None


def _normalizar_ausente(valor):
    if valor is None:
        return None
    if isinstance(valor, str):
        texto = valor.strip()
        if texto in {'', '-', 'null', 'None'}:
            return None
        if texto == '-1':
            return None
        return texto
    if valor == -1:
        return None
    return valor


def _interpretar_viabilidad(valor):
    if valor is None:
        return None
    texto = str(valor).strip().lower()
    if texto in {'true', 'viable', 'aprobado', 'aprobada', 'si', 'sí', 's', '1', 'alta', 'media', 'baja'}:
        return True
    if texto in {'false', 'no_viable', 'rechazado', 'rechazada', 'no', '0'}:
        return False
    return None


def _normalizar_score(score):
    if score is None:
        return None
    if score < 0 or score > 1000:
        return None
    return int(score)


def _nivel_desde_score(score):
    if score is None:
        return NIVEL_RIESGO_NO_DISPONIBLE
    if score >= 750:
        return NIVEL_RIESGO_BAJO
    if score >= 550:
        return NIVEL_RIESGO_MEDIO
    return NIVEL_RIESGO_ALTO


def _normalizar_nivel_riesgo(valor):
    if valor is None:
        return None
    texto = str(valor).strip().upper()
    if texto in {NIVEL_RIESGO_BAJO, NIVEL_RIESGO_MEDIO, NIVEL_RIESGO_ALTO}:
        return texto
    return None


def _resolver_mora_severa(*, mora_vector, saldo_mora, con_informacion):
    if mora_vector is True:
        return True
    if saldo_mora is not None and saldo_mora > 0:
        return False
    if con_informacion is False:
        return None
    return mora_vector


def _error_tipo_desde_estado(estado):
    if estado == ESTADO_ERROR_CREDENCIAL_SERVICIO:
        return 'error_credencial_servicio'
    if estado in {ESTADO_CONFIGURACION_BLOQUEADA, ESTADO_CONFIGURACION_VENCIDA}:
        return 'configuracion_datacredito'
    if estado == ESTADO_ERROR_TEMPORAL:
        return 'error_temporal_datacredito'
    if estado in {ESTADO_IDENTIFICACION_NO_ENCONTRADA, ESTADO_APELLIDO_NO_COINCIDE, ESTADO_EXITOSA_SIN_INFORMACION}:
        return None
    return 'error_tecnico_datacredito'


def _any_true(iterable):
    hay_false = False
    for item in iterable:
        if item is True:
            return True
        if item is False:
            hay_false = True
    return False if hay_false else None

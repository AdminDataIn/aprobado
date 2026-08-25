from dataclasses import replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from contractors.models import ContractorApplication, ContractorApplicationDocument
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.capacidad_contractual import simular_credito_prestador_informativo
from contractors.services.endeudamiento import calcular_carga_financiera_prestador
from contractors.services.ingreso_contractual import calcular_ingreso_contractual_mensual
from contractors.services.validacion_contractual import validar_contrato_prestador
from contractors.score.dto import ComponenteScorePrestador


Q2 = Decimal('0.01')
Q4 = Decimal('0.0001')


def construir_componentes_score(solicitud, politica, datacredito):
    if politica.usa_fuentes_duales:
        return construir_componentes_score_dual(solicitud, politica, datacredito)
    normalizado = datacredito.resultado_normalizado if datacredito else None
    datacredito_componente = componente_datacredito(normalizado, politica)
    capacidad_componente, variables_capacidad = componente_capacidad(
        solicitud, normalizado, politica
    )
    comportamiento_componente = componente_comportamiento(solicitud, politica)
    riesgo_componente, bloqueos_riesgo = componente_riesgo(
        solicitud, normalizado, politica
    )
    referencias_componente = ComponenteScorePrestador(
        nombre='referencias',
        disponible=False,
        score=None,
        peso_configurado=politica.peso_referencias,
        razones=('No existen referencias verificadas en el flujo actual.',),
        fuente='no_disponible',
    )
    geolocalizacion = {
        'disponible': False,
        'score': None,
        'fuente': 'no_disponible',
    }
    return (
        (
            datacredito_componente,
            capacidad_componente,
            comportamiento_componente,
            riesgo_componente,
            referencias_componente,
        ),
        variables_capacidad,
        tuple(bloqueos_riesgo),
        geolocalizacion,
    )


def construir_componentes_score_dual(solicitud, politica, centrales):
    decisor = getattr(centrales, 'decisor', None)
    historial = getattr(centrales, 'historial', None)
    normalizado_decisor = getattr(decisor, 'resultado_normalizado', None)
    normalizado_hdc = getattr(historial, 'resultado_normalizado', None)
    componente_midecisor = componente_datacredito(normalizado_decisor, politica)
    componente_midecisor = replace(
        componente_midecisor,
        nombre='datacredito_score',
        peso_configurado=politica.peso_midecisor,
        fuente='midecisor_normalizado',
        razones=('Score crediticio externo normalizado, fuente MiDecisor.',),
    )
    componente_hdcplus = componente_hdc(normalizado_hdc, politica)
    capacidad_componente, variables_capacidad = componente_capacidad_dual(
        solicitud,
        normalizado_hdc,
        politica,
    )
    riesgo_componente, bloqueos_riesgo = componente_riesgo(
        solicitud,
        None,
        politica,
    )
    comportamiento_componente = componente_comportamiento(solicitud, politica)
    referencias_componente = ComponenteScorePrestador(
        nombre='referencias',
        disponible=False,
        score=None,
        peso_configurado=politica.peso_referencias,
        razones=('No existen referencias verificadas en el flujo actual.',),
        fuente='no_disponible',
    )
    return (
        (
            componente_midecisor,
            componente_hdcplus,
            capacidad_componente,
            comportamiento_componente,
            riesgo_componente,
            referencias_componente,
        ),
        variables_capacidad,
        tuple(bloqueos_riesgo),
        {'disponible': False, 'score': None, 'fuente': 'no_disponible'},
    )


def componente_datacredito(normalizado, politica):
    score = getattr(normalizado, 'score_externo', None) if normalizado else None
    if score is None or not 0 <= int(score) <= 1000:
        return ComponenteScorePrestador(
            nombre='datacredito',
            disponible=False,
            score=None,
            peso_configurado=politica.peso_datacredito,
            razones=('DataCredito no entrego un score externo valido.',),
            fuente='datacredito_normalizado',
        )
    return ComponenteScorePrestador(
        nombre='datacredito',
        disponible=True,
        score=Decimal(int(score)),
        valor_original=int(score),
        peso_configurado=politica.peso_datacredito,
        razones=('Score externo normalizado disponible.',),
        alertas=tuple(getattr(normalizado, 'alertas', ()) or ()),
        fuente=str(getattr(normalizado, 'servicio_fuente', '') or 'datacredito_normalizado'),
    )


def componente_hdc(normalizado, politica):
    peso = (
        politica.peso_hdcplus
        if politica.peso_hdcplus is not None
        else Decimal('0.00000')
    )
    if normalizado is None:
        return ComponenteScorePrestador(
            nombre='hdcplus',
            disponible=False,
            score=None,
            peso_configurado=peso,
            razones=('HDCPlus no entrego informacion normalizada utilizable.',),
            fuente='hdcplus_normalizado',
        )
    return ComponenteScorePrestador(
        nombre='hdcplus',
        disponible=True,
        score=None,
        peso_configurado=peso,
        razones=(
            'HDCPlus es una fuente informativa requerida para capacidad y no genera score.',
        ),
        fuente='hdcplus_normalizado_informativo_v2',
    )


def componente_capacidad(solicitud, normalizado, politica, usar_carga_total=False):
    faltantes = []
    validacion_contractual = validar_contrato_prestador(solicitud)
    meses_restantes = validacion_contractual.meses_financiables
    valor_pendiente = _decimal(solicitud.valor_pendiente_cobrar)
    obligaciones = _decimal(
        getattr(normalizado, 'cuota_mensual_total', None) if normalizado else None
    )
    monto = _decimal(solicitud.monto_solicitado)
    plazo = solicitud.plazo_meses
    if not validacion_contractual.capacidad_automatica:
        faltantes.extend(validacion_contractual.bloqueos)
        faltantes.extend(validacion_contractual.alertas)
    if meses_restantes <= 0:
        faltantes.append('No hay meses vigentes suficientes para derivar el ingreso contractual.')
    if valor_pendiente is None or valor_pendiente <= 0:
        faltantes.append('No hay valor pendiente verificable del contrato.')
    if obligaciones is None:
        faltantes.append('DataCredito no entrego obligaciones mensuales verificables.')
    if monto is None or monto <= 0 or not plazo:
        faltantes.append('Monto o plazo solicitado no disponible.')

    variables = {
        'meses_restantes_contrato': meses_restantes,
        'ingreso_contractual_estimado': None,
        'obligaciones_mensuales': obligaciones,
        'ingreso_disponible': None,
        'cuota_solicitada': None,
        'relacion_cuota_ingreso': None,
        'cuota_ingreso_maxima': politica.cuota_ingreso_maxima,
        'capacidad_monto_teorica': None,
    }
    if faltantes:
        return ComponenteScorePrestador(
            nombre='capacidad',
            disponible=False,
            score=None,
            peso_configurado=politica.peso_capacidad,
            razones=tuple(faltantes),
            fuente='capacidad_contractual_verificable_v1',
        ), variables

    ingreso = (valor_pendiente / Decimal(meses_restantes)).quantize(Q2, rounding=ROUND_HALF_UP)
    simulador = politica.configuracion_financiera
    simulacion = simular_credito_prestador_informativo(
        monto=monto,
        plazo_meses=plazo,
        configuracion=simulador,
    )
    cuota = simulacion.cuota_mensual
    carga = None
    ingreso_disponible = max(Decimal('0'), ingreso - obligaciones)
    if usar_carga_total:
        carga = calcular_carga_financiera_prestador(
            ingreso_contractual=ingreso,
            cuota_existente=obligaciones,
            cuota_nueva=cuota,
        )
        relacion = carga.relacion_cuota_ingreso
    else:
        relacion = None if ingreso_disponible <= 0 else (
            cuota / ingreso_disponible
        ).quantize(Q4, rounding=ROUND_HALF_UP)
    maximo = politica.cuota_ingreso_maxima
    if relacion is None or maximo <= 0:
        score = Decimal('0')
    else:
        score = max(
            Decimal('0'),
            (Decimal('1') - (relacion / maximo)) * Decimal('1000'),
        ).quantize(Q2, rounding=ROUND_HALF_UP)
    cuota_nueva_maxima = (
        carga.cuota_nueva_maxima(maximo)
        if carga is not None else ingreso_disponible * maximo
    )
    capacidad_monto = _valor_presente(
        cuota_nueva_maxima,
        min(int(plazo), meses_restantes, politica.plazo_maximo_politica),
        politica.tasa_mensual_referencia / Decimal('100'),
    )
    variables.update({
        'ingreso_contractual_estimado': ingreso,
        'ingreso_disponible': ingreso_disponible,
        'cuota_solicitada': cuota,
        'cuota_total_con_nueva_solicitud': (
            carga.cuota_total if carga is not None else obligaciones + cuota
        ),
        'cuota_nueva_maxima_politica': cuota_nueva_maxima,
        'relacion_cuota_ingreso': relacion,
        'capacidad_monto_teorica': capacidad_monto,
    })
    alertas = ()
    if relacion is None or relacion > maximo:
        alertas = ('La cuota solicitada supera la relacion maxima cuota/ingreso.',)
    return ComponenteScorePrestador(
        nombre='capacidad',
        disponible=True,
        score=min(Decimal('1000'), score),
        peso_configurado=politica.peso_capacidad,
        razones=('Capacidad calculada con ingreso contractual, obligaciones y cuota solicitada.',),
        alertas=alertas,
        fuente='capacidad_contractual_verificable_v1',
    ), variables


def componente_capacidad_dual(solicitud, normalizado, politica):
    validacion = validar_contrato_prestador(solicitud)
    ingreso = calcular_ingreso_contractual_mensual(
        solicitud,
        tolerancia=politica.tolerancia_ingreso_contractual,
        validacion_contractual=validacion,
    )
    cuota_existente = _decimal(
        getattr(normalizado, 'cuota_mensual_total', None) if normalizado else None
    )
    monto = _decimal(solicitud.monto_solicitado)
    plazo = solicitud.plazo_meses
    faltantes = list(ingreso.bloqueos)
    if cuota_existente is None:
        faltantes.append('HDCPlus no entrego la cuota mensual existente.')
    if monto is None or monto <= 0 or not plazo:
        faltantes.append('Monto o plazo solicitado no disponible.')

    variables = {
        'version_calculo_ingreso': ingreso.version,
        'metodo_ingreso_contractual': ingreso.metodo,
        'meses_restantes_contrato': ingreso.meses_restantes,
        'ingreso_contractual_estimado': ingreso.ingreso_mensual,
        'valor_total_contrato': ingreso.valor_total,
        'saldo_contractual_pendiente': ingreso.saldo_pendiente,
        'valor_mensual_explicito': ingreso.valor_mensual_explicito,
        'obligaciones_mensuales': cuota_existente,
        'otros_compromisos_conocidos': Decimal('0.00'),
        'ingreso_disponible': None,
        'cuota_solicitada': None,
        'cuota_total_con_nueva_solicitud': None,
        'relacion_cuota_ingreso': None,
        'cuota_ingreso_maxima': politica.cuota_ingreso_maxima,
        'capacidad_disponible': None,
        'capacidad_monto_teorica': None,
    }
    if faltantes:
        return ComponenteScorePrestador(
            nombre='capacidad',
            disponible=False,
            score=None,
            peso_configurado=politica.peso_capacidad,
            razones=tuple(dict.fromkeys(faltantes)),
            alertas=ingreso.alertas,
            fuente='capacidad_financiera_prestador_v2',
        ), variables

    simulacion = simular_credito_prestador_informativo(
        monto=monto,
        plazo_meses=plazo,
        configuracion=politica.configuracion_financiera,
    )
    carga = calcular_carga_financiera_prestador(
        ingreso_contractual=ingreso.ingreso_mensual,
        cuota_existente=cuota_existente,
        cuota_nueva=simulacion.cuota_mensual,
    )
    limite = politica.cuota_ingreso_maxima
    capacidad_disponible = carga.capacidad_disponible(limite)
    relacion = carga.relacion_cuota_ingreso
    score = max(
        Decimal('0'),
        (Decimal('1') - (relacion / limite)) * Decimal('1000'),
    ).quantize(Q2, rounding=ROUND_HALF_UP) if limite > 0 else Decimal('0')
    capacidad_monto = _valor_presente(
        capacidad_disponible,
        min(int(plazo), ingreso.meses_restantes, politica.plazo_maximo_politica),
        politica.tasa_mensual_referencia / Decimal('100'),
    )
    variables.update({
        'ingreso_disponible': carga.ingreso_disponible,
        'cuota_solicitada': simulacion.cuota_mensual,
        'cuota_total_con_nueva_solicitud': carga.cuota_total,
        'relacion_cuota_ingreso': relacion,
        'capacidad_disponible': capacidad_disponible,
        'capacidad_monto_teorica': capacidad_monto,
    })
    alertas = list(ingreso.alertas)
    if simulacion.cuota_mensual > capacidad_disponible:
        alertas.append('La cuota nueva supera la capacidad mensual disponible.')
    return ComponenteScorePrestador(
        nombre='capacidad',
        disponible=True,
        score=min(Decimal('1000'), score),
        peso_configurado=politica.peso_capacidad,
        razones=(
            'Capacidad calculada una sola vez con cuota HDC, cuota nueva e ingreso contractual mensual.',
        ),
        alertas=tuple(dict.fromkeys(alertas)),
        fuente='capacidad_financiera_prestador_v2',
    ), variables


def componente_comportamiento(solicitud, politica):
    documentos = list(solicitud.documentos.all())
    tipos = {item.tipo_documento for item in documentos}
    requeridos = set(ContractorApplicationDocument.TipoDocumento.values)
    capturas = {
        item.tipo_documento: str((item.metadata_captura or {}).get('source', '')).lower()
        for item in documentos
    }
    identidad = (solicitud.metadata_analisis_contractual or {}).get('identidad') or {}
    evidencias = {
        'documentos_completos': requeridos.issubset(tipos),
        'analisis_completado': solicitud.estado_analisis_contractual in {
            ContractorApplication.EstadoAnalisisContractual.COMPLETADO,
            ContractorApplication.EstadoAnalisisContractual.CON_ADVERTENCIAS,
        },
        'autorizacion_vigente': obtener_autorizacion_datacredito_vigente(solicitud) is not None,
        'identidad_coincidente': identidad.get('documento_coincide') is True,
        'cedulas_capturadas': all(
            capturas.get(tipo) in {'capture', 'camera'}
            for tipo in (
                ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
                ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
            )
        ),
        'formulario_consistente': all((
            solicitud.numero_documento,
            solicitud.nombres,
            solicitud.apellidos,
            solicitud.celular,
            solicitud.correo,
            solicitud.empresa_id,
        )),
    }
    disponibles = len(evidencias)
    positivos = sum(1 for valor in evidencias.values() if valor)
    score = (Decimal(positivos) / Decimal(disponibles) * Decimal('1000')).quantize(
        Q2, rounding=ROUND_HALF_UP
    )
    faltantes = tuple(
        f'Senal no satisfecha: {nombre}.' for nombre, valor in evidencias.items() if not valor
    )
    return ComponenteScorePrestador(
        nombre='comportamiento',
        disponible=True,
        score=score,
        peso_configurado=politica.peso_comportamiento,
        razones=('Puntaje basado en seis evidencias digitales existentes.',),
        alertas=faltantes,
        fuente='evidencias_digitales_prestador_v1',
    )


def componente_riesgo(solicitud, normalizado, politica):
    metadata = solicitud.metadata_analisis_contractual or {}
    identidad = metadata.get('identidad') or {}
    bloqueos = []
    alertas = []
    evidencias = []

    if identidad.get('documento_coincide') is False:
        bloqueos.append('identidad:documento_contrato_no_coincide')
    elif identidad.get('documento_coincide') is True:
        evidencias.append(True)
    else:
        alertas.append('No fue posible verificar la coincidencia documental.')

    if solicitud.estado_analisis_contractual == ContractorApplication.EstadoAnalisisContractual.BLOQUEADO:
        bloqueos.append('contrato:analisis_contractual_bloqueado')
    elif solicitud.estado_analisis_contractual in {
        ContractorApplication.EstadoAnalisisContractual.COMPLETADO,
        ContractorApplication.EstadoAnalisisContractual.CON_ADVERTENCIAS,
    }:
        evidencias.append(True)

    if not evidencias and not bloqueos:
        return ComponenteScorePrestador(
            nombre='riesgo',
            disponible=False,
            score=None,
            peso_configurado=politica.peso_riesgo,
            razones=('No existen evidencias antifraude suficientes para puntuar.',),
            alertas=tuple(alertas),
            fuente='reglas_riesgo_verificables_v1',
        ), bloqueos
    score = Decimal('0') if bloqueos else (
        Decimal(len(evidencias)) / Decimal('2') * Decimal('1000')
    ).quantize(Q2, rounding=ROUND_HALF_UP)
    return ComponenteScorePrestador(
        nombre='riesgo',
        disponible=True,
        score=min(Decimal('1000'), score),
        peso_configurado=politica.peso_riesgo,
        razones=('Solo se evaluaron identidad y coherencia contractual verificables.',),
        alertas=tuple(alertas),
        fuente='reglas_riesgo_verificables_v1',
    ), bloqueos


def aplicar_pesos(componentes, permite_redistribuir):
    disponibles = [item for item in componentes if item.disponible and item.score is not None]
    peso_disponible = sum((item.peso_configurado for item in disponibles), Decimal('0'))
    resultado = []
    for componente in componentes:
        peso = Decimal('0')
        if componente in disponibles:
            peso = componente.peso_configurado
            if permite_redistribuir and peso_disponible > 0:
                peso = componente.peso_configurado / peso_disponible
        resultado.append(replace(componente, peso_aplicado=peso.quantize(Decimal('0.00001'))))
    return tuple(resultado)


def _combinar_senales_riesgo(decisor, historial):
    if decisor is None:
        return historial
    if historial is None:
        return decisor

    class SenalesRiesgo:
        mora_severa = bool(
            getattr(decisor, 'mora_severa', False)
            or getattr(historial, 'mora_severa', False)
        )
        mora_actual = bool(
            getattr(decisor, 'mora_actual', False)
            or getattr(historial, 'mora_actual', False)
        )
        mora_maxima_dias = max(
            getattr(decisor, 'mora_maxima_dias', None) or 0,
            getattr(historial, 'mora_maxima_dias', None) or 0,
        )

    return SenalesRiesgo()


def _valor_presente(cuota, plazo, tasa):
    if cuota is None or cuota <= 0 or plazo <= 0:
        return Decimal('0')
    if tasa == 0:
        return (cuota * Decimal(plazo)).quantize(Q2, rounding=ROUND_HALF_UP)
    factor = (Decimal('1') + tasa) ** int(plazo)
    valor = cuota * ((factor - Decimal('1')) / (tasa * factor))
    return valor.quantize(Q2, rounding=ROUND_HALF_UP)


def _decimal(valor):
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None

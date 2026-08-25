from decimal import Decimal, ROUND_HALF_UP

from contractors.models import BandaScorePrestador
from contractors.score.componentes import aplicar_pesos, construir_componentes_score
from contractors.score.dto import ResultadoScorePrestador
from contractors.score.politica import buscar_banda
Q2 = Decimal('0.01')


def evaluar_score_prestador(solicitud, politica, datacredito):
    componentes, variables, bloqueos, geolocalizacion = construir_componentes_score(
        solicitud, politica, datacredito
    )
    faltantes_previos = [
        item.nombre for item in componentes if not item.disponible
    ]
    nombres_obligatorios = ['capacidad']
    if politica.usa_fuentes_duales:
        if politica.requiere_midecisor:
            nombres_obligatorios.append('datacredito_score')
        if politica.requiere_hdcplus:
            nombres_obligatorios.append('hdcplus')
    else:
        nombres_obligatorios.append('datacredito')
    obligatorios_faltantes = [
        nombre for nombre in nombres_obligatorios if nombre in faltantes_previos
    ]
    permite_redistribuir = bool(
        politica.permite_redistribuir_pesos_faltantes
        and not obligatorios_faltantes
    )
    componentes = aplicar_pesos(
        componentes,
        permite_redistribuir,
    )
    razones = []
    alertas = []
    faltantes = [item.nombre for item in componentes if not item.disponible]
    if politica.requiere_referencias and 'referencias' in faltantes:
        obligatorios_faltantes.append('referencias')

    puede_calcular = not obligatorios_faltantes and (
        permite_redistribuir or not faltantes
    )
    if obligatorios_faltantes:
        razones.append(
            'Faltan componentes obligatorios: ' + ', '.join(obligatorios_faltantes) + '.'
        )
    elif faltantes and not permite_redistribuir:
        razones.append(
            'La politica no permite redistribuir componentes faltantes: '
            + ', '.join(faltantes) + '.'
        )
    elif faltantes:
        if faltantes == ['referencias']:
            alertas.append(
                'Peso de referencias redistribuido porque no existen referencias verificadas.'
            )
        else:
            alertas.append(
                'Pesos redistribuidos por componentes opcionales no disponibles: '
                + ', '.join(faltantes) + '.'
            )

    score_base = None
    if puede_calcular:
        score_base = sum(
            (
                componente.score * componente.peso_aplicado
                for componente in componentes
                if componente.disponible and componente.score is not None
            ),
            Decimal('0'),
        ).quantize(Q2, rounding=ROUND_HALF_UP)
        score_base = _limitar_score(score_base)

    penalizaciones = ()
    score_final = score_base
    # La geolocalizacion no se usa hasta que exista una senal verificable.
    if geolocalizacion['disponible'] and score_base is not None:
        score_geo = geolocalizacion['score']
        if score_geo is not None and score_geo < politica.umbral_geolocalizacion:
            from contractors.score.dto import PenalizacionScorePrestador

            penalizacion = PenalizacionScorePrestador(
                nombre='geolocalizacion',
                puntos=Decimal(politica.penalizacion_geolocalizacion),
                razon='La senal geografica verificable esta bajo el umbral.',
            )
            penalizaciones = (penalizacion,)
            score_final = _limitar_score(score_base - penalizacion.puntos)

    banda = buscar_banda(politica, score_final)
    incompatibilidades = detectar_incompatibilidades_simulador(solicitud, politica)
    alertas.extend(incompatibilidades)
    for componente in componentes:
        alertas.extend(componente.alertas)

    monto_sugerido = None
    plazo_sugerido = None
    if banda and banda.nombre != BandaScorePrestador.Nombre.REVISION:
        candidatos_monto = [
            Decimal(solicitud.monto_solicitado),
            politica.monto_maximo_politica,
            banda.monto_maximo,
        ]
        if solicitud.valor_pendiente_cobrar is not None:
            candidatos_monto.append(Decimal(solicitud.valor_pendiente_cobrar))
        capacidad_monto = variables.get('capacidad_monto_teorica')
        if capacidad_monto is not None:
            candidatos_monto.append(Decimal(capacidad_monto))
        monto_sugerido = max(Decimal('0'), min(candidatos_monto)).quantize(
            Q2, rounding=ROUND_HALF_UP
        )

        candidatos_plazo = [
            int(solicitud.plazo_meses),
            int(politica.plazo_maximo_politica),
            int(banda.plazo_maximo),
        ]
        meses_restantes = variables.get('meses_restantes_contrato')
        if meses_restantes is not None:
            candidatos_plazo.append(int(meses_restantes))
        plazo_sugerido = max(0, min(candidatos_plazo))

    requiere_revision = bool(
        score_final is None
        or incompatibilidades
        or bloqueos
        or not banda
        or banda.resultado == BandaScorePrestador.Resultado.REQUIERE_REVISION_MANUAL
    )
    return ResultadoScorePrestador(
        score_base=score_base,
        penalizaciones=penalizaciones,
        score_final=score_final,
        banda=banda.nombre if banda else None,
        componentes=componentes,
        variables_calculadas={
            **variables,
            'geolocalizacion_disponible': False,
            'componentes_faltantes': list(faltantes),
            'redistribucion_aplicada': bool(faltantes and permite_redistribuir),
            'motivo_redistribucion': (
                'referencias_no_verificadas'
                if faltantes == ['referencias'] and permite_redistribuir
                else 'componentes_opcionales_no_disponibles'
                if faltantes and permite_redistribuir
                else ''
            ),
        },
        razones=tuple(dict.fromkeys(razones)),
        alertas=tuple(dict.fromkeys(alertas)),
        bloqueos=tuple(dict.fromkeys(bloqueos)),
        version_score=politica.version_score,
        version_politica=politica.version_politica,
        monto_maximo_sugerido=monto_sugerido,
        plazo_maximo_sugerido=plazo_sugerido,
        requiere_revision_manual=requiere_revision,
    )


def detectar_incompatibilidades_simulador(solicitud, politica):
    alertas = []
    configuracion = politica.configuracion_financiera
    if configuracion is None or not configuracion.activo or not configuracion.version:
        return ('La politica no tiene una configuracion financiera activa y versionada.',)
    if not solicitud.version_configuracion_financiera_simulacion:
        alertas.append('La simulacion no conserva una version de configuracion financiera.')
    elif solicitud.version_configuracion_financiera_simulacion != configuracion.version:
        alertas.append('La politica financiera cambio despues de la simulacion.')
    if not solicitud.version_politica_simulacion:
        alertas.append('La simulacion no conserva la version de politica aplicable.')
    elif solicitud.version_politica_simulacion != politica.version_politica:
        alertas.append('La politica de score cambio despues de la simulacion.')
    if (
        solicitud.monto_simulado != solicitud.monto_solicitado
        or solicitud.plazo_simulado_meses != solicitud.plazo_meses
    ):
        alertas.append('El monto o plazo fue editado manualmente despues de la simulacion.')
    if solicitud.tasa_mensual_simulacion != configuracion.tasa_mensual:
        alertas.append('La tasa guardada en la simulacion no coincide con su configuracion historica.')
    if solicitud.monto_maximo_configuracion_simulacion != configuracion.monto_maximo:
        alertas.append('El monto maximo guardado no coincide con su configuracion historica.')
    if solicitud.plazo_maximo_configuracion_simulacion != configuracion.plazo_maximo_meses:
        alertas.append('El plazo maximo guardado no coincide con su configuracion historica.')
    if (
        solicitud.monto_solicitado is not None
        and solicitud.monto_solicitado > configuracion.monto_maximo
    ):
        alertas.append('El monto solicitado supera el maximo de la politica aplicable.')
    if solicitud.plazo_meses and solicitud.plazo_meses > configuracion.plazo_maximo_meses:
        alertas.append('El plazo solicitado supera el maximo de la politica aplicable.')
    return tuple(alertas)


def _limitar_score(valor):
    return max(Decimal('0'), min(Decimal('1000'), Decimal(valor))).quantize(
        Q2, rounding=ROUND_HALF_UP
    )

from dataclasses import dataclass, field
from decimal import Decimal

from contractors.datacredito import EntradaConsultaDatacreditoPrestador, consultar_datacredito_prestador
from contractors.models import ContractorApplication
from contractors.score import EntradaScoreInternoPrestador, evaluar_score_interno_prestador
from contractors.score.configuracion import obtener_configuracion_score_prestadores
from contractors.score.policies import calcular_puntaje_capacidad_contractual
from contractors.selectors import obtener_credito_previo_por_documento_solicitud
from contractors.services.capacidad_contractual import evaluar_capacidad_contractual_contratista
from contractors.services.elegibilidad_conversion import evaluar_elegibilidad_conversion_contratista
from libranza.escenarios_credito import (
    NUEVO_CREDITO,
    es_nuevo_credito,
    es_recogida_cartera,
    es_segundo_credito,
    normalizar_escenario_credito,
)
from risk.services.portfolio_takeover import evaluar_recogida_cartera
from risk.services.second_credit import evaluar_elegibilidad_segundo_credito


ESTADO_PENDIENTE = 'PENDIENTE'
ESTADO_NO_EVALUADO = 'NO_EVALUADO'
ESTADO_APROBADO = 'APROBADO'
ESTADO_RECHAZADO = 'RECHAZADO'
ESTADO_EVALUADO_READ_ONLY = 'EVALUADO_READ_ONLY'
ESTADO_INCOMPLETO = 'INCOMPLETO'

DECISION_PREAPROBADO_READ_ONLY = 'PREAPROBADO_READ_ONLY'
DECISION_REQUIERE_REVISION_MANUAL = 'REQUIERE_REVISION_MANUAL'
DECISION_BLOQUEADO_READ_ONLY = 'BLOQUEADO_READ_ONLY'
DECISION_INCOMPLETO = 'INCOMPLETO'

FUENTE_PREDECISION = 'predecision_prestadores_read_only'

MOTIVO_PREDECISION_ELEGIBLE = 'predecision_elegible'
MOTIVO_SIN_CREDITO_PREVIO = 'sin_credito_previo'
MOTIVO_CREDITO_PREVIO_REQUIERE_ESCENARIO = 'credito_previo_existente_requiere_escenario'
MOTIVO_NO_EXISTE_CREDITO_PREVIO = 'no_existe_credito_previo'
MOTIVO_SEGUNDO_CREDITO_APROBADO = 'riesgo_segundo_credito_aprobado'
MOTIVO_RECOGIDA_CARTERA_APROBADA = 'riesgo_recogida_cartera_aprobada'


@dataclass(frozen=True)
class ResultadoPredecisionPrestador:
    solicitud_id: int | None
    elegible: bool
    razon: str
    decision: str
    razones: tuple[str, ...] = field(default_factory=tuple)
    bloqueos: tuple[str, ...] = field(default_factory=tuple)
    advertencias: tuple[str, ...] = field(default_factory=tuple)
    escenario_credito: str = NUEVO_CREDITO
    documental_status: str = ESTADO_NO_EVALUADO
    capacidad_status: str = ESTADO_NO_EVALUADO
    riesgo_status: str = ESTADO_NO_EVALUADO
    documental: dict = field(default_factory=dict)
    capacidad_contractual: dict = field(default_factory=dict)
    capacidad_resultado: dict = field(default_factory=dict)
    riesgo: dict = field(default_factory=dict)
    score_status: str = ESTADO_PENDIENTE
    score_resultado: dict = field(default_factory=dict)
    datacredito_status: str = ESTADO_PENDIENTE
    datacredito_resultado: dict = field(default_factory=dict)
    segundo_credito_resultado: dict | None = None
    recogida_cartera_resultado: dict | None = None
    monto_maximo_sugerido: Decimal = Decimal('0.00')
    plazo_maximo_sugerido: int = 0
    requiere_revision_manual: bool = False
    fuente: str = FUENTE_PREDECISION
    bloqueantes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def application_id(self):
        return self.solicitud_id

    @property
    def eligible(self):
        return self.elegible

    @property
    def reason(self):
        return self.razon

    @property
    def reasons(self):
        return self.razones

    @property
    def blockers(self):
        return self.bloqueantes

    def como_dict(self):
        return {
            'application_id': self.solicitud_id,
            'eligible': self.elegible,
            'decision': self.decision,
            'reason': self.razon,
            'reasons': list(self.razones),
            'bloqueos': list(self.bloqueos),
            'advertencias': list(self.advertencias),
            'escenario_credito': self.escenario_credito,
            'documental_status': self.documental_status,
            'capacidad_status': self.capacidad_status,
            'riesgo_status': self.riesgo_status,
            'documental': self.documental,
            'capacidad_contractual': self.capacidad_contractual,
            'capacidad_resultado': self.capacidad_resultado,
            'riesgo': self.riesgo,
            'score_status': self.score_status,
            'score_resultado': self.score_resultado,
            'datacredito_status': self.datacredito_status,
            'datacredito_resultado': self.datacredito_resultado,
            'segundo_credito_resultado': self.segundo_credito_resultado,
            'recogida_cartera_resultado': self.recogida_cartera_resultado,
            'monto_maximo_sugerido': str(self.monto_maximo_sugerido),
            'plazo_maximo_sugerido': self.plazo_maximo_sugerido,
            'requiere_revision_manual': self.requiere_revision_manual,
            'fuente': self.fuente,
            'blockers': list(self.bloqueantes),
        }


ResultadoPredecisionContratista = ResultadoPredecisionPrestador


def evaluar_predecision_contratista(solicitud, datacredito_resuelto=None):
    if solicitud is None:
        return _resultado(
            solicitud=None,
            solicitud_id=None,
            razones=['solicitud_requerida'],
            documental={},
            capacidad_contractual={},
            riesgo=_resultado_riesgo_sin_credito_previo(),
        )

    resultado_documental = evaluar_elegibilidad_conversion_contratista(solicitud)
    resultado_capacidad = evaluar_capacidad_contractual_contratista(solicitud)
    resultado_riesgo = _evaluar_riesgo_credito_previo(solicitud)
    resultado_datacredito = _consultar_datacredito_read_only(solicitud, datacredito_resuelto=datacredito_resuelto)

    razones = []
    if not resultado_documental.elegible:
        razones.extend(f'documental:{razon}' for razon in resultado_documental.razones)

    if not resultado_capacidad.elegible:
        razones.extend(f'capacidad_contractual:{razon}' for razon in resultado_capacidad.razones)
    elif resultado_capacidad.requiere_revision_manual:
        razones.append('capacidad_contractual_revision_manual')

    if resultado_riesgo['status'] == ESTADO_RECHAZADO:
        razones.extend(f'riesgo:{razon}' for razon in resultado_riesgo['reasons'])

    if resultado_datacredito.mora_severa:
        razones.append('datacredito:mora_severa')

    return _resultado(
        solicitud=solicitud,
        solicitud_id=solicitud.id,
        razones=razones,
        documental=resultado_documental.como_dict(),
        capacidad_contractual=resultado_capacidad.como_dict(),
        riesgo=resultado_riesgo,
        datacredito=resultado_datacredito.como_dict(),
    )


def _evaluar_riesgo_credito_previo(solicitud):
    escenario_credito = getattr(
        solicitud,
        'escenario_credito',
        NUEVO_CREDITO,
    )
    try:
        escenario_credito = normalizar_escenario_credito(escenario_credito)
    except Exception:
        escenario_credito = str(escenario_credito or '')
    credito_previo = obtener_credito_previo_por_documento_solicitud(solicitud)

    if credito_previo is None:
        if es_nuevo_credito(escenario_credito):
            return _resultado_riesgo_sin_credito_previo(escenario_credito=escenario_credito)

        return _resultado_riesgo_rechazado(
            escenario_credito=escenario_credito,
            razon=MOTIVO_NO_EXISTE_CREDITO_PREVIO,
            credito_previo=None,
            segundo_credito=None,
            recogida_cartera=None,
        )

    if es_nuevo_credito(escenario_credito):
        return _resultado_riesgo_rechazado(
            escenario_credito=escenario_credito,
            razon=MOTIVO_CREDITO_PREVIO_REQUIERE_ESCENARIO,
            credito_previo=credito_previo,
            segundo_credito=None,
            recogida_cartera=None,
        )

    if es_segundo_credito(escenario_credito):
        return _evaluar_riesgo_segundo_credito(solicitud, credito_previo, escenario_credito)

    if es_recogida_cartera(escenario_credito):
        return _evaluar_riesgo_recogida_cartera(solicitud, credito_previo, escenario_credito)

    return _resultado_riesgo_rechazado(
        escenario_credito=escenario_credito,
        razon='escenario_credito_no_valido',
        credito_previo=credito_previo,
        segundo_credito=None,
        recogida_cartera=None,
    )


def _evaluar_riesgo_segundo_credito(solicitud, credito_previo, escenario_credito):
    segundo_credito = evaluar_elegibilidad_segundo_credito(
        cliente_id=credito_previo.usuario_id,
    )

    if not segundo_credito.get('eligible', False):
        razon = segundo_credito.get('reason') or 'segundo_credito_no_elegible'
        return _resultado_riesgo_rechazado(
            escenario_credito=escenario_credito,
            razon=razon,
            credito_previo=credito_previo,
            segundo_credito=segundo_credito,
            recogida_cartera=None,
        )

    return _resultado_riesgo_aprobado(
        escenario_credito=escenario_credito,
        razon=segundo_credito.get('reason') or MOTIVO_SEGUNDO_CREDITO_APROBADO,
        credito_previo=credito_previo,
        segundo_credito=segundo_credito,
        recogida_cartera=None,
    )


def _evaluar_riesgo_recogida_cartera(solicitud, credito_previo, escenario_credito):
    recogida_cartera = evaluar_recogida_cartera(
        cliente_id=credito_previo.usuario_id,
        monto_solicitado=solicitud.requested_amount,
    )

    if not recogida_cartera.get('eligible', False):
        razon = recogida_cartera.get('reason') or 'recogida_cartera_no_elegible'
        return _resultado_riesgo_rechazado(
            escenario_credito=escenario_credito,
            razon=razon,
            credito_previo=credito_previo,
            segundo_credito=None,
            recogida_cartera=recogida_cartera,
        )

    return _resultado_riesgo_aprobado(
        escenario_credito=escenario_credito,
        razon=recogida_cartera.get('reason') or MOTIVO_RECOGIDA_CARTERA_APROBADA,
        credito_previo=credito_previo,
        segundo_credito=None,
        recogida_cartera=recogida_cartera,
    )


def _resultado_riesgo_aprobado(
    *,
    escenario_credito,
    razon,
    credito_previo,
    segundo_credito,
    recogida_cartera,
):
    return {
        'status': ESTADO_APROBADO,
        'eligible': True,
        'reason': razon,
        'reasons': (),
        'escenario_credito': escenario_credito,
        'credito_previo_id': credito_previo.id,
        'cliente_id': credito_previo.usuario_id,
        'segundo_credito': segundo_credito,
        'recogida_cartera': recogida_cartera,
    }


def _resultado_riesgo_rechazado(
    *,
    escenario_credito,
    razon,
    credito_previo,
    segundo_credito,
    recogida_cartera,
):
    return {
        'status': ESTADO_RECHAZADO,
        'eligible': False,
        'reason': razon,
        'reasons': (razon,),
        'escenario_credito': escenario_credito,
        'credito_previo_id': getattr(credito_previo, 'id', None),
        'cliente_id': getattr(credito_previo, 'usuario_id', None),
        'segundo_credito': segundo_credito,
        'recogida_cartera': recogida_cartera,
    }


def _resultado_riesgo_sin_credito_previo(*, escenario_credito=NUEVO_CREDITO):
    return {
        'status': ESTADO_APROBADO,
        'eligible': True,
        'reason': MOTIVO_SIN_CREDITO_PREVIO,
        'reasons': (MOTIVO_SIN_CREDITO_PREVIO,),
        'escenario_credito': escenario_credito,
        'credito_previo_id': None,
        'cliente_id': None,
        'segundo_credito': None,
        'recogida_cartera': None,
    }


def _resultado(*, solicitud, solicitud_id, razones, documental, capacidad_contractual, riesgo, datacredito=None):
    razones = tuple(razones)
    datacredito = datacredito or _resultado_datacredito_no_evaluado()
    decision, elegible, bloqueos, advertencias, requiere_revision_manual = _resolver_decision(
        razones=razones,
        datacredito=datacredito,
    )
    score_status, score_resultado = _evaluar_score_read_only(
        solicitud=solicitud,
        solicitud_id=solicitud_id,
        puede_evaluar_score=not bloqueos and decision != DECISION_INCOMPLETO,
        capacidad_contractual=capacidad_contractual,
        datacredito=datacredito,
    )
    if score_resultado:
        decision, elegible, advertencias, requiere_revision_manual = _resolver_decision_con_score(
            decision=decision,
            elegible=elegible,
            advertencias=advertencias,
            requiere_revision_manual=requiere_revision_manual,
            score_resultado=score_resultado,
            datacredito=datacredito,
        )
    monto_sugerido, plazo_sugerido = _resolver_monto_y_plazo_sugeridos(
        solicitud=solicitud,
        capacidad_contractual=capacidad_contractual,
        score_resultado=score_resultado,
    )
    razon = MOTIVO_PREDECISION_ELEGIBLE if not razones and elegible else (razones[0] if razones else decision)
    escenario_credito = getattr(
        solicitud,
        'escenario_credito',
        NUEVO_CREDITO,
    )
    return ResultadoPredecisionPrestador(
        solicitud_id=solicitud_id,
        elegible=elegible,
        decision=decision,
        razon=razon,
        razones=razones,
        bloqueos=bloqueos,
        advertencias=advertencias,
        escenario_credito=escenario_credito,
        documental_status=_resolver_status_documental(documental),
        capacidad_status=_resolver_status_booleano(capacidad_contractual),
        riesgo_status=riesgo.get('status', ESTADO_NO_EVALUADO),
        documental=documental,
        capacidad_contractual=capacidad_contractual,
        capacidad_resultado=capacidad_contractual,
        riesgo=riesgo,
        score_status=score_status,
        score_resultado=score_resultado,
        datacredito_status=datacredito.get('status', ESTADO_PENDIENTE),
        datacredito_resultado=datacredito,
        segundo_credito_resultado=riesgo.get('segundo_credito'),
        recogida_cartera_resultado=riesgo.get('recogida_cartera'),
        monto_maximo_sugerido=monto_sugerido,
        plazo_maximo_sugerido=plazo_sugerido,
        requiere_revision_manual=requiere_revision_manual,
        bloqueantes=bloqueos,
    )


def _evaluar_score_read_only(*, solicitud, solicitud_id, puede_evaluar_score, capacidad_contractual, datacredito):
    if not puede_evaluar_score or solicitud_id is None:
        return ESTADO_NO_EVALUADO, {}

    configuracion_score = obtener_configuracion_score_prestadores()
    puntaje_capacidad = calcular_puntaje_capacidad_contractual(
        capacidad_contractual,
        configuracion_score,
    )
    componentes = {
        'capacidad': puntaje_capacidad,
        'monto_solicitado': getattr(solicitud, 'requested_amount', None),
        'plazo_solicitado': getattr(solicitud, 'term_months', None),
        'meses_restantes_contrato': capacidad_contractual.get('meses_restantes_contrato'),
        'valor_pendiente_cobrar': capacidad_contractual.get('valor_pendiente_cobrar'),
        'ingreso_neto': datacredito.get('ingreso_estimado'),
        'obligaciones_mensuales': datacredito.get('valor_cuota_total'),
    }
    score_datacredito = datacredito.get('score_normalizado_0_1000')
    if datacredito.get('disponible') and score_datacredito is not None:
        componentes['datacredito'] = score_datacredito

    entrada = EntradaScoreInternoPrestador(
        solicitud_id=solicitud_id,
        componentes=componentes,
        datacredito_status=datacredito.get('status', ESTADO_PENDIENTE),
    )
    resultado = evaluar_score_interno_prestador(entrada, configuracion_score)
    return ESTADO_EVALUADO_READ_ONLY, resultado.como_dict()


def _resolver_decision(*, razones, datacredito):
    if any(razon.startswith('documental:') for razon in razones):
        return DECISION_INCOMPLETO, False, tuple(razones), (), False

    bloqueos = tuple(
        razon
        for razon in razones
        if (
            razon.startswith('capacidad_contractual:')
            or razon.startswith('riesgo:')
            or razon == 'datacredito:mora_severa'
        )
    )
    if bloqueos:
        return DECISION_BLOQUEADO_READ_ONLY, False, bloqueos, (), False

    advertencias = []
    requiere_revision_manual = False
    if 'capacidad_contractual_revision_manual' in razones:
        advertencias.append('capacidad_contractual:requiere_revision_manual')
        requiere_revision_manual = True
    if not datacredito.get('disponible'):
        advertencias.append('datacredito:no_disponible')
        requiere_revision_manual = True
        return DECISION_REQUIERE_REVISION_MANUAL, False, (), tuple(advertencias), True
    if requiere_revision_manual:
        return DECISION_REQUIERE_REVISION_MANUAL, False, (), tuple(advertencias), True

    return DECISION_PREAPROBADO_READ_ONLY, True, (), (), requiere_revision_manual


def _resolver_decision_con_score(
    *,
    decision,
    elegible,
    advertencias,
    requiere_revision_manual,
    score_resultado,
    datacredito,
):
    advertencias = list(advertencias)
    banda = (score_resultado.get('banda') or {}).get('nombre')
    if banda == 'REVISION':
        advertencias.append('score:revision_manual')
        return DECISION_REQUIERE_REVISION_MANUAL, False, tuple(advertencias), True

    if score_resultado.get('requiere_revision_manual'):
        advertencias.append('score:requiere_revision_manual')
        return DECISION_REQUIERE_REVISION_MANUAL, False, tuple(advertencias), True

    if not datacredito.get('disponible'):
        return DECISION_REQUIERE_REVISION_MANUAL, False, tuple(advertencias), True

    return decision, elegible, tuple(advertencias), requiere_revision_manual


def _resolver_monto_y_plazo_sugeridos(*, solicitud, capacidad_contractual, score_resultado):
    if not score_resultado:
        return Decimal('0.00'), 0

    montos = [
        _decimal_seguro(score_resultado.get('monto_maximo_sugerido')),
        _decimal_seguro(capacidad_contractual.get('capacidad_maxima_estimada')),
    ]
    plazos = [
        int(score_resultado.get('plazo_maximo_sugerido') or 0),
        int(capacidad_contractual.get('meses_restantes_contrato') or 0),
    ]
    configuracion = getattr(solicitud, 'configuracion_portal', None) if solicitud is not None else None
    if configuracion is not None:
        montos.append(_decimal_seguro(configuracion.monto_maximo))
        plazos.append(int(configuracion.plazo_maximo_meses or 0))
    elif solicitud is not None and getattr(solicitud, 'product_config', None) is not None:
        montos.append(_decimal_seguro(solicitud.product_config.max_amount))
        plazos.append(int(solicitud.product_config.max_term_months or 0))

    montos_validos = [monto for monto in montos if monto > Decimal('0.00')]
    plazos_validos = [plazo for plazo in plazos if plazo > 0]
    return (
        min(montos_validos) if montos_validos else Decimal('0.00'),
        min(plazos_validos) if plazos_validos else 0,
    )


def _resolver_status_documental(documental):
    if not documental:
        return ESTADO_NO_EVALUADO
    return ESTADO_APROBADO if documental.get('eligible') else ESTADO_INCOMPLETO


def _resolver_status_booleano(resultado):
    if not resultado:
        return ESTADO_NO_EVALUADO
    return ESTADO_APROBADO if resultado.get('eligible') else ESTADO_RECHAZADO


def _decimal_seguro(valor):
    if valor in (None, ''):
        return Decimal('0.00')
    return Decimal(str(valor))


def _consultar_datacredito_read_only(solicitud, datacredito_resuelto=None):
    entrada = EntradaConsultaDatacreditoPrestador(
        solicitud_id=solicitud.id,
        tipo_documento=solicitud.document_type,
        numero_documento=solicitud.document_number,
    )
    if datacredito_resuelto is not None and getattr(datacredito_resuelto, 'tiene_resultados_para_predecision', False):
        return consultar_datacredito_prestador(
            entrada,
            resultado_decisor_resuelto=datacredito_resuelto.resultado_decisor,
            resultado_historial_resuelto=datacredito_resuelto.resultado_historial,
        )
    return consultar_datacredito_prestador(entrada)


def _resultado_datacredito_no_evaluado():
    return {
        'disponible': False,
        'fuente': 'no_configurado',
        'score_externo': None,
        'score_normalizado_0_1000': None,
        'mora_severa': False,
        'mora_actual': False,
        'obligaciones_abiertas': None,
        'obligaciones_en_mora': None,
        'nivel_riesgo': 'NO_DISPONIBLE',
        'alertas': [],
        'requiere_revision_manual': True,
        'error_tipo': 'datacredito_no_evaluado',
        'metadata_segura': {},
        'status': ESTADO_PENDIENTE,
    }

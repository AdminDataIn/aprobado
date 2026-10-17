"""Process-only UAT tooling. Not imported by application views, tasks or gates."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import replace
import logging
import re
from unittest.mock import patch

from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone

from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador
from contractors.models import ContractorApplication, DOCUMENTOS_OBLIGATORIOS_PRESTADOR
from contractors.score.motor import evaluar_score_prestador
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.centrales_riesgo import obtener_evaluacion_centrales_prestador
from contractors.services.datacredito_evaluacion import construir_fingerprint_datacredito
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
from contractors.services.politica_financiera_prestador import preparar_oferta
from contractors.services.predecision import evaluar_predecision_formal_prestador
from integrations.datacredito.midecisor_shadow import diagnosticar_midecisor_pn
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot


SOLICITUD_ID = 9
PROPIETARIO_ID = 306
CONSENTIMIENTO_ID = 7
INGRESO_ID = 5
HDC_ID = '87dbae55-e1b9-41d3-80e6-5687e89d8f8c'
MIDECISOR_ID = 'edf4c084-7f7a-4dbc-bce6-06dcb420a8d5'
_DIAGNOSTICO_ACTIVO = ContextVar('diagnostico_uat_activo', default=False)


class OperacionBloqueada(RuntimeError):
    pass


@contextmanager
def entorno_diagnostico():
    """No writes, no outbound operations, no provider logs; always roll back."""
    contadores = {'red_bloqueada': 0, 'publicaciones_bloqueadas': 0, 'sql_bloqueado': 0}

    def bloquear_red(*args, **kwargs):
        contadores['red_bloqueada'] += 1
        raise OperacionBloqueada('red_prohibida')

    def bloquear_publicacion(*args, **kwargs):
        contadores['publicaciones_bloqueadas'] += 1
        raise OperacionBloqueada('publicacion_prohibida')

    def solo_select(execute, sql, params, many, context):
        savepoint = re.fullmatch(
            r'(?:SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT) "[a-zA-Z0-9_]+"', sql.strip(), re.I,
        )
        if not sql.lstrip().upper().startswith('SELECT ') and not savepoint:
            contadores['sql_bloqueado'] += 1
            raise OperacionBloqueada('escritura_sql_prohibida')
        return execute(sql, params, many, context)

    nivel_anterior = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with ExitStack() as stack:
            for target in ('requests.sessions.Session.request', 'httpx.Client.send',
                           'httpx.AsyncClient.send', 'urllib.request.urlopen',
                           'socket.socket.connect', 'socket.socket.connect_ex',
                           'socket.socket.sendto', 'socket.create_connection'):
                stack.enter_context(patch(target, side_effect=bloquear_red))
            for target in ('celery.app.task.Task.apply_async', 'celery.app.base.Celery.send_task',
                           'django.core.mail.message.EmailMessage.send'):
                stack.enter_context(patch(target, side_effect=bloquear_publicacion))
            stack.enter_context(override_settings(
                DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False,
                PRESTADORES_AUTO_EVALUACION_ENABLED=False,
            ))
            exterior = not connection.in_atomic_block
            with transaction.atomic():
                if exterior and connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION READ ONLY')
                try:
                    with connection.execute_wrapper(solo_select):
                        token = _DIAGNOSTICO_ACTIVO.set(True)
                        try:
                            yield contadores
                        finally:
                            _DIAGNOSTICO_ACTIVO.reset(token)
                finally:
                    transaction.set_rollback(True)
    finally:
        logging.disable(nivel_anterior)


def evaluar_candidato_sintetico(*, solicitud, politica, raw_midecisor, historial):
    """Counterfactual test only. Never attest a synthetic response as personal data."""
    nombre = str(connection.settings_dict['NAME'])
    memoria = connection.vendor == 'sqlite' and (nombre == ':memory:' or nombre.startswith('file:memorydb_'))
    postgres_test = (
        connection.vendor == 'postgresql' and connection.settings_dict.get('HOST') == '127.0.0.1'
        and nombre.startswith('test_') and nombre == (connection.settings_dict.get('TEST') or {}).get('NAME')
    )
    if not (memoria or postgres_test):
        raise OperacionBloqueada('se_requiere_base_sintetica_aislada')
    with entorno_diagnostico() as contadores:
        if not politica.usa_fuentes_duales:
            raise OperacionBloqueada('politica_no_dual')
        diagnostico = diagnosticar_midecisor_pn(raw_midecisor, shadow=True)
        salida = {
            'tipo': 'DIAGNOSTICO_SINTETICO_NO_PERSONAL',
            'semantica_hc14': 'NO_CONFIRMADA',
            'estado_oficial': diagnostico.estado_oficial,
            'candidato_utilizable': diagnostico.candidato is not None,
            'elegibilidad_crediticia_final': False,
            'riesgo': 'NO_EVALUABLE', 'capacidad_calculable': False,
            'oferta': 'NO_EVALUABLE', 'score_completo_calculado': False,
            'llamadas': contadores, 'rollback_obligatorio': True,
        }
        if diagnostico.candidato is None:
            return salida
        if historial.servicio != 'historial' or historial.estado not in {'EXITOSO', 'SIN_INFORMACION'}:
            raise OperacionBloqueada('historial_no_utilizable')
        # A no-information response supplies neither a zero debt nor a complete load.
        if historial.estado == 'SIN_INFORMACION':
            historial = replace(historial, resultado_normalizado=None)
        normalizado = _proyectar_resultado_allowlist(diagnostico.candidato, servicio='decisor')
        salida.update(evaluar_dto_en_memoria(
            solicitud=solicitud, politica=politica, normalizado=normalizado, historial=historial,
        ))
        return salida


def evaluar_dto_en_memoria(*, solicitud, politica, normalizado, historial):
    """Only under the read-only/rollback/network fence; never saves an approval."""
    if not _DIAGNOSTICO_ACTIVO.get() or not connection.in_atomic_block:
        raise OperacionBloqueada('diagnostico_sin_salvaguardas')
    if historial is not None and historial.estado == 'SIN_INFORMACION':
        historial = replace(historial, resultado_normalizado=None)
    candidato = ResultadoConsultaDatacreditoPrestador(
        estado='EXITOSO', servicio='decisor', snapshot_id='CANDIDATO_SOLO_EN_MEMORIA',
        resultado_normalizado=normalizado, requiere_revision_manual=True,
    )

    def fuente_local(s, *, servicio, **kwargs):
        if s.pk != solicitud.pk or servicio not in {'decisor', 'historial'}:
            raise OperacionBloqueada('fuente_fuera_de_contexto')
        return candidato if servicio == 'decisor' else historial

    with patch('contractors.services.centrales_riesgo.obtener_evaluacion_datacredito_prestador',
               side_effect=fuente_local):
        centrales = obtener_evaluacion_centrales_prestador(solicitud, politica=politica)
    score = evaluar_score_prestador(solicitud, politica, centrales)
    predecision = evaluar_predecision_formal_prestador(
        solicitud=solicitud, politica=politica, centrales=centrales,
    )
    corte = timezone.localdate()
    from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable
    ingreso = resolver_ingreso_evaluable(solicitud, politica, corte=corte)
    horizonte = horizonte_para_solicitud(solicitud, politica.configuracion_financiera, corte=corte)
    oferta = None
    if horizonte is not None and horizonte.disponible:
        oferta = preparar_oferta(
            score=score, politica=politica, ingreso_neto=ingreso,
            obligaciones_mensuales=score.variables_calculadas.get('obligaciones_mensuales'),
            monto_solicitado=solicitud.monto_solicitado, plazo_solicitado=solicitud.plazo_meses,
            horizonte=horizonte, configuracion=politica.configuracion_financiera,
            corte=corte, solicitud_id=solicitud.pk,
        )
    return dict(
        riesgo=predecision.resultado, elegibilidad_crediticia_final=False,
        score_completo_calculado=score.score_final is not None,
        componentes_disponibles=[c.nombre for c in score.componentes if c.disponible],
        componentes_faltantes=score.variables_calculadas['componentes_faltantes'],
        capacidad_calculable=any(c.nombre == 'capacidad' and c.disponible for c in score.componentes),
        carga_hdc_disponible=score.variables_calculadas.get('obligaciones_mensuales') is not None,
        ingreso_vigente=ingreso is not None,
        ingreso_fuente=ingreso.fuente if ingreso else 'NO_DETERMINABLE',
        ingreso_estimado_no_habilita_originacion=bool(ingreso and ingreso.fuente != 'MANUAL_VERIFICADA'),
        horizonte_disponible=bool(horizonte and horizonte.disponible),
        oferta=oferta.estado if oferta else 'NO_EVALUABLE',
        proyeccion_condicional_en_memoria=True,
    )


def inspeccionar_uat9():
    """Read-only preflight, never silently supplies a fixture for application #9."""
    try:
        with entorno_diagnostico() as contadores:
            solicitud = ContractorApplication.objects.get(pk=SOLICITUD_ID)
            autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)
            ingreso = obtener_ingreso_neto_vigente(solicitud)
            politica = obtener_politica_score_activa()
            configuracion = obtener_configuracion_datacredito()
            snapshots = ConsultaDatacreditoSnapshot.objects.in_bulk([HDC_ID, MIDECISOR_ID])
            por_id = {str(pk): valor for pk, valor in snapshots.items()}
            controles = {
                'propietario': solicitud.usuario_id == PROPIETARIO_ID,
                'consentimiento': bool(autorizacion and autorizacion.pk == CONSENTIMIENTO_ID
                                       and autorizacion.usuario_id == PROPIETARIO_ID),
                'ingreso_vigente': bool(ingreso and ingreso.registro_id == INGRESO_ID and ingreso.version == 1),
                'politica_dual': bool(politica and politica.usa_fuentes_duales),
                'documentos': set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).issubset(
                    solicitud.documentos.values_list('tipo_documento', flat=True)),
                'simulacion': bool(solicitud.simulada_en and solicitud.monto_solicitado
                                    and solicitud.plazo_meses),
            }
            resumen = {}
            for servicio, pk in (('historial', HDC_ID), ('decisor', MIDECISOR_ID)):
                snapshot = por_id.get(pk)
                controles[servicio + '_origen'] = bool(
                    snapshot and snapshot.servicio == servicio
                    and snapshot.autorizacion_referencia == str(CONSENTIMIENTO_ID)
                    and snapshot.ambiente == configuracion.environment == 'uat'
                )
                controles[servicio + '_fingerprint'] = bool(
                    snapshot and autorizacion and snapshot.fingerprint == construir_fingerprint_datacredito(
                        solicitud=solicitud, servicio=servicio, autorizacion=autorizacion,
                        configuracion=configuracion,
                    )
                )
                resumen[servicio] = {
                    'id': pk, 'presente': snapshot is not None,
                    'estado': snapshot.estado if snapshot else None,
                    'normalizado_presente': bool(snapshot and snapshot.resultado_normalizado),
                    'raw_conservado_por_modelo': False,
                    'hash_respuesta_conservado_por_modelo': False,
                }
            return {
                'solicitud_id': SOLICITUD_ID, 'controles': controles, 'snapshots': resumen,
                'resultado': ('EVIDENCIA_REAL_NO_RECUPERABLE' if all(controles.values())
                              else 'BLOCKED_CONTEXTO_NO_VERIFICADO'),
                'evaluacion_ejecutada': False, 'llamadas': contadores,
                'rollback_obligatorio': True,
            }
    except Exception:
        # No exception message/traceback can reveal identity, storage paths or SQL params.
        return {'solicitud_id': SOLICITUD_ID, 'resultado': 'BLOCKED_CONTEXTO_NO_VERIFICADO',
                'evaluacion_ejecutada': False}

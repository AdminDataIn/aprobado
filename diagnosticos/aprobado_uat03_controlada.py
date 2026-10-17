"""Explicitly authorized UAT-03 only. Official sources + memory-only diagnostics."""
from contextlib import ExitStack
import ast
from functools import lru_cache
import hashlib
import importlib.util
import inspect
import logging
import os
from pathlib import Path
import re
import subprocess
import textwrap
from unittest.mock import patch

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError, connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings

from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from contractors.score.motor import detectar_incompatibilidades_simulador
from contractors.services import datacredito_evaluacion as dc
from contractors.services.horizonte_simulacion import validar_plazo_contractual
from integrations.datacredito.normalizadores import normalizar_midecisor_pn
from integrations.datacredito.midecisor_shadow import diagnosticar_midecisor_pn
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot


PERFILES_SHA = 'ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173'
SHADOW_SHA = 'd5a980b0dad60cb801d54a44770b91765da0dd31866926500c58473429b306d3'
RELEASE_BASE = '4c536c148e946e13aefdc9c0012fdeccdb048123'
RELEASE_REF = 'refs/heads/release/prestadores-prod-2026-08-28'
RELEASE_TRACKING = 'refs/remotes/origin/release/prestadores-prod-2026-08-28'
VERSION_INGRESO = 'prestadores-score-prod-v2-ingreso'
MIGRACION_INGRESO = '0022_configuracionscoreprestador_politica_ingreso_evaluable'
REGLAS_INGRESO = {'version': 'ingreso-evaluable-v1',
                  'descuento_contractual_porcentaje': '25.00',
                  'semantica_midecisor': 'NO_CONFIRMADA'}


def cargar(nombre, ruta, digest):
    if hashlib.sha256(ruta.read_bytes()).hexdigest() != digest:
        raise RuntimeError('helper_no_coincide')
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


helpers = Path(os.environ.get('APROBADO_UAT_HELPERS_DIR', str(Path(__file__).parent)))
perfiles = cargar('_uat03_perfiles', helpers / 'aprobado_uat_perfiles.py', PERFILES_SHA)
diagnostico = cargar('_uat03_memoria', Path(__file__).with_name('uat9_shadow.py'), SHADOW_SHA)
base = perfiles.base
require, emit = base.require, base.emit


class CandidatoEnMemoria:
    def __init__(self):
        self.dto = None
        self.oficial = None
        self.llamadas = 0
        self.cliente = None

    def __repr__(self):
        return '<CandidatoEnMemoria sin representacion de datos>'

    def observar(self, *args, **kwargs):
        require(self.llamadas == 0 and not connection.in_atomic_block, 'observador_fuera_de_fase')
        self.llamadas += 1
        respuesta = self.cliente(*args, **kwargs)
        require(respuesta.status_code in {200, 202}, 'http_producto_no_interpretable')
        oficial = normalizar_midecisor_pn(respuesta.raw_sanitizado)
        self.oficial = oficial.estado
        candidato = oficial if oficial.estado == 'EXITOSA_CON_INFORMACION' else None
        if (oficial.metadata_segura.get('codigo_hc'), oficial.metadata_segura.get('codigo_tx')) == ('14', '05'):
            candidato = diagnosticar_midecisor_pn(respuesta.raw_sanitizado, shadow=True).candidato
        if candidato is not None:
            self.dto = _proyectar_resultado_allowlist(candidato, servicio='decisor')
        # The real adapter still receives its original response and official classification.
        return respuesta

    def limpiar(self):
        self.dto = None
        self.cliente = None
        self.oficial = None


def validar_destino(sid):
    require(sid not in {3, 4, 9}, 'solicitud_historica_prohibida')


def _git(*args):
    try:
        return subprocess.run(['git', *args], capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except (OSError, subprocess.SubprocessError):
        require(False, 'git_no_verificable')


@lru_cache(maxsize=1)
def _release_remoto(checkout, release):
    # Only Git may use the network, before installing the provider/diagnostic guards.
    resultado = _git('ls-remote', '--exit-code', 'origin', RELEASE_REF)
    require(resultado.returncode == 0 and resultado.stdout.strip() == f'{release}\t{RELEASE_REF}',
            'release_remoto_no_coincide')
    return release


def validar_release():
    release = os.environ.get('APROBADO_UAT_RELEASE_SHA', '')
    require(bool(re.fullmatch(r'[0-9a-f]{40}', release)), 'release_sha40_explicito_requerido')
    require(release != RELEASE_BASE, 'release_base_sin_ingreso')
    for referencia in ('HEAD', RELEASE_TRACKING):
        resultado = _git('rev-parse', '--verify', referencia + '^{commit}')
        require(resultado.returncode == 0 and resultado.stdout.strip() == release, 'release_incorrecto')
    require(_git('merge-base', '--is-ancestor', RELEASE_BASE, 'HEAD').returncode == 0,
            'release_no_desciende_de_base')
    require(_git('diff', '--quiet', 'HEAD', '--').returncode == 0, 'codigo_rastreado_modificado')
    return _release_remoto(str(Path.cwd().resolve()), release)


def _exigir_integracion(funcion, destino):
    """Check a real call, including local imports and aliases, not a name in a comment."""
    funcion = inspect.unwrap(funcion)
    require(inspect.isfunction(funcion), 'integracion_ingreso_no_instalada')
    arbol = ast.parse(textwrap.dedent(inspect.getsource(funcion)))
    nombres = {nombre for nombre, valor in funcion.__globals__.items() if valor is destino}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module == destino.__module__:
            nombres.update(alias.asname or alias.name for alias in nodo.names if alias.name == destino.__name__)
    require(any(isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                and nodo.func.id in nombres for nodo in ast.walk(arbol)),
            'integracion_ingreso_ausente_' + funcion.__name__)


def validar_core_ingreso():
    try:
        from django.db.models import JSONField
        from contractors.models import (ConfiguracionScorePrestador, ContractorApplication,
                                        CambioPoliticaScorePrestadorAudit, TimelinePrestador)
        from contractors.services.ingreso_evaluable import (resolver_ingreso_evaluable,
                                                           ingreso_evaluable_vigente)
        from contractors.services.politica_ingreso_publicacion import (obtener_politica_para_solicitud,
                                                                     ingreso_automatico_aplica,
                                                                     politica_habilitada_para_solicitud)
        from contractors.services.predecision import _evaluar_predecision_dual
        from contractors.score.componentes import componente_capacidad_dual
        from contractors.services.oferta_financiera import calcular_oferta_evaluada
        from contractors.services.evaluacion_formal import evaluar_solicitud_prestador, _finalizar_evaluacion
        from contractors.services.evaluacion_versionado import construir_snapshot_entrada_evaluacion
        from contractors.services.politica_financiera_prestador import preparar_oferta, oferta_con_ingreso_vigente

        inspect.signature(resolver_ingreso_evaluable).bind(None, None, decisor=None, corte=None)
        inspect.signature(obtener_politica_para_solicitud).bind(None)
        inspect.signature(ingreso_automatico_aplica).bind(None, None)
        inspect.signature(politica_habilitada_para_solicitud).bind(None, None)
        _exigir_integracion(_evaluar_predecision_dual, politica_habilitada_para_solicitud)
        _exigir_integracion(politica_habilitada_para_solicitud, ingreso_automatico_aplica)
        for funcion in (componente_capacidad_dual, calcular_oferta_evaluada,
                        _finalizar_evaluacion, oferta_con_ingreso_vigente):
            _exigir_integracion(funcion, resolver_ingreso_evaluable)
        _exigir_integracion(preparar_oferta, ingreso_evaluable_vigente)
        for funcion in (evaluar_solicitud_prestador, oferta_con_ingreso_vigente):
            _exigir_integracion(funcion, obtener_politica_para_solicitud)
        for funcion in (resolver_ingreso_evaluable, componente_capacidad_dual,
                        construir_snapshot_entrada_evaluacion):
            _exigir_integracion(funcion, ingreso_automatico_aplica)
        require(isinstance(ConfiguracionScorePrestador._meta.get_field('politica_ingreso_evaluable'), JSONField),
                'modelo_ingreso_no_instalado')
    except base.StopUAT:
        raise
    except Exception:
        require(False, 'core_ingreso_no_instalado_o_incompatible')

    try:
        require(MigrationRecorder.Migration.objects.filter(app='contractors', name=MIGRACION_INGRESO).exists(),
                'migracion_ingreso_no_aplicada')
    except DatabaseError:
        require(False, 'migracion_ingreso_no_verificable')
    necesarios = (
        (ConfiguracionScorePrestador, ('politica_ingreso_evaluable', 'version', 'version_politica', 'activa')),
        (ContractorApplication, ('version_politica_simulacion',)),
        (CambioPoliticaScorePrestadorAudit, ('politica_nueva', 'snapshot_nuevo')),
        (TimelinePrestador, ('solicitud', 'tipo_evento', 'metadata')),
    )
    try:
        with connection.cursor() as cursor:
            for modelo, campos in necesarios:
                columnas = ', '.join(connection.ops.quote_name(modelo._meta.get_field(campo).column)
                                     for campo in campos)
                tabla = connection.ops.quote_name(modelo._meta.db_table)
                # Fixed model/field allowlist; no introspection SQL outside the SELECT-space fence.
                cursor.execute(f'SELECT {columnas} FROM {tabla} LIMIT 0')
    except DatabaseError:
        require(False, 'schema_ingreso_no_instalado')
    return obtener_politica_para_solicitud


def validar_politica_ingreso(solicitud, resolvedor):
    from contractors.models import CambioPoliticaScorePrestadorAudit, TimelinePrestador

    require(solicitud.version_politica_simulacion == VERSION_INGRESO, 'politica_ingreso_no_asociada')
    try:
        politica = resolvedor(solicitud)
    except Exception:
        require(False, 'politica_ingreso_no_resoluble')
    require(politica is not None and politica.version == VERSION_INGRESO
            and politica.version_politica == VERSION_INGRESO and politica.activa is False
            and politica.politica_ingreso_evaluable == REGLAS_INGRESO, 'politica_ingreso_no_publicada_o_incompatible')
    require(CambioPoliticaScorePrestadorAudit.objects.filter(
        politica_nueva=politica, snapshot_nuevo__ingreso_publicado=True).exists(), 'publicacion_ingreso_no_auditada')
    require(TimelinePrestador.objects.filter(solicitud=solicitud,
        tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
        metadata__tipo='ADOPCION_INGRESO', metadata__version_politica=VERSION_INGRESO).exists(),
        'asociacion_ingreso_no_auditada')
    hoy = perfiles.timezone.localdate()
    require(politica.fecha_vigencia_desde and politica.fecha_vigencia_desde <= hoy
            and (politica.fecha_vigencia_hasta is None or hoy <= politica.fecha_vigencia_hasta),
            'politica_ingreso_fuera_de_vigencia')
    return politica


def preflight(sid, propietario_id, actor_id, **kwargs):
    # The historical runner stays immutable. Income is resolved AFTER sources,
    # and is deliberately not an eligibility condition for acquiring them.
    p = perfiles
    validar_destino(sid)
    require(connection.vendor == 'postgresql', 'motor_no_postgresql')
    require(p.Path.cwd().resolve() == p.Path('/root/aprobado_web'), 'checkout_incorrecto')
    expected = (True, True, False) if kwargs.get('temporary') else (False, False, False)
    require(tuple(bool(getattr(settings, n, False)) for n in base.FLAGS) == expected, 'flags_no_coinciden_con_fase')
    validar_release()
    resolvedor_politica = validar_core_ingreso()
    solicitud = p.ContractorApplication.objects.get(pk=sid)
    owner = p.get_user_model().objects.get(pk=propietario_id)
    actor = p.get_user_model().objects.get(pk=actor_id)
    require(solicitud.usuario_id == propietario_id and owner.is_active
            and not hasattr(owner, 'perfil_pagador'), 'propietario_incorrecto_o_no_habilitado')
    require(actor.is_active and actor.is_staff and not hasattr(actor, 'perfil_pagador')
            and actor.has_perm('contractors.can_evaluate_contractor_application'), 'actor_no_autorizado')
    require(solicitud.estado == 'EVALUACION_PENDIENTE'
            and not solicitud.auditorias_predecision.exists() and not solicitud.evaluacion_jobs.exists()
            and not solicitud.aprobaciones_internas.exists() and not solicitud.aprobaciones_pagador.exists(),
            'solicitud_no_nueva_para_esta_uat')
    auth = p.obtener_autorizacion_datacredito_vigente(solicitud)
    require(auth is not None and auth.usuario_id == propietario_id and auth.solicitud_id == sid,
            'consentimiento_no_vigente_o_ajeno')
    require(solicitud.simulada_en and p.solicitud_tiene_documentos_obligatorios(solicitud)
            and solicitud.estado_analisis_contractual == 'COMPLETADO', 'documentos_o_contrato_incompletos')
    politica = validar_politica_ingreso(solicitud, resolvedor_politica)
    financiera = p.obtener_configuracion_simulador_prestador()
    require(politica is not None and financiera is not None and politica.usa_fuentes_duales
            and politica.configuracion_financiera_id == financiera.pk, 'politica_no_vigente')
    require(not detectar_incompatibilidades_simulador(solicitud, politica), 'condiciones_simuladas_no_vigentes')
    validar_plazo_contractual(solicitud, politica.configuracion_financiera, solicitud.plazo_meses)
    cfg = p.obtener_configuracion_datacredito()
    require(str(getattr(settings, 'DATACREDITO_ENVIRONMENT', '')).strip().lower() == 'uat'
            and cfg.environment == 'uat' and cfg.token_url == p.TOKEN_URLS['uat'], 'oauth_no_uat_explicito')
    require(not cfg.proxy_url and 0 < cfg.timeout_seconds <= 60, 'transporte_no_admitido')
    require(cfg.historial_url == p.HISTORIAL_URLS['uat'] and cfg.midecisor_url == p.MIDECISOR_URLS['uat']
            and not cfg.usa_legacy_historial and not cfg.usa_legacy_decisor, 'producto_o_credenciales_no_admitidos')
    servicios = tuple(s for s, aplica in (
        ('historial', politica.requiere_hdcplus or (politica.peso_hdcplus or 0) > 0),
        ('decisor', politica.requiere_midecisor or (politica.peso_midecisor or 0) > 0),
    ) if aplica)
    reporte = p.evaluar_readiness_datacredito()
    require(servicios == ('historial', 'decisor') and all(r['configurado']
            for section in ('GLOBAL', 'CONSENTIMIENTO', 'HDC', 'MIDECISOR')
            for r in reporte['secciones'][section] if r['critico']), 'readiness_incompleto')
    fps = {s: dc.construir_fingerprint_datacredito(solicitud=solicitud, servicio=s,
                                                autorizacion=auth, configuracion=cfg) for s in servicios}
    require(not Snapshot.objects.filter(fingerprint__in=fps.values(), estado='EN_PROCESO').exists(),
            'reserva_existente_incluso_expirada')
    pendientes = kwargs.get('pending_services', servicios)
    require(not Snapshot.objects.filter(fingerprint__in=[fps[s] for s in pendientes]).exists(),
            'contexto_con_antecedentes_no_consultar_ni_forzar')
    ingreso = p.obtener_ingreso_neto_vigente(solicitud)
    emit('PREFLIGHT_PASS', solicitud_id=sid, propietario_id=propietario_id, actor_id=actor_id,
         consentimiento_id=auth.pk, ingreso_estado='DOCUMENTAL_VIGENTE' if ingreso else 'PENDIENTE_RESOLUCION_AUTOMATICA',
         fuentes=list(servicios), aprobacion_habilitada=False,
         procedencia_credenciales='REQUIERE_CONFIRMACION_OPERADOR')
    return solicitud, actor, auth, politica, cfg, fps


def adquirir_y_diagnosticar(sid, propietario_id, actor_id):
    """No outer atomic around sources. Diagnostic starts only after HTTP has ended."""
    from contractors.datacredito.adapter import consultar_midecisor_persona_natural

    validar_destino(sid)
    require(not connection.in_atomic_block and connection.vendor == 'postgresql', 'conexion_no_autocommit')
    s, actor, auth, politica, cfg, fps = preflight(sid, propietario_id, actor_id)
    require(tuple(fps) == ('historial', 'decisor'), 'fuentes_no_esperadas')
    baseline = base.source_rows(fps)
    require(not baseline, 'contexto_con_antecedentes')
    clave = base.context_key(s, actor, auth, politica, cfg, fps)
    presupuesto = base.TransportBudget(cfg, enabled=True)
    holder = CandidatoEnMemoria()
    holder.cliente = consultar_midecisor_persona_natural
    resultados = {}
    ids = []
    lock = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', [1042026, sid])
            lock = bool(cursor.fetchone()[0])
        require(lock, 'uat_concurrente')
        with ExitStack() as stack:
            base.install_guards(stack, presupuesto)
            stack.enter_context(connection.execute_wrapper(base.write_fence('fuentes', set(baseline))))
            stack.enter_context(override_settings(
                DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True,
                PRESTADORES_AUTO_EVALUACION_ENABLED=False,
                CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                    'LOCATION': f'uat03-proceso-{sid}'}},
            ))
            stack.enter_context(patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                                      new=holder.observar))
            try:
                for servicio in fps:
                    pendientes = tuple(v for v in fps if v not in resultados)
                    actual = preflight(sid, propietario_id, actor_id,
                                                temporary=True, pending_services=pendientes)
                    require(base.context_key(*actual) == clave, 'contexto_modificado')
                    presupuesto.current = servicio
                    resultado = dc.obtener_evaluacion_datacredito_prestador(
                        actual[0], servicio=servicio, modo=dc.REUTILIZAR_SI_VIGENTE,
                        solicitado_por=actual[1], vigencia_dias=(politica.vigencia_hdcplus_dias
                            if servicio == 'historial' else politica.vigencia_midecisor_dias),
                    )
                    presupuesto.current = None
                    require(resultado.snapshot_id and not resultado.reutilizado, 'fuente_no_nueva')
                    snap = Snapshot.objects.get(pk=resultado.snapshot_id)
                    require(snap.autorizacion_referencia == str(auth.pk) and snap.fingerprint == fps[servicio]
                            and snap.creado_por_id == actor_id, 'fuente_fuera_de_contexto')
                    require(presupuesto.counts[servicio]['producto'] == 1 and not presupuesto.stopped,
                            'transporte_incierto_no_reintentar')
                    base.print_snapshot(snap, auth)
                    ids.append(str(snap.pk))
                    hc14_controlado = (
                        servicio == 'decisor' and holder.dto is not None
                        and snap.estado == 'ERROR_PERMANENTE' and snap.codigo_funcional == 'HC14_TX05'
                        and snap.codigo_http in {200, 202}
                        and snap.error_codigo == 'respuesta_funcional_indeterminada'
                        and snap.error_tipo == 'RESPUESTA_INVALIDA'
                        and not snap.resultado_normalizado
                    )
                    require(hc14_controlado or snap.estado in {'EXITOSO', 'SIN_INFORMACION'},
                            'error_no_diagnosticable_no_reintentar')
                    if not hc14_controlado:
                        require(bool(dc.evidencia_uso_snapshot(snap, solicitud=actual[0], autorizacion=auth)),
                                'evidencia_origen_no_valida')
                    resultados[servicio] = resultado
            finally:
                # Only this process' locmem OAuth cache, never production Redis.
                backend = caches['default']
                require(isinstance(backend, LocMemCache), 'cache_no_esperada')
                backend.clear()
        require(not connection.in_atomic_block, 'diagnostico_antes_de_cierre_http')
        if holder.dto is None:
            emit('DIAGNOSTICO_NO_EVALUABLE', candidato_disponible=False)
            return {'riesgo': 'NO_EVALUABLE', 'oferta': 'NO_EVALUABLE', 'elegibilidad_crediticia_final': False}
        with diagnostico.entorno_diagnostico() as contadores:
            actual = preflight(sid, propietario_id, actor_id, pending_services=())
            require(base.context_key(*actual) == clave, 'contexto_modificado_antes_diagnostico')
            resumen = diagnostico.evaluar_dto_en_memoria(
                solicitud=actual[0], politica=actual[3], normalizado=holder.dto,
                historial=resultados['historial'],
            )
            emit('DIAGNOSTICO_CANDIDATO_UAT', solicitud_id=sid, snapshots=ids,
                 semantica_hc14='NO_CONFIRMADA', resultado_no_oficial=True,
                 rollback_obligatorio=True, llamadas_diagnostico=contadores, **resumen)
        return resumen
    finally:
        presupuesto.current = None
        holder.limpiar()
        resultados.clear()
        try:
            rows = base.source_rows(fps)
            require(all(rows.get(pk) == row for pk, row in baseline.items()), 'historico_modificado')
            require(all(sum(r['servicio'] == servicio for pk, r in rows.items()
                            if pk not in baseline) <= 1 for servicio in fps), 'snapshots_duplicados')
            for pk in sorted(rows.keys() - baseline.keys()):
                base.print_snapshot(Snapshot.objects.get(pk=pk), auth)
            s.refresh_from_db()
            require(s.estado == s.Estado.EVALUACION_PENDIENTE and not s.auditorias_predecision.exists()
                    and not s.evaluacion_jobs.exists() and not s.aprobaciones_internas.exists()
                    and not s.aprobaciones_pagador.exists(), 'avance_indebido')
            require(not any(bool(getattr(settings, n, False)) for n in base.FLAGS), 'flags_no_restaurados')
            validar_release()
            emit('POSTCONTROL', solicitud_id=sid, intentos=presupuesto.counts, http=presupuesto.http,
                 auditorias=0, jobs=0, gates=0, temporales_liberados=True,
                 borrado_criptografico_memoria=False, flags_restaurados=True)
        finally:
            if lock:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s, %s)', [1042026, sid])


def main():
    modo = os.environ.get('APROBADO_UAT_MODO', '')
    require(modo in {'preflight', 'diagnostico_ingreso', 'fuentes_y_diagnostico'}, 'modo_no_autorizado')
    sid = perfiles.internal_id('APROBADO_UAT_SOLICITUD_ID')
    validar_destino(sid)
    propietario = perfiles.internal_id('APROBADO_UAT_PROPIETARIO_ID')
    actor = perfiles.internal_id('APROBADO_UAT_ACTOR_ID')
    require(connection.vendor == 'postgresql', 'motor_no_postgresql')
    require(perfiles.Path.cwd().resolve() == perfiles.Path('/root/aprobado_web'), 'checkout_incorrecto')
    _release_remoto.cache_clear()
    validar_release()
    nivel = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        presupuesto = base.TransportBudget(perfiles.obtener_configuracion_datacredito(), enabled=False)
        with ExitStack() as stack:
            base.install_guards(stack, presupuesto)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                valores = preflight(sid, propietario, actor)
                if modo == 'diagnostico_ingreso':
                    from contractors.services.ingreso_evaluable import resolver_ingreso_evaluable
                    ingreso = resolver_ingreso_evaluable(valores[0], valores[3])
                    emit('INGRESO_DIAGNOSTICO_SIN_FUENTES', solicitud_id=sid,
                         fuente=ingreso.fuente if ingreso else 'NO_DETERMINABLE',
                         regla=ingreso.evidencia.get('regla') if ingreso else None,
                         version_regla=ingreso.evidencia.get('reglas', {}).get('version') if ingreso else None,
                         valor_determinado=ingreso is not None, aprobacion_habilitada=False,
                         consultas_http=0, escrituras=0)
                transaction.set_rollback(True)
        if modo in {'preflight', 'diagnostico_ingreso'}:
            emit('READ_ONLY_TERMINADO', consultas_http=0, escrituras=0)
            return
        require(os.environ.get('APROBADO_UAT_AUTORIZACION') ==
                f'AUTORIZO_UAT03_FUENTES_Y_DIAGNOSTICO_SOLICITUD_{sid}', 'falta_autorizacion_individual')
        require(os.environ.get('APROBADO_UAT_CREDENCIALES') == 'CONFIRMADAS_POR_OPERADOR_COMO_UAT',
                'procedencia_credenciales_no_acreditada')
        adquirir_y_diagnosticar(sid, propietario, actor)
    finally:
        logging.disable(nivel)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit('BLOCKED_NO_REINTENTAR', control=str(exc) if type(exc) is base.StopUAT else 'error_controlado')
        raise SystemExit(2) from None

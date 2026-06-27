import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand
from integrations.datacredito import decisor_client, historial_client
from integrations.datacredito.dto import EntradaHistorialCredito, EntradaMiDecisor
from integrations.datacredito.exceptions import DatacreditoError
from integrations.datacredito.normalizadores import (
    normalizar_historial_credito,
    normalizar_midecisor_pn,
)
from integrations.datacredito.request_preview import construir_request_sanitizado_datacredito
from integrations.datacredito.snapshots import obtener_o_consultar_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito


SERVICIO_DECISOR = 'decisor'
SERVICIO_HISTORIAL = 'historial'
SERVICIO_AMBOS = 'ambos'


class Command(BaseCommand):
    help = 'Diagnostica conectividad DataCredito UAT sin guardar respuestas crudas.'

    def add_arguments(self, parser):
        parser.add_argument('--tipo-documento')
        parser.add_argument('--numero-documento')
        parser.add_argument('--apellido')
        parser.add_argument(
            '--servicio',
            choices=[SERVICIO_DECISOR, SERVICIO_HISTORIAL, SERVICIO_AMBOS],
            default=SERVICIO_AMBOS,
        )
        parser.add_argument('--confirmar-consumo-real', action='store_true')
        parser.add_argument('--json', action='store_true', dest='salida_json')
        parser.add_argument('--validar-configuracion', action='store_true')
        parser.add_argument('--diagnostico-detallado', action='store_true')
        parser.add_argument('--usar-snapshot', action='store_true')
        parser.add_argument('--forzar-consulta', action='store_true')
        parser.add_argument('--mostrar-request-sanitizado', action='store_true')

    def handle(self, *args, **options):
        if options['validar_configuracion']:
            return self._responder(
                self._validar_configuracion(),
                salida_json=options['salida_json'],
            )

        faltantes_argumentos = [
            nombre for nombre in ('tipo_documento', 'numero_documento', 'apellido')
            if not options.get(nombre)
        ]
        if faltantes_argumentos:
            return self._responder(
                {
                    'ejecutado': False,
                    'error': f"Faltan argumentos: {', '.join(faltantes_argumentos)}.",
                },
                salida_json=options['salida_json'],
            )

        if options['mostrar_request_sanitizado']:
            servicio = options['servicio']
            servicios = [SERVICIO_DECISOR, SERVICIO_HISTORIAL] if servicio == SERVICIO_AMBOS else [servicio]
            return self._responder(
                {
                    'ejecutado': False,
                    'dry_run_request': True,
                    'ambiente': str(getattr(settings, 'DATACREDITO_ENVIRONMENT', 'uat') or 'uat').lower(),
                    'documento_enmascarado': _enmascarar_documento(options['numero_documento']),
                    'resultados': [
                        construir_request_sanitizado_datacredito(
                            servicio=servicio_actual,
                            tipo_documento=options['tipo_documento'],
                            numero_documento=options['numero_documento'],
                            apellido=options['apellido'],
                        )
                        for servicio_actual in servicios
                    ],
                },
                salida_json=options['salida_json'],
            )

        if not options['confirmar_consumo_real']:
            return self._responder(
                {
                    'ejecutado': False,
                    'error': 'Consumo real no ejecutado. Use --confirmar-consumo-real para confirmar.',
                },
                salida_json=options['salida_json'],
            )

        ambiente = str(getattr(settings, 'DATACREDITO_ENVIRONMENT', 'uat') or 'uat').lower()
        if ambiente in {'prod', 'production', 'produccion'}:
            return self._responder(
                {
                    'ejecutado': False,
                    'error': 'Este comando solo esta permitido en UAT.',
                },
                salida_json=options['salida_json'],
            )

        if not getattr(settings, 'DATACREDITO_REAL_ENABLED', False):
            return self._responder(
                {
                    'ejecutado': False,
                    'error': 'DataCredito real no esta habilitado. Configure DATACREDITO_REAL_ENABLED=True.',
                },
                salida_json=options['salida_json'],
            )

        resultados = []
        servicio = options['servicio']
        if servicio in {SERVICIO_DECISOR, SERVICIO_AMBOS}:
            resultados.append(self._consultar_decisor(options, diagnostico_detallado=options['diagnostico_detallado']))
        if servicio in {SERVICIO_HISTORIAL, SERVICIO_AMBOS}:
            resultados.append(self._consultar_historial(options, diagnostico_detallado=options['diagnostico_detallado']))

        return self._responder(
            {
                'ejecutado': True,
                'ambiente': 'uat',
                'documento_enmascarado': _enmascarar_documento(options['numero_documento']),
                'resultados': resultados,
            },
            salida_json=options['salida_json'],
        )

    def _validar_configuracion(self):
        configuracion = obtener_configuracion_datacredito()
        faltantes_decisor = configuracion.credenciales_decisor.validar_para_token()
        faltantes_historial = (
            configuracion.credenciales_historial.validar_para_token()
            + configuracion.credenciales_servicio_historial.validar_para_historial()
        )
        return {
            'ejecutado': False,
            'validacion_configuracion': True,
            'ambiente': configuracion.environment,
            'real_enabled': configuracion.real_enabled,
            'decisor': {
                'completo': not faltantes_decisor,
                'faltantes': faltantes_decisor,
                'endpoint': configuracion.midecisor_url,
                'usa_legacy': configuracion.usa_legacy_decisor,
            },
            'historial': {
                'completo': not faltantes_historial,
                'faltantes': faltantes_historial,
                'endpoint': configuracion.historial_url,
                'product_id_configurado': bool(configuracion.credenciales_servicio_historial.product_id),
                'info_account_type_configurado': bool(configuracion.credenciales_servicio_historial.info_account_type),
                'server_ip_configurada': bool(configuracion.credenciales_servicio_historial.server_ip_address),
                'parameters_env_var': 'DATACREDITO_HDC_PARAMETERS_JSON',
                'parameters_env_presente': configuracion.parametros_historial_configurados,
                'parameters_longitud': configuracion.parametros_historial_longitud,
                'parameters_json_parseado': (
                    configuracion.parametros_historial_configurados
                    and not bool(configuracion.parametros_historial_error)
                ),
                'parameters_cantidad': len(configuracion.parametros_historial),
                'parameters_configurados': configuracion.parametros_historial_configurados,
                'parameters_validos': not bool(configuracion.parametros_historial_error),
                'parameters_error': configuracion.parametros_historial_error,
                'usa_legacy': configuracion.usa_legacy_historial,
            },
        }

    def _consultar_decisor(self, options, *, diagnostico_detallado=False):
        if options.get('usar_snapshot'):
            return self._consultar_con_snapshot(SERVICIO_DECISOR, options, diagnostico_detallado=diagnostico_detallado)
        entrada = EntradaMiDecisor(
            tipo_identificacion=options['tipo_documento'],
            numero_identificacion=options['numero_documento'],
            apellido_razon_social=options['apellido'],
        )
        try:
            raw = decisor_client.consultar_midecisor_persona_natural(entrada)
        except DatacreditoError as exc:
            return _resumen_error('decisor', exc, etapa_default=_etapa_desde_excepcion(exc))

        try:
            normalizado = normalizar_midecisor_pn(raw)
        except Exception as exc:  # noqa: BLE001 - salida diagnostica controlada y sanitizada.
            return _resumen_error(
                'decisor',
                exc,
                etapa_default='NORMALIZACION',
                http_status=getattr(raw, 'status_code', None),
                codigo_funcional=getattr(raw, 'codigo_funcional', None) or getattr(raw, 'response_code', None),
                error_tipo='ERROR_NORMALIZACION',
            )

        return _resumen_normalizado(
            'decisor',
            normalizado,
            http_status=getattr(raw, 'status_code', None),
            codigo_funcional=getattr(raw, 'codigo_funcional', None),
            proveedor_respondio=True,
            consulta_procesada=True,
            diagnostico_detallado=diagnostico_detallado,
        )

    def _consultar_historial(self, options, *, diagnostico_detallado=False):
        if options.get('usar_snapshot'):
            return self._consultar_con_snapshot(SERVICIO_HISTORIAL, options, diagnostico_detallado=diagnostico_detallado)
        configuracion = obtener_configuracion_datacredito()
        entrada = EntradaHistorialCredito(
            tipo_identificacion=options['tipo_documento'],
            numero_identificacion=options['numero_documento'],
            apellido=options['apellido'],
            request_uuid=str(uuid.uuid4()),
            parametros=configuracion.parametros_historial,
        )
        try:
            raw = historial_client.consultar_historial_credito(entrada)
        except DatacreditoError as exc:
            return _resumen_error('historial', exc, etapa_default=_etapa_desde_excepcion(exc))

        try:
            normalizado = normalizar_historial_credito(raw)
        except Exception as exc:  # noqa: BLE001 - salida diagnostica controlada y sanitizada.
            return _resumen_error(
                'historial',
                exc,
                etapa_default='NORMALIZACION',
                http_status=getattr(raw, 'status_code', None),
                codigo_funcional=getattr(raw, 'response_code', None),
                error_tipo='ERROR_NORMALIZACION',
            )

        return _resumen_normalizado(
            'historial',
            normalizado,
            http_status=getattr(raw, 'status_code', None),
            codigo_funcional=getattr(raw, 'response_code', None),
            proveedor_respondio=True,
            consulta_procesada=True,
            diagnostico_detallado=diagnostico_detallado,
        )

    def _consultar_con_snapshot(self, servicio, options, *, diagnostico_detallado=False):
        try:
            resultado = obtener_o_consultar_datacredito(
                servicio=servicio,
                tipo_documento=options['tipo_documento'],
                numero_documento=options['numero_documento'],
                apellido=options['apellido'],
                forzar_consulta=options.get('forzar_consulta', False),
            )
        except DatacreditoError as exc:
            return _resumen_error(servicio, exc, etapa_default=_etapa_desde_excepcion(exc))

        snapshot = resultado.snapshot
        resumen = _resumen_normalizado(
            servicio,
            resultado.resultado_normalizado,
            http_status=snapshot.http_status if snapshot else None,
            codigo_funcional=snapshot.codigo_funcional if snapshot else None,
            proveedor_respondio=snapshot.proveedor_respondio if snapshot else None,
            consulta_procesada=snapshot.consulta_procesada if snapshot else None,
            diagnostico_detallado=diagnostico_detallado,
        )
        resumen.update(
            {
                'reutilizado': resultado.reutilizado,
                'consultado_proveedor': resultado.consultado_proveedor,
                'snapshot_id': resultado.snapshot_id,
                'consulted_at': snapshot.consulted_at if snapshot else None,
                'vigente_hasta': snapshot.vigente_hasta if snapshot else None,
            }
        )
        return resumen

    def _responder(self, payload, *, salida_json):
        if salida_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return

        if payload.get('validacion_configuracion'):
            self.stdout.write(f"ambiente={payload['ambiente']}")
            self.stdout.write(f"real_enabled={payload['real_enabled']}")
            for servicio in ('decisor', 'historial'):
                datos = payload[servicio]
                self.stdout.write(f"{servicio}.completo={datos['completo']}")
                self.stdout.write(f"{servicio}.endpoint={datos['endpoint']}")
                if servicio == 'historial':
                    self.stdout.write(f"historial.product_id_configurado={datos['product_id_configurado']}")
                    self.stdout.write(f"historial.info_account_type_configurado={datos['info_account_type_configurado']}")
                    self.stdout.write(f"historial.server_ip_configurada={datos['server_ip_configurada']}")
                    self.stdout.write(f"historial.parameters_env_var={datos['parameters_env_var']}")
                    self.stdout.write(f"historial.parameters_env_presente={datos['parameters_env_presente']}")
                    self.stdout.write(f"historial.parameters_longitud={datos['parameters_longitud']}")
                    self.stdout.write(f"historial.parameters_json_parseado={datos['parameters_json_parseado']}")
                    self.stdout.write(f"historial.parameters_cantidad={datos['parameters_cantidad']}")
                    self.stdout.write(f"historial.parameters_configurados={datos['parameters_configurados']}")
                    self.stdout.write(f"historial.parameters_validos={datos['parameters_validos']}")
                    if datos.get('parameters_error'):
                        self.stdout.write(f"historial.parameters_error={datos['parameters_error']}")
                if datos.get('usa_legacy'):
                    self.stdout.write(f"{servicio}.advertencia=usa_credenciales_legacy")
                if datos['faltantes']:
                    self.stdout.write(f"{servicio}.faltantes={','.join(datos['faltantes'])}")
            return

        if not payload.get('ejecutado'):
            if payload.get('dry_run_request'):
                self.stdout.write(f"ambiente={payload['ambiente']}")
                self.stdout.write(f"documento={payload['documento_enmascarado']}")
                for resultado in payload['resultados']:
                    self.stdout.write(f"servicio={resultado['servicio']}")
                    self.stdout.write(f"  token.url={resultado['token_request']['url']}")
                    self.stdout.write(f"  service.url={resultado['service_request']['url']}")
                    self.stdout.write(f"  service.method={resultado['service_request']['method']}")
                    self.stdout.write(f"  service.headers_presentes={resultado['service_request']['headers_presentes']}")
                    self.stdout.write(f"  service.body_keys={resultado['service_request']['body_keys']}")
                return
            self.stdout.write(payload['error'])
            return

        self.stdout.write(f"ambiente={payload['ambiente']}")
        self.stdout.write(f"documento={payload['documento_enmascarado']}")
        for resultado in payload['resultados']:
            self.stdout.write(f"servicio={resultado['servicio']}")
            for clave, valor in resultado.items():
                if clave == 'servicio':
                    continue
                self.stdout.write(f"  {clave}={valor}")


def _resumen_normalizado(
    servicio,
    normalizado,
    *,
    http_status=None,
    codigo_funcional=None,
    proveedor_respondio=None,
    consulta_procesada=None,
    diagnostico_detallado=False,
):
    codigo = codigo_funcional or normalizado.codigo_respuesta or normalizado.response_code
    score_disponible = bool(normalizado.score_midecisor or normalizado.score or normalizado.scores_hdc)
    resumen = {
        'servicio': servicio,
        'http_status': http_status,
        'codigo_funcional': codigo,
        'estado_normalizado': normalizado.estado,
        'proveedor_respondio': proveedor_respondio,
        'consulta_procesada': consulta_procesada,
        'con_informacion': normalizado.con_informacion,
        'utilizable_para_score': score_disponible and normalizado.disponible,
        'score_disponible': score_disponible,
        'mora_disponible': normalizado.mora_severa is not None or normalizado.mora_actual is not None,
        'requiere_revision_manual': normalizado.requiere_revision_manual,
        'requiere_revision_cumplimiento': normalizado.requiere_revision_cumplimiento,
        'disponible': normalizado.disponible,
        'response_code': normalizado.response_code,
        'score': normalizado.score,
        'mora_severa': normalizado.mora_severa,
        'mora_actual': normalizado.mora_actual,
        'saldo_mora': normalizado.saldo_mora,
        'viabilidad': normalizado.viable,
        'rating_recaudos': normalizado.metadata_segura.get('rating_recaudos'),
        'monto_sugerido': normalizado.monto_sugerido,
        'alertas_resumen': list(normalizado.alertas_resumen),
        'hdc_estructura': (normalizado.metadata_segura or {}).get('hdc_estructura') if servicio == SERVICIO_HISTORIAL else None,
        'error': None,
        'error_tipo': None,
        'etapa_error': None,
        'clase_error': None,
    }
    if diagnostico_detallado and servicio == SERVICIO_HISTORIAL:
        hdc_resumen = (normalizado.metadata_segura or {}).get('hdc_resumen') or {}
        resumen.update(
            {
                'hdc_total_liabilities': hdc_resumen.get('total_liabilities'),
                'hdc_liabilities_castigadas': hdc_resumen.get('liabilities_castigadas'),
                'hdc_liabilities_en_mora': hdc_resumen.get('liabilities_en_mora'),
                'hdc_saldo_total_hdc': hdc_resumen.get('saldo_total_hdc'),
                'hdc_saldo_mora_hdc': hdc_resumen.get('saldo_mora_hdc'),
                'hdc_cuota_total_hdc': hdc_resumen.get('cuota_total_hdc'),
                'hdc_max_mora_dias': hdc_resumen.get('max_mora_dias'),
                'hdc_huellas_ultimos_6_meses': hdc_resumen.get('huellas_ultimos_6_meses'),
                'hdc_alertas_hdc': hdc_resumen.get('alertas_hdc'),
                'hdc_sectores_detectados': hdc_resumen.get('sectores_detectados'),
                'hdc_tipos_cartera_detectados': hdc_resumen.get('tipos_cartera_detectados'),
                'hdc_resumen': hdc_resumen,
            }
        )
    return resumen


def _resumen_error(servicio, exc, *, etapa_default=None, http_status=None, codigo_funcional=None, error_tipo=None):
    etapa = getattr(exc, 'etapa', None) or etapa_default
    clase = getattr(exc, 'causa_clase', None) or exc.__class__.__name__
    error_tipo = getattr(exc, 'error_tipo', None) or error_tipo or exc.__class__.__name__
    http_status = getattr(exc, 'http_status', None) or http_status
    codigo_funcional = getattr(exc, 'codigo_funcional', None) or codigo_funcional
    return {
        'servicio': servicio,
        'etapa': etapa,
        'clase_error': clase,
        'error_tipo': error_tipo,
        'http_status': http_status,
        'codigo_funcional': codigo_funcional,
        'estado_normalizado': None,
        'proveedor_respondio': bool(http_status),
        'consulta_procesada': False,
        'disponible': False,
        'response_code': None,
        'score': None,
        'mora_severa': None,
        'mora_actual': None,
        'saldo_mora': None,
        'viabilidad': None,
        'rating_recaudos': None,
        'monto_sugerido': None,
        'alertas_resumen': [],
        'error': exc.__class__.__name__,
        'etapa_error': etapa,
    }


def _etapa_desde_excepcion(exc):
    if getattr(exc, 'etapa', None):
        return exc.etapa
    nombre = exc.__class__.__name__
    if 'Auth' in nombre:
        return 'AUTH'
    if 'Config' in nombre or 'Disabled' in nombre:
        return 'CONFIGURACION'
    if 'Timeout' in nombre or 'Provider' in nombre:
        return 'HTTP'
    return 'ADAPTER'


def _enmascarar_documento(documento):
    texto = ''.join(caracter for caracter in str(documento or '') if caracter.isdigit())
    if not texto:
        return ''
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"

import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.datacredito.dto import (
    ResultadoNormalizadoDatacreditoPrestador,
    ResultadoProveedorDatacreditoPrestador,
)
from contractors.models import ContractorApplication
from contractors.consentimiento_centrales import TEXTO_CONSENTIMIENTO_CENTRALES, VERSION_CONSENTIMIENTO_CENTRALES
from contractors.services.autorizacion_datacredito import (
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.datacredito_evaluacion import (
    ESTADO_AUTORIZACION_REQUERIDA,
    ESTADO_NO_CONFIGURADO,
    FORZAR_CONSULTA,
    REUTILIZAR_SI_VIGENTE,
    SOLO_CACHE,
    _hmac_documento,
    construir_fingerprint_datacredito,
    obtener_evaluacion_datacredito_prestador,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from integrations.datacredito.dto import ResultadoMiDecisorRawSeguro, TokenDatacredito
from integrations.datacredito.exceptions import (
    DatacreditoProviderError,
    DatacreditoTimeoutError,
)
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_midecisor_clasificacion import payload_pn


CONFIGURACION_DATACREDITO_PRUEBA = {
    'DATACREDITO_ENABLED': True,
    'DATACREDITO_REAL_ENABLED': True,
    'DATACREDITO_ENVIRONMENT': 'uat',
    'DATACREDITO_DEFAULT_SERVICE': 'decisor',
    'DATACREDITO_REUSE_DAYS': 30,
    'DATACREDITO_DOCUMENT_HASH_SECRET': 'secreto-hmac-sintetico-de-prueba-32-bytes',
    'DATACREDITO_AUTHORIZATION_TEXT_VERSION': VERSION_CONSENTIMIENTO_CENTRALES,
    'DATACREDITO_AUTHORIZATION_TEXT': TEXTO_CONSENTIMIENTO_CENTRALES,
    'DATACREDITO_DECISOR_CLIENT_ID': 'client-id-prueba',
    'DATACREDITO_DECISOR_CLIENT_SECRET': 'client-secret-prueba',
    'DATACREDITO_DECISOR_TOKEN_USERNAME': 'usuario-token-prueba',
    'DATACREDITO_DECISOR_TOKEN_PASSWORD': 'password-token-prueba',
    'DATACREDITO_HDC_CLIENT_ID': 'hdc-client-id-prueba',
    'DATACREDITO_HDC_CLIENT_SECRET': 'hdc-client-secret-prueba',
    'DATACREDITO_HDC_TOKEN_USERNAME': 'hdc-usuario-prueba',
    'DATACREDITO_HDC_TOKEN_PASSWORD': 'hdc-password-prueba',
    'DATACREDITO_HDC_SERVICE_USER': 'hdc-service-user-prueba',
    'DATACREDITO_HDC_SERVICE_PASSWORD': 'hdc-service-password-prueba',
    'DATACREDITO_HDC_PRODUCT_ID': '64',
    'DATACREDITO_HDC_INFO_ACCOUNT_TYPE': '1',
    'DATACREDITO_HDC_SERVER_IP_ADDRESS': '192.0.2.30',
}


@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA)
class DatacreditoSnapshotV2Test(TestCase):
    def setUp(self):
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
        guard.start()
        self.addCleanup(guard.stop)
        self.usuario = get_user_model().objects.create_user(
            username='prestador-datacredito',
            email='prestador-datacredito@example.com',
            password='test-password',
        )
        self.empresa = Empresa.objects.create(nombre='Empresa DataCrédito', convenio_activo=True)
        self.solicitud = ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento='123456789',
            nombres='Ana',
            apellidos='Pérez Gómez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1',
            cargo='Consultora',
            autoriza_consulta_centrales=True,
        )
        self.autorizacion = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
        )

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_snapshot_vigente_se_reutiliza_sin_duplicar_consulta(self, consultar):
        consultar.return_value = self._resultado_proveedor()
        creditos_antes = Credito.objects.count()
        libranzas_antes = CreditoLibranza.objects.count()

        primero = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )
        segundo = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(primero.estado, ConsultaDatacreditoSnapshot.Estado.EXITOSO)
        self.assertFalse(primero.reutilizado)
        self.assertTrue(segundo.reutilizado)
        self.assertEqual(primero.snapshot_id, segundo.snapshot_id)
        self.assertEqual(consultar.call_count, 1)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 1)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas_antes)

    def test_fingerprint_es_deterministico_y_no_contiene_documento(self):
        configuracion = obtener_configuracion_datacredito()

        primero = construir_fingerprint_datacredito(
            solicitud=self.solicitud,
            servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
            autorizacion=self.autorizacion,
            configuracion=configuracion,
        )
        segundo = construir_fingerprint_datacredito(
            solicitud=self.solicitud,
            servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
            autorizacion=self.autorizacion,
            configuracion=configuracion,
        )

        self.assertEqual(primero, segundo)
        self.assertEqual(len(primero), 64)
        self.assertNotIn(self.solicitud.numero_documento, primero)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_snapshot_vencido_no_se_reutiliza(self, consultar):
        consultar.return_value = self._resultado_proveedor()
        primero = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )
        ConsultaDatacreditoSnapshot.objects.filter(pk=primero.snapshot_id).update(
            vigente_hasta=timezone.now() - timedelta(seconds=1)
        )

        segundo = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertFalse(segundo.reutilizado)
        self.assertNotEqual(primero.snapshot_id, segundo.snapshot_id)
        self.assertEqual(consultar.call_count, 2)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_timeout_es_controlado_y_no_repite_consulta_cobrable(self, consultar):
        consultar.side_effect = DatacreditoTimeoutError('timeout controlado')

        primero = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )
        segundo = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(
            primero.estado,
            ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO,
        )
        self.assertTrue(primero.requiere_revision_manual)
        self.assertFalse(primero.reutilizado)
        self.assertEqual(primero.snapshot_id, segundo.snapshot_id)
        self.assertTrue(segundo.reutilizado)
        self.assertEqual(consultar.call_count, 1)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_error_proveedor_no_aprueba_ni_modifica_solicitud(self, consultar):
        estado_inicial = self.solicitud.estado
        consultar.side_effect = DatacreditoProviderError(
            'proveedor no disponible',
            http_status=503,
            error_tipo='SERVICIO_NO_DISPONIBLE',
        )

        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            resultado.estado,
            ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO,
        )
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIsNone(resultado.resultado_normalizado)
        self.assertEqual(self.solicitud.estado, estado_inicial)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_hdc_4xx_es_error_permanente_y_5xx_es_transitorio(self, consultar):
        for http_status, estado_esperado in (
            (400, ConsultaDatacreditoSnapshot.Estado.ERROR_PERMANENTE),
            (503, ConsultaDatacreditoSnapshot.Estado.ERROR_TRANSITORIO),
        ):
            with self.subTest(http_status=http_status):
                self.solicitud.apellidos = f'Prueba{http_status}'
                consultar.side_effect = DatacreditoProviderError(
                    'error proveedor controlado',
                    http_status=http_status,
                    error_tipo='HTTP_PROVEEDOR',
                )
                resultado = obtener_evaluacion_datacredito_prestador(
                    self.solicitud,
                    solicitado_por=self.usuario,
                    servicio='historial',
                )
                self.assertEqual(resultado.estado, estado_esperado)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_forzar_consulta_exige_staff_permiso_y_justificacion(self, consultar):
        with self.assertRaises(PermissionDenied):
            obtener_evaluacion_datacredito_prestador(
                self.solicitud,
                modo=FORZAR_CONSULTA,
                solicitado_por=self.usuario,
                justificacion='Diagnostico controlado',
            )

        staff = get_user_model().objects.create_superuser(
            username='staff-datacredito',
            email='staff-datacredito@example.com',
            password='test-password',
        )
        with self.assertRaises(ValidationError):
            obtener_evaluacion_datacredito_prestador(
                self.solicitud,
                modo=FORZAR_CONSULTA,
                solicitado_por=staff,
            )
        consultar.assert_not_called()

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_consulta_en_proceso_evitar_segunda_invocacion(self, consultar):
        configuracion = obtener_configuracion_datacredito()
        documento_hash = _hmac_documento(
            self.solicitud.numero_documento,
            configuracion.document_hash_secret,
        )
        fingerprint = construir_fingerprint_datacredito(
            solicitud=self.solicitud,
            servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
            autorizacion=self.autorizacion,
            configuracion=configuracion,
            documento_hash=documento_hash,
        )
        snapshot = ConsultaDatacreditoSnapshot.objects.create(
            ambiente='uat',
            servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
            documento_hash=documento_hash,
            documento_enmascarado='*****6789',
            fingerprint=fingerprint,
            estado=ConsultaDatacreditoSnapshot.Estado.EN_PROCESO,
            consultado_en=timezone.now(),
            vigente_hasta=timezone.now() + timedelta(minutes=5),
            autorizacion_referencia=str(self.autorizacion.pk),
        )

        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(resultado.snapshot_id, str(snapshot.pk))
        self.assertEqual(resultado.estado, ConsultaDatacreditoSnapshot.Estado.EN_PROCESO)
        self.assertTrue(resultado.reutilizado)
        consultar.assert_not_called()
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 1)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    @override_settings(DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False)
    def test_integracion_apagada_no_hace_http(self, consultar):
        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(resultado.estado, ESTADO_NO_CONFIGURADO)
        self.assertEqual(resultado.error_codigo, 'datacredito_deshabilitado')
        consultar.assert_not_called()
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    @override_settings(DATACREDITO_DECISOR_CLIENT_ID='')
    def test_credenciales_incompletas_no_hacen_http(self, consultar):
        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(resultado.estado, ESTADO_NO_CONFIGURADO)
        self.assertEqual(resultado.error_codigo, 'credenciales_datacredito_incompletas')
        consultar.assert_not_called()

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_solo_cache_no_consulta_si_no_hay_snapshot(self, consultar):
        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            modo=SOLO_CACHE,
            solicitado_por=self.usuario,
        )

        self.assertEqual(resultado.estado, 'SIN_CACHE')
        consultar.assert_not_called()

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_ambiente_y_servicio_no_comparten_snapshot(self, consultar):
        consultar.side_effect = lambda solicitud, servicio: self._resultado_proveedor(servicio)
        decisor = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
            servicio='decisor',
        )
        historial = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
            servicio='historial',
        )
        with self.settings(DATACREDITO_ENVIRONMENT='prod'):
            produccion = obtener_evaluacion_datacredito_prestador(
                self.solicitud,
                solicitado_por=self.usuario,
                servicio='decisor',
            )

        self.assertEqual(len({decisor.snapshot_id, historial.snapshot_id, produccion.snapshot_id}), 3)
        self.assertEqual(consultar.call_count, 3)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 3)

    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_sin_autorizacion_vigente_no_consulta(self, consultar):
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales', 'updated_at'])

        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        self.assertEqual(resultado.estado, ESTADO_AUTORIZACION_REQUERIDA)
        consultar.assert_not_called()

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_raw_tokens_y_pii_no_se_persisten(self, cliente):
        cliente.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200,
            response_code='13',
            codigo_funcional='HC13_TX02',
            raw_sanitizado={
                'status': 'ACCEPTED',
                'access_token': 'token-que-no-debe-persistir',
                'nombreCompleto': 'Persona de Prueba',
                'content': {
                    'status': '202 ACCEPTED',
                    'respuesta': {
                        'validacion': {'conInformacion': True},
                        'informacionRiesgo': {'score': 837},
                        'comportamientoCrediticio': {
                            'indicadoresValores': {
                                'saldoActual': 100000,
                                'valorCuota': 20000,
                                'creditosVigentes': 2,
                                'creditosCerrados': 1,
                            }
                        },
                    },
                    'infoTransaccion': {
                        'codigosRespuesta': [
                            {'clave': 'HC', 'valor': '13'}, {'clave': 'TX', 'valor': '02'},
                        ]
                    },
                },
            },
        )

        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
        persistido = json.dumps(snapshot.resultado_normalizado, sort_keys=True)
        self.assertEqual(resultado.resultado_normalizado.score_externo, 837)
        self.assertNotIn('token-que-no-debe-persistir', persistido)
        self.assertNotIn('Persona de Prueba', persistido)
        self.assertNotIn(self.solicitud.numero_documento, persistido)
        self.assertNotIn('headers', persistido.lower())
        self.assertEqual(
            set(snapshot.resultado_normalizado),
            set(ResultadoNormalizadoDatacreditoPrestador.__dataclass_fields__),
        )

    @override_settings(
        DATACREDITO_PROXY_URL='socks5h://usuario:clave@127.0.0.1:1080'
    )
    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_snapshot_no_persiste_configuracion_proxy(self, consultar):
        consultar.return_value = self._resultado_proveedor()

        resultado = obtener_evaluacion_datacredito_prestador(
            self.solicitud,
            solicitado_por=self.usuario,
        )

        snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
        persistido = json.dumps(
            {
                'resultado_normalizado': snapshot.resultado_normalizado,
                'error_codigo': snapshot.error_codigo,
                'error_tipo': snapshot.error_tipo,
                'codigo_funcional': snapshot.codigo_funcional,
            },
            sort_keys=True,
        )
        self.assertNotIn('proxy', persistido.lower())
        self.assertNotIn('socks5h://', persistido)
        self.assertNotIn('usuario:clave', persistido)

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_error_funcional_conserva_diagnostico_sin_raw_y_reutiliza_intento(self, cliente):
        from integrations.datacredito.decisor_client import _codigo_funcional_midecisor

        for tx, estado, codigo, tipo in (
            ('20', 'ERROR_PERMANENTE', 'solicitud_funcional_invalida', 'ERROR_SOLICITUD'),
            ('23', 'ERROR_TRANSITORIO', 'excepcion_interna_proveedor', 'ERROR_PROVEEDOR'),
            ('17', 'ERROR_PERMANENTE', 'tx17_pn_no_confirmado', 'RESPUESTA_INVALIDA'),
            ('09', 'ERROR_PERMANENTE', 'consulta_funcional_fallida', 'RESPUESTA_FUNCIONAL'),
            (None, 'ERROR_PERMANENTE', 'respuesta_funcional_indeterminada', 'RESPUESTA_INVALIDA'),
        ):
            with self.subTest(tx=tx):
                self.solicitud.apellidos = f'Prueba{tx}'
                payload = payload_pn(hc=None, tx=tx, informacion=False)
                payload.update({
                    'documento': 'documento-sintetico-no-persistir',
                    'nombres': 'nombre-sintetico-no-persistir',
                    'access_token': 'token-sintetico-no-persistir',
                    'headers': {'Authorization': 'credencial-sintetica-no-persistir'},
                })
                payload['content']['infoTransaccion']['msjExcepcion'] = 'mensaje-libre-no-persistir'
                cliente.return_value = ResultadoMiDecisorRawSeguro(
                    status_code=202, codigo_funcional=_codigo_funcional_midecisor(payload),
                    raw_sanitizado=payload,
                )
                llamadas = cliente.call_count
                primero = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
                segundo = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
                snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=primero.snapshot_id)
                self.assertEqual(snapshot.estado, estado)
                self.assertEqual(snapshot.codigo_http, 202)
                self.assertEqual(snapshot.codigo_funcional, f'TX{tx}' if tx else '')
                self.assertEqual(snapshot.error_codigo, codigo)
                self.assertEqual(snapshot.error_tipo, tipo)
                self.assertEqual(snapshot.resultado_normalizado, {})
                self.assertIsNone(primero.resultado_normalizado)
                self.assertEqual(primero.snapshot_id, segundo.snapshot_id)
                self.assertTrue(segundo.reutilizado)
                self.assertEqual(cliente.call_count - llamadas, 1)
                persistido = json.dumps({
                    'resultado': snapshot.resultado_normalizado, 'codigo': snapshot.codigo_funcional,
                    'error': snapshot.error_codigo, 'tipo': snapshot.error_tipo,
                })
                for sensible in ('no-persistir', 'Authorization', 'headers', self.solicitud.numero_documento):
                    self.assertNotIn(sensible, persistido)

    @patch('integrations.datacredito.decisor_client.obtener_token_cacheado', return_value=TokenDatacredito('token-sintetico'))
    @patch('integrations.datacredito.decisor_client.invalidar_token')
    @patch('integrations.datacredito.decisor_client.crear_session_datacredito')
    def test_http_error_con_codigo_funcional_conserva_clasificacion_y_no_reintenta(self, crear_session, invalidar, token):
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                self.solicitud.apellidos = f'Prueba{status}'
                session = Mock()
                session.post.return_value.status_code = status
                session.post.return_value.json.return_value = payload_pn(hc=None, tx='20', informacion=False)
                crear_session.return_value = session
                resultado = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
                repetido = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
                snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
                self.assertEqual(snapshot.codigo_http, status)
                self.assertEqual(snapshot.codigo_funcional, 'TX20')
                self.assertEqual(snapshot.estado, 'ERROR_TRANSITORIO' if status in (401, 429, 500, 503) else 'ERROR_PERMANENTE')
                self.assertEqual(snapshot.error_tipo, 'HTTP_ERROR')
                self.assertEqual(snapshot.resultado_normalizado, {})
                self.assertEqual(resultado.snapshot_id, repetido.snapshot_id)
                session.post.assert_called_once()

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_error_nuevo_y_finalizacion_tardia_no_modifican_historicos(self, cliente):
        from contractors.services.datacredito_evaluacion import _finalizar_error

        historico = ConsultaDatacreditoSnapshot.objects.create(
            ambiente='uat', servicio='decisor', documento_hash='a' * 64,
            fingerprint='a' * 64, estado='ERROR_PERMANENTE', codigo_http=None,
            error_codigo='respuesta_funcional_no_exitosa', error_tipo='RESPUESTA_FUNCIONAL',
            consultado_en=timezone.now(), vigente_hasta=timezone.now(),
        )
        antes = ConsultaDatacreditoSnapshot.objects.values().get(pk=historico.pk)
        cliente.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional='TX20',
            raw_sanitizado=payload_pn(hc=None, tx='20', informacion=False),
        )
        nuevo = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
        _finalizar_error(historico, solicitud=self.solicitud, usuario=self.usuario,
                         estado='ERROR_TRANSITORIO', codigo='nuevo', tipo='NUEVO',
                         codigo_http=202, codigo_funcional='TX23')
        self.assertNotEqual(str(historico.pk), nuevo.snapshot_id)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.values().get(pk=historico.pk), antes)
        cliente.assert_called_once()

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_codigos_invalidos_no_persisten_datos_libres(self, cliente):
        from integrations.datacredito.decisor_client import _codigo_funcional_midecisor

        payload = payload_pn(hc='documento-sintetico', tx='token-sintetico')
        cliente.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional=_codigo_funcional_midecisor(payload),
            raw_sanitizado=payload,
        )
        resultado = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
        snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
        self.assertEqual(snapshot.codigo_http, 200)
        self.assertEqual(snapshot.codigo_funcional, '')
        self.assertEqual(snapshot.resultado_normalizado, {})
        self.assertEqual(snapshot.error_tipo, 'RESPUESTA_INVALIDA')

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_sin_informacion_coherente_o_contradictoria_se_persisten_distintas(self, cliente):
        for contradiccion in (False, True):
            with self.subTest(contradiccion=contradiccion):
                self.solicitud.apellidos = f'Prueba{contradiccion}'
                payload = payload_pn(informacion=False)
                if contradiccion:
                    payload['content']['respuesta']['informacionRiesgo'] = {'conInformacion': True, 'score': '853'}
                cliente.return_value = ResultadoMiDecisorRawSeguro(
                    status_code=200, codigo_funcional='HC13_TX02', raw_sanitizado=payload,
                )
                resultado = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
                snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
                self.assertEqual(snapshot.estado, 'ERROR_PERMANENTE' if contradiccion else 'SIN_INFORMACION')
                self.assertEqual(snapshot.codigo_http, 200)
                self.assertEqual(snapshot.codigo_funcional, 'HC13_TX02')
                if contradiccion:
                    self.assertEqual(snapshot.error_codigo, 'respuesta_funcional_inconsistente')
                    self.assertEqual(snapshot.resultado_normalizado, {})
                else:
                    self.assertIsNone(resultado.resultado_normalizado.score_externo)
                    self.assertEqual(snapshot.error_codigo, '')

    @patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural')
    def test_error_parseando_datos_no_pierde_http_ni_codigo_funcional(self, cliente):
        payload = payload_pn()
        payload['content']['respuesta']['informacionRiesgo'] = {}
        payload['score'] = 'NaN'
        cliente.return_value = ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional='HC13_TX02', raw_sanitizado=payload,
        )
        resultado = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
        repetido = obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=self.usuario)
        snapshot = ConsultaDatacreditoSnapshot.objects.get(pk=resultado.snapshot_id)
        self.assertEqual(snapshot.estado, 'ERROR_PERMANENTE')
        self.assertEqual(snapshot.codigo_http, 200)
        self.assertEqual(snapshot.codigo_funcional, 'HC13_TX02')
        self.assertEqual(snapshot.error_codigo, 'respuesta_funcional_indeterminada')
        self.assertEqual(snapshot.error_tipo, 'RESPUESTA_INVALIDA')
        self.assertEqual(snapshot.resultado_normalizado, {})
        self.assertEqual(resultado.snapshot_id, repetido.snapshot_id)
        cliente.assert_called_once()

    def _resultado_proveedor(self, servicio='decisor'):
        return ResultadoProveedorDatacreditoPrestador(
            estado_snapshot=ConsultaDatacreditoSnapshot.Estado.EXITOSO,
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                score_externo=800,
                rango_score='BAJO',
                total_obligaciones=3,
                saldo_total='100000.00',
                cuota_mensual_total='20000.00',
                obligaciones_vigentes=2,
                alertas=(),
                servicio_fuente=servicio,
                fecha_consulta=timezone.now().isoformat(),
            ),
            codigo_http=200,
            codigo_funcional='13',
        )

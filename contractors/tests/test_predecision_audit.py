from decimal import Decimal
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionPortalContratistas,
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
    InformacionLaboralSolicitudContratista,
    PredecisionPrestadorAudit,
)
from contractors.services.elegibilidad_conversion import TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION
from contractors.services.predecision import evaluar_predecision_contratista
from contractors.services.predecision_audit import (
    crear_auditoria_predecision_prestador,
    serializar_predecision_prestador,
)
from contractors.services.evaluacion_formal import evaluar_formalmente_solicitud_prestador
from contractors.services.datacredito_evaluacion import (
    MODO_CONSULTAR_SI_NO_EXISTE,
    MODO_REUTILIZAR_SNAPSHOT,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.datacredito.snapshots import construir_documento_hash, construir_request_fingerprint


User = get_user_model()


class AuditoriaPredecisionPrestadorTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.usuario = User.objects.create_user(
            username='auditor-prestador',
            email='auditor-prestador@example.com',
        )
        self.organizacion = ContractorOrganization.objects.create(
            name='Portal Contratistas',
            slug='contratistas',
            subdomain='contratistas',
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('5000000.00'),
            min_term_months=3,
            max_term_months=24,
            monthly_rate=Decimal('2.5000'),
            commission_rate=Decimal('5.0000'),
            commission_amount=Decimal('100000.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Prestadores',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('1200000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=4,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagadora Test',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            configuracion_portal=self.configuracion_portal,
            product_config=self.configuracion,
            usuario=self.usuario,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('1000000.00'),
            term_months=6,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number='123456789',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

    def _documentos_minimos_aprobados(self):
        for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
            ContractorApplicationDocument.objects.create(
                application=self.solicitud,
                document_type=tipo_documento,
                file=f'contractors/applications/documents/{tipo_documento}.pdf',
                original_filename=f'{tipo_documento}.pdf',
                content_type='application/pdf',
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )

    def _datos_contractuales(self):
        return InformacionLaboralSolicitudContratista.objects.create(
            solicitud=self.solicitud,
            cargo='Contratista comercial',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=self.hoy - relativedelta(months=1),
            fecha_fin_contrato=self.hoy + relativedelta(months=8),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('4000000.00'),
            valor_pendiente_cobrar=Decimal('8000000.00'),
            empresa=self.empresa,
            empresa_contratante_nombre='Empresa Pagadora Test',
        )

    def _request(self):
        request = RequestFactory().get(
            '/',
            HTTP_X_REQUEST_ID='req-predecision-123',
            HTTP_USER_AGENT='pytest-agent',
            REMOTE_ADDR='127.0.0.1',
        )
        request.user = self.usuario
        return request

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='bueno',
    )
    def test_crea_auditoria_con_datos_principales(self):
        resultado = evaluar_predecision_contratista(self.solicitud)
        auditoria = crear_auditoria_predecision_prestador(
            self.solicitud,
            resultado,
            request=self._request(),
        )

        self.assertEqual(auditoria.solicitud, self.solicitud)
        self.assertEqual(auditoria.usuario, self.usuario)
        self.assertEqual(auditoria.escenario_credito, self.solicitud.escenario_credito)
        self.assertEqual(auditoria.decision, resultado.decision)
        self.assertEqual(auditoria.eligible, resultado.elegible)
        self.assertEqual(auditoria.score_status, resultado.score_status)
        self.assertEqual(auditoria.score_banda, resultado.score_resultado['banda']['nombre'])
        self.assertEqual(auditoria.score_version_configuracion, 'prestadores_score_v2_2026_06')
        self.assertEqual(auditoria.datacredito_status, resultado.datacredito_status)
        self.assertEqual(auditoria.datacredito_fuente, 'mock')
        self.assertEqual(auditoria.request_id, 'req-predecision-123')
        self.assertEqual(auditoria.ip_address, '127.0.0.1')
        self.assertEqual(auditoria.user_agent, 'pytest-agent')

    def test_permite_multiples_auditorias_por_solicitud(self):
        resultado = evaluar_predecision_contratista(self.solicitud)

        crear_auditoria_predecision_prestador(self.solicitud, resultado, usuario=self.usuario)
        crear_auditoria_predecision_prestador(self.solicitud, resultado, usuario=self.usuario)

        self.assertEqual(PredecisionPrestadorAudit.objects.filter(solicitud=self.solicitud).count(), 2)

    def test_serializacion_no_incluye_raw_prompt_base64_token_ni_credenciales(self):
        payload = {
            'decision': 'PREAPROBADO_READ_ONLY',
            'raw_datacredito': {'score': 800},
            'prompt_ia': 'prompt sensible',
            'contrato_base64': 'data:application/pdf;base64,abc',
            'access_token': 'token-real',
            'client_secret': 'secreto',
            'password': 'clave',
            'api_key': 'sk-test',
            'datacredito_resultado': {
                'metadata_segura': {'documento_hash': 'hash-seguro'},
                'respuesta_cruda': {'documento': '123456789'},
            },
        }

        serializado = serializar_predecision_prestador(payload)
        texto = str(serializado)

        self.assertNotIn('raw_datacredito', texto)
        self.assertNotIn('prompt_ia', texto)
        self.assertNotIn('contrato_base64', texto)
        self.assertNotIn('access_token', texto)
        self.assertNotIn('client_secret', texto)
        self.assertNotIn('password', texto)
        self.assertNotIn('api_key', texto)
        self.assertNotIn('token-real', texto)
        self.assertNotIn('secreto', texto)
        self.assertNotIn('123456789', texto)
        self.assertIn('hash-seguro', texto)

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='bueno',
    )
    def test_guarda_score_banda_version_datacredito_bloqueos_advertencias(self):
        resultado = evaluar_predecision_contratista(self.solicitud)
        auditoria = crear_auditoria_predecision_prestador(self.solicitud, resultado, usuario=self.usuario)

        self.assertIsNotNone(auditoria.score_final)
        self.assertEqual(auditoria.score_banda, 'PREMIUM')
        self.assertEqual(auditoria.score_version_configuracion, 'prestadores_score_v2_2026_06')
        self.assertEqual(auditoria.datacredito_status, 'DISPONIBLE')
        self.assertFalse(auditoria.datacredito_mora_severa)
        self.assertFalse(auditoria.datacredito_mora_actual)
        self.assertEqual(auditoria.bloqueos, [])
        self.assertEqual(auditoria.advertencias, [])
        self.assertEqual(auditoria.razones, [])

    def test_admin_registrado(self):
        self.assertIn(PredecisionPrestadorAudit, admin.site._registry)

    def test_no_crea_credito_ni_credito_libranza_y_no_modifica_decision(self):
        resultado = evaluar_predecision_contratista(self.solicitud)
        decision_original = resultado.decision
        estado_original = self.solicitud.status

        crear_auditoria_predecision_prestador(self.solicitud, resultado, usuario=self.usuario)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_original)
        self.assertEqual(resultado.decision, decision_original)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)


class EvaluacionFormalPredecisionPrestadorTests(AuditoriaPredecisionPrestadorTests):
    TEXTO_AUTORIZACION_DATACREDITO = 'Texto autorizado DataCredito UAT para pruebas.'
    VERSION_AUTORIZACION_DATACREDITO = 'uat-v1'

    def _crear_autorizacion_datacredito(self):
        return AutorizacionConsultaDatacreditoPrestador.objects.create(
            solicitud=self.solicitud,
            usuario=self.usuario,
            autorizado=True,
            version_texto=self.VERSION_AUTORIZACION_DATACREDITO,
            texto_hash=hashlib.sha256(self.TEXTO_AUTORIZACION_DATACREDITO.encode('utf-8')).hexdigest(),
            finalidad='Consulta DataCredito prestador prueba',
            accepted_at=timezone.now(),
            source=AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO,
        )

    def _crear_snapshot(self, servicio, *, estado='EXITOSA_CON_INFORMACION', score=820, autorizacion=None):
        return ConsultaDatacreditoSnapshot.objects.create(
            servicio=servicio,
            ambiente='uat',
            proveedor=ConsultaDatacreditoSnapshot.PROVEEDOR_DATACREDITO_REAL,
            tipo_documento='CC',
            request_fingerprint=construir_request_fingerprint(
                ambiente='uat',
                servicio=servicio,
                tipo_documento=self.solicitud.document_type,
                numero_documento=self.solicitud.document_number,
                apellido=self.solicitud.last_name,
            ),
            documento_hash=construir_documento_hash(
                ambiente='uat',
                tipo_documento=self.solicitud.document_type,
                numero_documento=self.solicitud.document_number,
            ),
            documento_enmascarado='*****6789',
            estado_normalizado=estado,
            http_status=200,
            codigo_funcional='HC13' if servicio == 'decisor' else '13',
            proveedor_respondio=True,
            consulta_procesada=True,
            con_informacion=estado == 'EXITOSA_CON_INFORMACION',
            utilizable_para_score=servicio == 'decisor' and score is not None,
            requiere_revision_manual=estado != 'EXITOSA_CON_INFORMACION',
            requiere_revision_cumplimiento=False,
            resultado_normalizado={
                'estado': estado,
                'score_midecisor': score if servicio == 'decisor' else None,
                'score': score if servicio == 'decisor' else None,
                'scores_hdc': [],
                'score_normalizado_0_1000': score if servicio == 'decisor' else None,
                'saldo_mora': '0',
                'mora_severa': False,
                'mora_actual': False,
                'viabilidad': 'ALTA',
                'viable': True,
                'rating_recaudo': 'A',
                'monto_sugerido': 1200000,
                'cantidad_alertas': 0,
                'alertas_resumen': [],
                'requiere_revision_manual': estado != 'EXITOSA_CON_INFORMACION',
                'requiere_revision_cumplimiento': False,
                'codigo_respuesta': '13',
                'response_code': '13',
                'con_informacion': estado == 'EXITOSA_CON_INFORMACION',
                'disponible': estado == 'EXITOSA_CON_INFORMACION',
                'fuente': 'midecisor' if servicio == 'decisor' else 'historial_credito',
                'servicio': 'midecisor' if servicio == 'decisor' else 'historial_credito',
                'nivel_riesgo': 'BAJO',
                'error_tipo': None,
            },
            consulted_at=timezone.now(),
            vigente_hasta=timezone.now() + relativedelta(days=30),
            source=ConsultaDatacreditoSnapshot.SOURCE_CONSULTA_REAL,
            autorizacion_id=str(autorizacion.id) if autorizacion else '',
            autorizacion_version_texto=autorizacion.version_texto if autorizacion else '',
            autorizacion_texto_hash=autorizacion.texto_hash if autorizacion else '',
            autorizacion_accepted_at=autorizacion.accepted_at if autorizacion else None,
        )

    def test_servicio_crea_auditoria(self):
        evaluacion = evaluar_formalmente_solicitud_prestador(
            self.solicitud,
            usuario=self.usuario,
            request=self._request(),
        )

        self.assertEqual(evaluacion.auditoria.solicitud, self.solicitud)
        self.assertEqual(evaluacion.auditoria.usuario, self.usuario)
        self.assertEqual(evaluacion.auditoria.decision, evaluacion.resultado.decision)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)

    def test_servicio_no_modifica_estado_ni_crea_credito(self):
        estado_inicial = self.solicitud.status

        evaluar_formalmente_solicitud_prestador(self.solicitud, usuario=self.usuario)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_inicial)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    @patch('contractors.services.datacredito_evaluacion.solicitud_tiene_autorizacion_datacredito')
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_evaluacion_formal_por_defecto_no_consulta_datacredito(
        self,
        decisor_mock,
        historial_mock,
        autorizacion_mock,
    ):
        evaluar_formalmente_solicitud_prestador(self.solicitud, usuario=self.usuario)

        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()
        autorizacion_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
        DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_AUTORIZACION_DATACREDITO,
        DATACREDITO_AUTHORIZATION_TEXT=TEXTO_AUTORIZACION_DATACREDITO,
    )
    @patch('contractors.datacredito.adapter.historial_client.consultar_historial_credito')
    @patch('contractors.datacredito.adapter.decisor_client.consultar_midecisor_persona_natural')
    def test_modo_reutilizar_usa_snapshots_sin_llamadas_http(self, decisor_adapter_mock, historial_adapter_mock):
        autorizacion = self._crear_autorizacion_datacredito()
        snapshot_decisor = self._crear_snapshot('decisor', score=830, autorizacion=autorizacion)
        snapshot_historial = self._crear_snapshot(
            'historial',
            estado='EXITOSA_SIN_INFORMACION',
            score=None,
            autorizacion=autorizacion,
        )

        evaluacion = evaluar_formalmente_solicitud_prestador(
            self.solicitud,
            usuario=self.usuario,
            modo_datacredito=MODO_REUTILIZAR_SNAPSHOT,
        )

        auditoria = evaluacion.auditoria
        self.assertEqual(auditoria.snapshot_decisor, snapshot_decisor)
        self.assertEqual(auditoria.snapshot_historial, snapshot_historial)
        self.assertEqual(auditoria.datacredito_modo, MODO_REUTILIZAR_SNAPSHOT)
        self.assertTrue(auditoria.decisor_reutilizado)
        self.assertTrue(auditoria.historial_reutilizado)
        self.assertFalse(auditoria.decisor_consultado)
        self.assertFalse(auditoria.historial_consultado)
        decisor_adapter_mock.assert_not_called()
        historial_adapter_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
        DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_AUTORIZACION_DATACREDITO,
        DATACREDITO_AUTHORIZATION_TEXT=TEXTO_AUTORIZACION_DATACREDITO,
    )
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_solo_consulta_servicio_faltante(self, decisor_mock, historial_mock):
        from integrations.datacredito.dto import ResultadoHistorialCreditoRawSeguro

        autorizacion = self._crear_autorizacion_datacredito()
        snapshot_decisor = self._crear_snapshot('decisor', score=810, autorizacion=autorizacion)
        historial_mock.return_value = ResultadoHistorialCreditoRawSeguro(
            status_code=200,
            response_code='14',
            raw_sanitizado={'ReportHDCplus': {'productResult': {'responseCode': '14'}}},
        )

        evaluacion = evaluar_formalmente_solicitud_prestador(
            self.solicitud,
            usuario=self.usuario,
            modo_datacredito=MODO_CONSULTAR_SI_NO_EXISTE,
        )

        auditoria = evaluacion.auditoria
        self.assertEqual(auditoria.snapshot_decisor, snapshot_decisor)
        self.assertIsNotNone(auditoria.snapshot_historial)
        self.assertEqual(auditoria.autorizacion_datacredito, autorizacion)
        self.assertEqual(auditoria.snapshot_historial.autorizacion_id, str(autorizacion.id))
        self.assertTrue(auditoria.decisor_reutilizado)
        self.assertFalse(auditoria.decisor_consultado)
        self.assertTrue(auditoria.historial_consultado)
        decisor_mock.assert_not_called()
        historial_mock.assert_called_once()

    @override_settings(
        DATACREDITO_REAL_ENABLED=True,
        DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
        DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_AUTORIZACION_DATACREDITO,
        DATACREDITO_AUTHORIZATION_TEXT=TEXTO_AUTORIZACION_DATACREDITO,
    )
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_consulta_sin_autorizacion_no_llama_proveedor(self, decisor_mock, historial_mock):
        evaluacion = evaluar_formalmente_solicitud_prestador(
            self.solicitud,
            usuario=self.usuario,
            modo_datacredito=MODO_CONSULTAR_SI_NO_EXISTE,
        )

        self.assertEqual(evaluacion.auditoria.datacredito_modo, MODO_CONSULTAR_SI_NO_EXISTE)
        self.assertIsNone(evaluacion.auditoria.snapshot_decisor)
        self.assertIsNone(evaluacion.auditoria.snapshot_historial)
        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()

    @override_settings(
        DATACREDITO_REAL_ENABLED=False,
        DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
        DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_AUTORIZACION_DATACREDITO,
        DATACREDITO_AUTHORIZATION_TEXT=TEXTO_AUTORIZACION_DATACREDITO,
    )
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_datacredito_real_apagado_reutiliza_snapshots_pero_no_llama(self, decisor_mock, historial_mock):
        autorizacion = self._crear_autorizacion_datacredito()
        self._crear_snapshot('decisor', score=800, autorizacion=autorizacion)
        self._crear_snapshot('historial', estado='EXITOSA_SIN_INFORMACION', score=None, autorizacion=autorizacion)

        evaluacion = evaluar_formalmente_solicitud_prestador(
            self.solicitud,
            usuario=self.usuario,
            modo_datacredito=MODO_CONSULTAR_SI_NO_EXISTE,
        )

        self.assertTrue(evaluacion.auditoria.decisor_reutilizado)
        self.assertTrue(evaluacion.auditoria.historial_reutilizado)
        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()


class AdminEvaluacionFormalPredecisionPrestadorTests(AuditoriaPredecisionPrestadorTests):
    def setUp(self):
        super().setUp()
        self.usuario_staff = User.objects.create_user(
            username='staff-evaluador',
            email='staff-evaluador@example.com',
            is_staff=True,
        )
        self.usuario_sin_permiso = User.objects.create_user(
            username='staff-sin-evaluacion',
            email='staff-sin-evaluacion@example.com',
            is_staff=True,
        )
        self.usuario_staff.user_permissions.add(
            Permission.objects.get(codename='can_evaluate_contractor_predecision'),
        )
        self.factory = RequestFactory()

    def _request_admin(self, usuario):
        request = self.factory.get('/admin/')
        request.user = usuario
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        request.META['HTTP_USER_AGENT'] = 'admin-test'
        request.META['HTTP_X_REQUEST_ID'] = 'admin-eval-1'
        return request

    def _crear_solicitud_adicional(self):
        solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            configuracion_portal=self.configuracion_portal,
            product_config=self.configuracion,
            usuario=self.usuario,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('1000000.00'),
            term_months=6,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number='987654321',
            first_name='Luis',
            last_name='Gomez',
            phone='3007654321',
            email='luis@example.com',
            address='Calle 9 # 8-7',
            accepted_terms=True,
            source_subdomain='contratistas',
        )
        for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
            ContractorApplicationDocument.objects.create(
                application=solicitud,
                document_type=tipo_documento,
                file=f'contractors/applications/documents/{tipo_documento}-2.pdf',
                original_filename=f'{tipo_documento}-2.pdf',
                content_type='application/pdf',
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )
        InformacionLaboralSolicitudContratista.objects.create(
            solicitud=solicitud,
            cargo='Contratista tecnico',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=self.hoy - relativedelta(months=1),
            fecha_fin_contrato=self.hoy + relativedelta(months=8),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('4000000.00'),
            valor_pendiente_cobrar=Decimal('8000000.00'),
            empresa=self.empresa,
            empresa_contratante_nombre='Empresa Pagadora Test',
        )
        return solicitud

    def test_admin_action_requiere_permiso(self):
        admin_modelo = admin.site._registry[ContractorApplication]

        acciones_sin_permiso = admin_modelo.get_actions(self._request_admin(self.usuario_sin_permiso))
        acciones_con_permiso = admin_modelo.get_actions(self._request_admin(self.usuario_staff))

        self.assertNotIn('accion_evaluar_predecision', acciones_sin_permiso)
        self.assertIn('accion_evaluar_predecision', acciones_con_permiso)

    def test_admin_action_crea_auditoria_y_resume_decisiones(self):
        admin_modelo = admin.site._registry[ContractorApplication]
        request = self._request_admin(self.usuario_staff)
        with patch.object(admin_modelo, 'message_user') as message_user:
            admin_modelo.accion_evaluar_predecision(
                request,
                ContractorApplication.objects.filter(pk=self.solicitud.pk),
            )

        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)
        auditoria = PredecisionPrestadorAudit.objects.get()
        self.assertEqual(auditoria.usuario, self.usuario_staff)
        mensaje_final = message_user.call_args_list[-1].args[1]
        self.assertIn('evaluadas=1', mensaje_final)
        self.assertIn('preaprobadas_read_only=1', mensaje_final)
        self.assertIn('errores=0', mensaje_final)

    def test_multiples_solicitudes_generan_multiples_auditorias(self):
        otra_solicitud = self._crear_solicitud_adicional()
        admin_modelo = admin.site._registry[ContractorApplication]
        request = self._request_admin(self.usuario_staff)
        with patch.object(admin_modelo, 'message_user'):
            admin_modelo.accion_evaluar_predecision(
                request,
                ContractorApplication.objects.filter(pk__in=[self.solicitud.pk, otra_solicitud.pk]),
            )

        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 2)

    def test_error_por_solicitud_no_detiene_toda_la_accion(self):
        otra_solicitud = self._crear_solicitud_adicional()
        admin_modelo = admin.site._registry[ContractorApplication]
        request = self._request_admin(self.usuario_staff)
        resultado_simulado = SimpleNamespace(decision='REQUIERE_REVISION_MANUAL')
        evaluacion_simulada = SimpleNamespace(resultado=resultado_simulado)

        with patch.object(admin_modelo, 'message_user') as message_user, patch(
            'contractors.admin.evaluar_formalmente_solicitud_prestador',
            side_effect=[Exception('fallo controlado'), evaluacion_simulada],
        ):
            admin_modelo.accion_evaluar_predecision(
                request,
                ContractorApplication.objects.filter(pk__in=[self.solicitud.pk, otra_solicitud.pk]).order_by('pk'),
            )

        mensaje_final = message_user.call_args_list[-1].args[1]
        self.assertIn('evaluadas=1', mensaje_final)
        self.assertIn('revision_manual=1', mensaje_final)
        self.assertIn('errores=1', mensaje_final)

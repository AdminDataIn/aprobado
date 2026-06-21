from decimal import Decimal
import hashlib
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionPortalContratistas,
    ContractorApplication,
    TimelinePrestador,
)
from contractors.services.autorizacion_datacredito import (
    ErrorAutorizacionDatacredito,
    diagnosticar_configuracion_staff_uat_datacredito,
    obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_prestador,
    revocar_autorizacion_datacredito_prestador,
)
from contractors.services.datacredito_evaluacion import (
    MODO_CONSULTAR_SI_NO_EXISTE,
    MODO_REUTILIZAR_SNAPSHOT,
    resolver_datacredito_para_solicitud_prestador,
)
from integrations.datacredito.auth import SERVICIO_DECISOR
from integrations.datacredito.snapshots import construir_documento_hash, construir_request_fingerprint
from integrations.models import ConsultaDatacreditoSnapshot


User = get_user_model()


@override_settings(
    DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-v1',
    DATACREDITO_AUTHORIZATION_TEXT='Texto aprobado para consulta DataCredito de prestadores.',
    DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas',
)
class AutorizacionDatacreditoPrestadorTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='prestador-dc',
            email='prestador-dc@example.com',
            password='x',
        )
        self.staff = User.objects.create_user(
            username='staff-dc',
            email='staff-dc@example.com',
            password='x',
            is_staff=True,
        )
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Prestadores',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('10000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.solicitud = ContractorApplication.objects.create(
            configuracion_portal=self.configuracion_portal,
            usuario=self.usuario,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('3000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('320000.00'),
            simulation_payload={},
            document_type='CC',
            document_number='1020304050',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )

    def _request(self, usuario=None):
        request = RequestFactory().post(
            '/',
            HTTP_X_REQUEST_ID='req-auth-dc',
            HTTP_USER_AGENT='pytest-agent',
            REMOTE_ADDR='127.0.0.1',
        )
        request.user = usuario or self.usuario
        return request

    def _hash_texto(self, texto='Texto aprobado para consulta DataCredito de prestadores.'):
        return hashlib.sha256(texto.encode('utf-8')).hexdigest()

    def _registrar_autorizacion_publica(self):
        return registrar_autorizacion_datacredito_prestador(
            solicitud=self.solicitud,
            usuario=self.usuario,
            source=AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO,
            request=self._request(),
        )

    def _crear_snapshot_sin_autorizacion(self):
        return ConsultaDatacreditoSnapshot.objects.create(
            servicio=SERVICIO_DECISOR,
            ambiente='uat',
            proveedor=ConsultaDatacreditoSnapshot.PROVEEDOR_DATACREDITO_REAL,
            tipo_documento='CC',
            request_fingerprint=construir_request_fingerprint(
                ambiente='uat',
                servicio=SERVICIO_DECISOR,
                tipo_documento=self.solicitud.document_type,
                numero_documento=self.solicitud.document_number,
                apellido=self.solicitud.last_name,
            ),
            documento_hash=construir_documento_hash(
                ambiente='uat',
                tipo_documento=self.solicitud.document_type,
                numero_documento=self.solicitud.document_number,
            ),
            documento_enmascarado='******4050',
            estado_normalizado='EXITOSA_CON_INFORMACION',
            http_status=200,
            codigo_funcional='13',
            proveedor_respondio=True,
            consulta_procesada=True,
            con_informacion=True,
            utilizable_para_score=True,
            requiere_revision_manual=False,
            requiere_revision_cumplimiento=False,
            resultado_normalizado={
                'estado': 'EXITOSA_CON_INFORMACION',
                'score': 800,
                'score_normalizado_0_1000': 800,
                'disponible': True,
                'fuente': 'midecisor',
                'servicio': 'midecisor',
            },
            consulted_at=timezone.now(),
            vigente_hasta=timezone.now() + relativedelta(days=30),
            source=ConsultaDatacreditoSnapshot.SOURCE_CONSULTA_REAL,
        )

    def test_terminos_generales_no_son_autorizacion_datacredito(self):
        estado = obtener_autorizacion_datacredito_vigente(self.solicitud)

        self.assertFalse(estado.vigente)
        self.assertEqual(estado.razon, 'autorizacion_datacredito_no_encontrada')

    def test_registra_autorizacion_publica_versionada_y_timeline_seguro(self):
        autorizacion = self._registrar_autorizacion_publica()

        self.assertEqual(autorizacion.solicitud, self.solicitud)
        self.assertEqual(autorizacion.usuario, self.usuario)
        self.assertEqual(autorizacion.version_texto, 'uat-v1')
        self.assertEqual(autorizacion.texto_hash, self._hash_texto())
        self.assertEqual(autorizacion.source, AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO)
        estado = obtener_autorizacion_datacredito_vigente(self.solicitud)
        self.assertTrue(estado.vigente)

        evento = TimelinePrestador.objects.get(tipo_evento='DATACREDITO_AUTORIZACION_ACEPTADA')
        metadata = str(evento.metadata)
        self.assertIn('version_texto', metadata)
        self.assertNotIn(self.solicitud.document_number, metadata)
        self.assertNotIn('Texto aprobado', metadata)

    def test_revocar_autorizacion_bloquea_vigencia(self):
        autorizacion = self._registrar_autorizacion_publica()

        revocar_autorizacion_datacredito_prestador(autorizacion, usuario=self.usuario)

        estado = obtener_autorizacion_datacredito_vigente(self.solicitud)
        self.assertFalse(estado.vigente)
        self.assertEqual(estado.estado, 'REVOCADA')
        self.assertTrue(TimelinePrestador.objects.filter(tipo_evento='DATACREDITO_AUTORIZACION_REVOCADA').exists())

    def test_version_no_vigente_no_autoriza(self):
        self._registrar_autorizacion_publica()

        with self.settings(DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-v2'):
            estado = obtener_autorizacion_datacredito_vigente(self.solicitud)

        self.assertFalse(estado.vigente)
        self.assertEqual(estado.estado, 'VERSION_NO_VIGENTE')

    def test_hash_no_vigente_no_autoriza(self):
        self._registrar_autorizacion_publica()

        with self.settings(DATACREDITO_AUTHORIZATION_TEXT='Texto aprobado actualizado.'):
            estado = obtener_autorizacion_datacredito_vigente(self.solicitud)

        self.assertFalse(estado.vigente)
        self.assertEqual(estado.estado, 'HASH_INVALIDO')

    @override_settings(DATACREDITO_REAL_ENABLED=True)
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_consulta_sin_autorizacion_no_llama_proveedor_y_registra_bloqueo(self, decisor_mock, historial_mock):
        resultado = resolver_datacredito_para_solicitud_prestador(
            solicitud=self.solicitud,
            modo=MODO_CONSULTAR_SI_NO_EXISTE,
            usuario=self.usuario,
            request=self._request(),
        )

        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()
        self.assertIn('decisor:autorizacion_datacredito_no_encontrada', resultado.errores_seguros)
        self.assertTrue(
            TimelinePrestador.objects.filter(
                tipo_evento='DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION',
            ).exists(),
        )

    @override_settings(DATACREDITO_ALLOW_LEGACY_SNAPSHOT_WITHOUT_AUTH_UAT=False)
    def test_snapshot_legacy_sin_autorizacion_no_se_reutiliza_por_defecto(self):
        self._crear_snapshot_sin_autorizacion()

        resultado = resolver_datacredito_para_solicitud_prestador(
            solicitud=self.solicitud,
            modo=MODO_REUTILIZAR_SNAPSHOT,
            usuario=self.usuario,
            request=self._request(),
        )

        self.assertFalse(resultado.reutilizado_decisor)
        self.assertIn('decisor:snapshot_sin_autorizacion_datacredito', resultado.errores_seguros)

    @override_settings(
        DATACREDITO_ENVIRONMENT='prod',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='1020304050',
    )
    def test_registro_uat_bloqueado_en_produccion(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))

        with self.assertRaises(PermissionDenied):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='1020304050',
    )
    def test_registro_uat_exige_permiso_y_justificacion(self):
        with self.assertRaises(PermissionDenied):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)
        with self.assertRaises(ValidationError):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='',
            )

        autorizacion = registrar_autorizacion_datacredito_prestador(
            solicitud=self.solicitud,
            usuario=self.staff,
            source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
            request=self._request(self.staff),
            justificacion='Prueba UAT controlada.',
        )

        self.assertEqual(autorizacion.source, AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT)
        self.assertTrue(TimelinePrestador.objects.filter(tipo_evento='DATACREDITO_AUTORIZACION_UAT_REGISTRADA').exists())

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='',
    )
    @patch('integrations.datacredito.snapshots.historial_client.consultar_historial_credito')
    @patch('integrations.datacredito.snapshots.decisor_client.consultar_midecisor_persona_natural')
    def test_allowlist_ausente_o_vacia_bloquea_staff_uat(self, decisor_mock, historial_mock):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)

        with self.assertRaisesMessage(
            ValidationError,
            'No hay documentos Demo autorizados configurados para pruebas UAT.',
        ):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)
        self.assertTrue(
            TimelinePrestador.objects.filter(
                tipo_evento='DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION',
            ).exists(),
        )
        metadata = str(TimelinePrestador.objects.latest('id').metadata)
        self.assertNotIn(self.solicitud.document_number, metadata)
        decisor_mock.assert_not_called()
        historial_mock.assert_not_called()

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='  ,  102-030-4050 , 1020304050 , ',
    )
    def test_allowlist_normaliza_espacios_vacios_y_duplicados(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)

        autorizacion = registrar_autorizacion_datacredito_prestador(
            solicitud=self.solicitud,
            usuario=self.staff,
            source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
            request=self._request(self.staff),
            justificacion='Prueba UAT controlada.',
        )
        diagnostico = diagnosticar_configuracion_staff_uat_datacredito()

        self.assertEqual(autorizacion.source, AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT)
        self.assertEqual(diagnostico['uat_demo_documentos_configurados'], 1)

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='102030405',
    )
    def test_coincidencia_parcial_bloquea_staff_uat(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)

        with self.assertRaisesMessage(ValidationError, 'El documento no esta autorizado para pruebas UAT DataCredito.'):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='9999999999',
    )
    def test_documento_no_autorizado_bloquea_staff_uat(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)

        with self.assertRaisesMessage(ValidationError, 'El documento no esta autorizado para pruebas UAT DataCredito.'):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)

    @override_settings(
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='',
        DATACREDITO_AUTHORIZATION_TEXT='',
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='1020304050',
    )
    def test_falta_texto_versionado_bloquea_staff_uat(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='can_register_uat_datacredito_authorization'))
        self.staff = User.objects.get(pk=self.staff.pk)

        with self.assertRaisesMessage(ErrorAutorizacionDatacredito, 'texto_autorizacion_datacredito_no_configurado'):
            registrar_autorizacion_datacredito_prestador(
                solicitud=self.solicitud,
                usuario=self.staff,
                source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                request=self._request(self.staff),
                justificacion='Prueba UAT controlada.',
            )

        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='1020304050',
    )
    def test_diagnostico_seguro_detecta_ip_hdc_faltante(self):
        diagnostico = diagnosticar_configuracion_staff_uat_datacredito()

        self.assertFalse(diagnostico['hdc.server_ip_configurada'])
        self.assertTrue(diagnostico['uat_demo_allowlist_configurada'])
        self.assertEqual(diagnostico['uat_demo_documentos_configurados'], 1)
        self.assertFalse(diagnostico['staff_uat_habilitable'])

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_HDC_SERVER_IP_ADDRESS='72.60.67.60',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='1020304050',
    )
    def test_diagnostico_seguro_habilitable_sin_exponer_documentos(self):
        diagnostico = diagnosticar_configuracion_staff_uat_datacredito()

        self.assertTrue(diagnostico['hdc.server_ip_configurada'])
        self.assertTrue(diagnostico['uat_demo_allowlist_configurada'])
        self.assertEqual(diagnostico['uat_demo_documentos_configurados'], 1)
        self.assertTrue(diagnostico['staff_uat_habilitable'])
        self.assertNotIn(self.solicitud.document_number, str(diagnostico))

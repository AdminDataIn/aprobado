from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionPortalContratistas,
    ContractorApplication,
    InformacionLaboralSolicitudContratista,
    PredecisionPrestadorAudit,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa


User = get_user_model()


class BandejaRiesgoPrestadoresTests(TestCase):
    def setUp(self):
        self.url_bandeja = reverse('gestion:prestadores_riesgo')
        self.usuario_operaciones = User.objects.create_user(
            username='operaciones-riesgo',
            email='operaciones-riesgo@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_sin_permiso = User.objects.create_user(
            username='operaciones-sin-permiso',
            email='operaciones-sin-permiso@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_operaciones.user_permissions.add(
            Permission.objects.get(codename='can_view_contractor_risk_queue'),
        )
        self.usuario_datacredito = User.objects.create_user(
            username='operaciones-datacredito',
            email='operaciones-datacredito@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_datacredito.user_permissions.add(
            Permission.objects.get(codename='can_view_contractor_risk_queue'),
            Permission.objects.get(codename='can_run_contractor_datacredito_evaluation'),
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
        self.empresa = Empresa.objects.create(
            nombre='Empresa Convenio Riesgo',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )

    def _crear_solicitud(self, documento='123456789', nombre='Ana', apellido='Perez'):
        solicitud = ContractorApplication.objects.create(
            configuracion_portal=self.configuracion_portal,
            usuario=self.usuario_operaciones,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('3000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('320000.00'),
            simulation_payload={},
            document_type='CC',
            document_number=documento,
            first_name=nombre,
            last_name=apellido,
            phone='3001234567',
            email=f'{documento}@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )
        hoy = timezone.localdate()
        InformacionLaboralSolicitudContratista.objects.create(
            solicitud=solicitud,
            empresa=self.empresa,
            cargo='Contratista',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=hoy - relativedelta(months=1),
            fecha_fin_contrato=hoy + relativedelta(months=12),
            valor_total_contrato=Decimal('20000000.00'),
            valor_pagado_contrato=Decimal('5000000.00'),
            valor_pendiente_cobrar=Decimal('15000000.00'),
            empresa_contratante_nombre='Empresa Convenio Riesgo',
        )
        return solicitud

    def _crear_auditoria(self, decision='REQUIERE_REVISION_MANUAL', documento='123456789', minutos=0):
        solicitud = self._crear_solicitud(documento=documento)
        auditoria = PredecisionPrestadorAudit.objects.create(
            solicitud=solicitud,
            usuario=self.usuario_operaciones,
            escenario_credito=solicitud.escenario_credito,
            decision=decision,
            eligible=decision == 'PREAPROBADO_READ_ONLY',
            requiere_revision_manual=decision == 'REQUIERE_REVISION_MANUAL',
            monto_maximo_sugerido=Decimal('2500000.00'),
            plazo_maximo_sugerido=12,
            score_status='EVALUADO',
            score_final=Decimal('780.00'),
            score_banda='ALTA',
            score_version_configuracion='prestadores_score_v1',
            datacredito_status='DISPONIBLE',
            datacredito_fuente='mock',
            capacidad_status='APROBADO',
            riesgo_status='APROBADO',
            bloqueos=[],
            advertencias=['revision_operativa'],
            razones=['score_alto'],
            resultado_sanitizado={
                'documental': {'status': 'APROBADO'},
                'capacidad_resultado': {'eligible': True, 'valor_pendiente_cobrar': '15000000.00'},
                'score_resultado': {'score_final': 780, 'banda': {'nombre': 'ALTA'}},
                'datacredito_resultado': {'disponible': True, 'fuente': 'mock'},
                'segundo_credito_resultado': {'evaluado': False},
                'recogida_cartera_resultado': {'evaluado': False},
            },
        )
        if minutos:
            PredecisionPrestadorAudit.objects.filter(pk=auditoria.pk).update(
                created_at=timezone.now() + relativedelta(minutes=minutos),
            )
            auditoria.refresh_from_db()
        return auditoria

    def test_usuario_anonimo_redirige_a_login(self):
        respuesta = self.client.get(self.url_bandeja)

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/admin/login/', respuesta['Location'])

    def test_staff_sin_permiso_no_accede(self):
        self.client.force_login(self.usuario_sin_permiso)

        respuesta = self.client.get(self.url_bandeja)

        self.assertEqual(respuesta.status_code, 403)

    def test_staff_con_permiso_accede_a_bandeja(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(self.url_bandeja)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, f'#{auditoria.solicitud_id}')
        self.assertContains(respuesta, 'Empresa Convenio Riesgo')

    def test_filtra_por_decision(self):
        self._crear_auditoria(decision='PREAPROBADO_READ_ONLY', documento='100000001')
        self._crear_auditoria(decision='BLOQUEADO_READ_ONLY', documento='100000002')
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(self.url_bandeja, {'decision': 'BLOQUEADO_READ_ONLY'})

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'BLOQUEADO_READ_ONLY')
        decisiones_filas = [fila['auditoria'].decision for fila in respuesta.context['filas']]
        self.assertEqual(decisiones_filas, ['BLOQUEADO_READ_ONLY'])

    def test_detalle_muestra_snapshot_completo(self):
        auditoria = self._crear_auditoria(decision='PREAPROBADO_READ_ONLY')
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(reverse('gestion:prestadores_riesgo_detalle', args=[auditoria.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Documental')
        self.assertContains(respuesta, 'Capacidad')
        self.assertContains(respuesta, 'Score')
        self.assertContains(respuesta, 'DataCredito')
        self.assertContains(respuesta, 'Segundo credito')
        self.assertContains(respuesta, 'Recogida cartera')
        self.assertContains(respuesta, 'Bloqueos')
        self.assertContains(respuesta, 'Advertencias')
        self.assertContains(respuesta, 'Razones')

    def test_paginacion_limita_primera_pagina(self):
        for indice in range(25):
            self._crear_auditoria(documento=f'2000000{indice:02d}', minutos=indice)
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(self.url_bandeja)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.context['page_obj'].object_list), 20)
        self.assertContains(respuesta, 'Pagina 1 de 2')

    def test_orden_mas_reciente_primero(self):
        auditoria_antigua = self._crear_auditoria(documento='300000001', minutos=-10)
        auditoria_reciente = self._crear_auditoria(documento='300000002', minutos=10)
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(self.url_bandeja)

        primera = respuesta.context['page_obj'].object_list[0]
        self.assertEqual(primera.id, auditoria_reciente.id)
        self.assertNotEqual(primera.id, auditoria_antigua.id)

    def test_no_crea_credito_ni_credito_libranza(self):
        self._crear_auditoria()
        self.client.force_login(self.usuario_operaciones)

        self.client.get(self.url_bandeja)

        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    def test_detalle_muestra_boton_datacredito_con_permiso(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_datacredito)

        respuesta = self.client.get(reverse('gestion:prestadores_riesgo_detalle', args=[auditoria.id]))

        self.assertContains(respuesta, 'Consultar DataCredito y reevaluar')

    def test_evaluar_datacredito_sin_permiso_recibe_403(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_operaciones)

        respuesta = self.client.get(reverse('gestion:prestadores_riesgo_datacredito', args=[auditoria.id]))

        self.assertEqual(respuesta.status_code, 403)

    @override_settings(DATACREDITO_DOCUMENT_HASH_SECRET='secreto-hmac-pruebas')
    def test_evaluar_datacredito_con_permiso_muestra_confirmacion(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_datacredito)

        respuesta = self.client.get(reverse('gestion:prestadores_riesgo_datacredito', args=[auditoria.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Reutilizar snapshots')
        self.assertContains(respuesta, 'Consultar solo si falta snapshot')
        self.assertContains(respuesta, 'Autorizacion especifica DataCredito')
        self.assertContains(respuesta, 'Requiere autorizacion DataCredito vigente')
        self.assertNotContains(respuesta, 'Forzar nueva consulta')

    def test_consultar_sin_autorizacion_no_crea_nueva_auditoria(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_datacredito)

        respuesta = self.client.post(
            reverse('gestion:prestadores_riesgo_datacredito', args=[auditoria.id]),
            {'modo_datacredito': 'CONSULTAR_SI_NO_EXISTE'},
        )

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)

    @override_settings(
        DATACREDITO_ENVIRONMENT='uat',
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-v1',
        DATACREDITO_AUTHORIZATION_TEXT='Texto aprobado para consulta DataCredito de prestadores.',
        DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS='123456789',
    )
    def test_registro_uat_desde_vista_requiere_permiso_y_crea_autorizacion(self):
        auditoria = self._crear_auditoria()
        self.usuario_datacredito.user_permissions.add(
            Permission.objects.get(codename='can_register_uat_datacredito_authorization'),
        )
        self.client.force_login(self.usuario_datacredito)

        respuesta = self.client.post(
            reverse('gestion:prestadores_riesgo_datacredito', args=[auditoria.id]),
            {
                'accion': 'registrar_autorizacion_uat',
                'justificacion_autorizacion': 'Prueba UAT controlada.',
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        autorizacion = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual(autorizacion.source, AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT)
        self.assertEqual(autorizacion.solicitud, auditoria.solicitud)

    def test_forzar_consulta_exige_permiso(self):
        auditoria = self._crear_auditoria()
        self.client.force_login(self.usuario_datacredito)

        respuesta = self.client.post(
            reverse('gestion:prestadores_riesgo_datacredito', args=[auditoria.id]),
            {'modo_datacredito': 'FORZAR_CONSULTA', 'justificacion': 'Revision controlada'},
        )

        self.assertEqual(respuesta.status_code, 403)

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from contractors.models import (
    ConfiguracionPortalContratistas,
    ContractorApplication,
    ContractorApplicationDocument,
    InformacionLaboralSolicitudContratista,
    PredecisionPrestadorAudit,
)
from contractors.services.originacion import (
    ErrorOriginacionPrestador,
    originar_credito_prestador_desde_auditoria,
)
from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    HistorialEstado,
    HistorialPago,
    Pagare,
)


User = get_user_model()


class OriginacionPrestadoresTests(TestCase):
    def setUp(self):
        self.usuario_cliente = User.objects.create_user(
            username='cliente-prestador',
            email='cliente-prestador@example.com',
            password='x',
        )
        self.usuario_staff = User.objects.create_user(
            username='staff-originador',
            email='staff-originador@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_sin_permiso = User.objects.create_user(
            username='staff-sin-originar',
            email='staff-sin-originar@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_staff.user_permissions.add(
            Permission.objects.get(codename='can_view_contractor_risk_queue'),
            Permission.objects.get(codename='can_originate_contractor_credit'),
        )
        self.usuario_sin_permiso.user_permissions.add(
            Permission.objects.get(codename='can_view_contractor_risk_queue'),
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
            nombre='Empresa Convenio Originacion',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = self._crear_solicitud()
        self._crear_datos_contractuales(self.solicitud)
        self._crear_documentos(self.solicitud)
        self.auditoria = self._crear_auditoria(self.solicitud)

    def _crear_solicitud(self, documento='123456789'):
        return ContractorApplication.objects.create(
            configuracion_portal=self.configuracion_portal,
            usuario=self.usuario_cliente,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('3000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('320000.00'),
            simulation_payload={'origen': 'test'},
            document_type='CC',
            document_number=documento,
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email=f'{documento}@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )

    def _crear_datos_contractuales(self, solicitud):
        hoy = timezone.localdate()
        return InformacionLaboralSolicitudContratista.objects.create(
            solicitud=solicitud,
            empresa=self.empresa,
            cargo='Contratista comercial',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=hoy - relativedelta(months=1),
            fecha_fin_contrato=hoy + relativedelta(months=12),
            valor_total_contrato=Decimal('20000000.00'),
            valor_pagado_contrato=Decimal('5000000.00'),
            valor_pendiente_cobrar=Decimal('15000000.00'),
            empresa_contratante_nombre='Empresa Convenio Originacion',
        )

    def _crear_documentos(self, solicitud):
        documentos = {
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL: 'cedula-frontal.jpg',
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO: 'cedula-reverso.jpg',
            ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL: 'contrato.pdf',
            ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO: 'certificado-bancario.pdf',
        }
        for tipo, nombre in documentos.items():
            content_type = 'application/pdf' if nombre.endswith('.pdf') else 'image/jpeg'
            ContractorApplicationDocument.objects.create(
                application=solicitud,
                document_type=tipo,
                file=f'contractors/applications/documents/{nombre}',
                original_filename=nombre,
                content_type=content_type,
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )

    def _crear_auditoria(
        self,
        solicitud,
        decision='PREAPROBADO_READ_ONLY',
        eligible=True,
        monto=Decimal('2500000.00'),
        plazo=10,
    ):
        return PredecisionPrestadorAudit.objects.create(
            solicitud=solicitud,
            usuario=self.usuario_staff,
            escenario_credito=solicitud.escenario_credito,
            decision=decision,
            eligible=eligible,
            requiere_revision_manual=decision == 'REQUIERE_REVISION_MANUAL',
            monto_maximo_sugerido=monto,
            plazo_maximo_sugerido=plazo,
            score_status='EVALUADO',
            score_final=Decimal('820.00'),
            score_banda='PREMIUM',
            score_version_configuracion='prestadores_score_v1',
            datacredito_status='DISPONIBLE',
            datacredito_fuente='mock',
            capacidad_status='APROBADO',
            riesgo_status='APROBADO',
            bloqueos=[],
            advertencias=[],
            razones=[],
            resultado_sanitizado={'decision': decision},
        )

    def test_no_permite_originar_auditoria_incompleta_revision_manual_o_bloqueada(self):
        casos = [
            ('INCOMPLETO', False),
            ('REQUIERE_REVISION_MANUAL', False),
            ('BLOQUEADO_READ_ONLY', False),
        ]
        for indice, (decision, eligible) in enumerate(casos):
            solicitud = self._crear_solicitud(documento=f'90000000{indice}')
            self._crear_datos_contractuales(solicitud)
            self._crear_documentos(solicitud)
            auditoria = self._crear_auditoria(solicitud, decision=decision, eligible=eligible)
            with self.assertRaises(ErrorOriginacionPrestador):
                originar_credito_prestador_desde_auditoria(auditoria, usuario=self.usuario_staff)

        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    def test_no_permite_originar_sin_permiso(self):
        with self.assertRaises(PermissionDenied):
            originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_sin_permiso)

    def test_no_permite_originar_si_solicitud_ya_convertida(self):
        self.solicitud.status = ContractorApplication.Estado.CONVERTIDA
        self.solicitud.save(update_fields=['status'])

        with self.assertRaises(ErrorOriginacionPrestador):
            originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

    def test_crea_credito_libranza_en_revision_y_marca_solicitud_convertida(self):
        resultado = originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        credito = resultado.credito
        self.solicitud.refresh_from_db()
        self.assertEqual(credito.linea, Credito.LineaCredito.LIBRANZA)
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertEqual(credito.monto_solicitado, Decimal('2500000.00'))
        self.assertEqual(credito.plazo_solicitado, 10)
        self.assertEqual(credito.monto_aprobado, Decimal('2500000.00'))
        self.assertEqual(credito.plazo, 10)
        self.assertEqual(self.solicitud.status, ContractorApplication.Estado.CONVERTIDA)
        self.assertEqual(self.solicitud.credito, credito)
        self.assertTrue(CreditoLibranza.objects.filter(credito=credito).exists())
        self.assertEqual(HistorialEstado.objects.filter(credito=credito).count(), 1)

    def test_no_crea_historial_pago_pagare_cuotas_no_desembolsa_ni_activa(self):
        resultado = originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        self.assertFalse(resultado.credito.documento_enviado)
        self.assertIsNone(resultado.credito.fecha_desembolso)
        self.assertEqual(resultado.credito.estado, Credito.EstadoCredito.EN_REVISION)

    def test_doble_post_no_duplica_credito(self):
        originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        with self.assertRaises(ErrorOriginacionPrestador):
            originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)

    def test_monto_y_plazo_respetan_minimo_entre_solicitud_y_auditoria(self):
        auditoria = self._crear_auditoria(
            self.solicitud,
            monto=Decimal('3500000.00'),
            plazo=18,
        )

        resultado = originar_credito_prestador_desde_auditoria(auditoria, usuario=self.usuario_staff)

        self.assertEqual(resultado.credito.monto_solicitado, Decimal('3000000.00'))
        self.assertEqual(resultado.credito.plazo_solicitado, 12)

    def test_boton_solo_aparece_cuando_aplica(self):
        self.client.force_login(self.usuario_staff)

        respuesta = self.client.get(reverse('gestion:prestadores_riesgo_detalle', args=[self.auditoria.id]))

        self.assertContains(respuesta, 'Originar en revision')

        auditoria_revision = self._crear_auditoria(
            self._crear_solicitud(documento='777777777'),
            decision='REQUIERE_REVISION_MANUAL',
            eligible=False,
        )
        respuesta_revision = self.client.get(reverse('gestion:prestadores_riesgo_detalle', args=[auditoria_revision.id]))
        self.assertNotContains(respuesta_revision, 'Originar en revision')

        self.client.force_login(self.usuario_sin_permiso)
        respuesta_sin_permiso = self.client.get(reverse('gestion:prestadores_riesgo_detalle', args=[self.auditoria.id]))
        self.assertNotContains(respuesta_sin_permiso, 'Originar en revision')

    def test_post_vista_origina_y_no_duplica_en_segundo_post(self):
        self.client.force_login(self.usuario_staff)
        url = reverse('gestion:prestadores_riesgo_detalle', args=[self.auditoria.id])

        primera = self.client.post(url, follow=True)
        segunda = self.client.post(url, follow=True)

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertContains(primera, 'originado en revision')
        self.assertContains(segunda, 'solicitud_ya_convertida')
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)

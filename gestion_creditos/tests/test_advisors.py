from datetime import date
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.admin import EmpresaAdmin, EmpresaAdminForm
from gestion_creditos.models import AsesorComercial, Credito, CreditoLibranza, Empresa, HistorialEstado
from gestion_creditos.services.advisors import get_asesor_performance_snapshot
from gestion_creditos.services.dashboard_metrics import get_admin_dashboard_context


User = get_user_model()


class AdvisorReferralTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff-ref',
            email='staff-ref@aprobado.test',
            password='123456',
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username='cliente-ref',
            email='cliente-ref@aprobado.test',
            password='123456',
        )
        self.factory = RequestFactory()

    def _crear_credito_libranza(self, empresa, numero, estado, monto, saldo):
        credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            numero_credito=numero,
            monto_solicitado=monto,
            monto_aprobado=monto,
            plazo_solicitado=6,
            plazo=6,
            valor_cuota=Decimal('250000.00'),
            saldo_pendiente=saldo,
            capital_pendiente=saldo,
            total_a_pagar=monto + Decimal('200000.00'),
            comision=Decimal('100000.00'),
            iva_comision=Decimal('19000.00'),
            fecha_desembolso=timezone.now(),
            fecha_proximo_pago=date(2026, 5, 1),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa,
            nombres='Cliente',
            apellidos='Referido',
            cedula='12345678',
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico='cliente-ref@aprobado.test',
            cedula_frontal='test/front.jpg',
            cedula_trasera='test/back.jpg',
            certificado_bancario='test/cert.pdf',
        )
        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            estado_nuevo=estado,
            comprobante_pago='comprobantes/test.pdf',
        )
        return credito

    def test_empresa_admin_form_crea_vinculo_con_asesor(self):
        form = EmpresaAdminForm(data={
            'nombre': 'Empresa Referida',
            'slug': 'empresa-referida',
            'tipo_empresa': Empresa.TipoEmpresa.CONVENIO,
            'convenio_activo': 'on',
            'marketplace_fee_percent': '10.00',
            'fue_referida': 'on',
            'asesor_nombre': 'Laura Mejia',
            'asesor_cedula': '11223344',
        })
        self.assertTrue(form.is_valid(), form.errors)
        empresa = form.save()

        self.assertTrue(empresa.fue_referida)
        self.assertEqual(empresa.asesor_comercial.cedula, '11223344')
        self.assertEqual(empresa.asesor_comercial.nombre, 'LAURA MEJIA')

    def test_service_resume_metrico_por_asesor(self):
        asesor = AsesorComercial.objects.create(nombre='Laura Mejia', cedula='11223344')
        empresa = Empresa.objects.create(
            nombre='Empresa Uno',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor,
        )
        credito_activo = self._crear_credito_libranza(
            empresa,
            'CR-ASE-0001',
            Credito.EstadoCredito.ACTIVO,
            Decimal('2000000.00'),
            Decimal('1500000.00'),
        )
        credito_pagado = self._crear_credito_libranza(
            empresa,
            'CR-ASE-0002',
            Credito.EstadoCredito.PAGADO,
            Decimal('3000000.00'),
            Decimal('0.00'),
        )
        credito_activo.reestructuraciones.create(
            monto_abonado=Decimal('300000.00'),
            tipo_abono='MAYOR',
            plan_anterior=[],
            plan_nuevo=[],
            saldo_pendiente_anterior=Decimal('1800000.00'),
            saldo_pendiente_nuevo=Decimal('1500000.00'),
            capital_pendiente_anterior=Decimal('1800000.00'),
            capital_pendiente_nuevo=Decimal('1500000.00'),
            plazo_restante_anterior=6,
            plazo_restante_nuevo=5,
            cuota_mensual_nueva=Decimal('240000.00'),
            ahorro_intereses=Decimal('50000.00'),
            credito=credito_activo,
        )

        summary = get_asesor_performance_snapshot(asesor)

        self.assertEqual(summary['empresas_count'], 1)
        self.assertEqual(summary['total_creditos'], 2)
        self.assertEqual(summary['creditos_activos'], 1)
        self.assertEqual(summary['creditos_pagados'], 1)
        self.assertEqual(summary['monto_colocado'], Decimal('5000000.00'))
        self.assertEqual(summary['comision_acumulada'], Decimal('50000.00'))
        self.assertEqual(summary['saldo_cartera'], Decimal('1500000.00'))

    def test_dashboard_admin_aplica_filtro_por_asesor(self):
        asesor_a = AsesorComercial.objects.create(nombre='Laura Mejia', cedula='11223344')
        asesor_b = AsesorComercial.objects.create(nombre='Carlos Ruiz', cedula='99887766')
        empresa_a = Empresa.objects.create(
            nombre='Empresa A',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor_a,
        )
        empresa_b = Empresa.objects.create(
            nombre='Empresa B',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor_b,
        )
        self._crear_credito_libranza(empresa_a, 'CR-ASE-1001', Credito.EstadoCredito.ACTIVO, Decimal('1000000.00'), Decimal('700000.00'))
        self._crear_credito_libranza(empresa_b, 'CR-ASE-1002', Credito.EstadoCredito.ACTIVO, Decimal('500000.00'), Decimal('300000.00'))

        request = self.factory.get('/gestion/', {'asesor': str(asesor_a.id)})
        context = get_admin_dashboard_context(self.staff, request=request)

        self.assertEqual(context['selected_asesor'], asesor_a)
        self.assertEqual(context['total_creditos'], 1)
        self.assertIn({'id': asesor_a.id, 'nombre': asesor_a.nombre}, context['asesores_choices'])

    def test_panel_asesor_usa_login_propietario_y_carga_dashboard(self):
        asesor_user = User.objects.create_user(
            username='asesor-demo',
            email='asesor@aprobado.test',
            password='123456',
        )
        asesor = AsesorComercial.objects.create(
            nombre='Laura Mejia',
            cedula='11223344',
            usuario=asesor_user,
        )
        empresa = Empresa.objects.create(
            nombre='Empresa Login',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor,
        )
        self._crear_credito_libranza(empresa, 'CR-ASE-3001', Credito.EstadoCredito.ACTIVO, Decimal('900000.00'), Decimal('400000.00'))

        redirect_response = self.client.get(reverse('asesores:dashboard'))
        self.assertRedirects(redirect_response, f"{reverse('asesores:login')}?next={reverse('asesores:dashboard')}")

        login_response = self.client.get(reverse('asesores:login'))
        self.assertContains(login_response, 'Acceso ejecutivo')

        self.client.login(username='asesor-demo', password='123456')
        dashboard_response = self.client.get(reverse('asesores:dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'Panel del Ejecutivo')
        self.assertContains(dashboard_response, 'Empresa Login')

    def test_panel_ejecutivo_filtra_empresa_y_oculta_estados_internos(self):
        asesor_user = User.objects.create_user(
            username='ejecutivo-filtro',
            email='ejecutivo-filtro@aprobado.test',
            password='123456',
        )
        asesor = AsesorComercial.objects.create(
            nombre='Ejecutivo Filtro',
            cedula='44556677',
            usuario=asesor_user,
        )
        empresa_a = Empresa.objects.create(
            nombre='Empresa Activa',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor,
        )
        empresa_b = Empresa.objects.create(
            nombre='Empresa Interna',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            asesor_comercial=asesor,
        )
        self._crear_credito_libranza(empresa_a, 'CR-EJE-0001', Credito.EstadoCredito.ACTIVO, Decimal('1200000.00'), Decimal('800000.00'))
        self._crear_credito_libranza(empresa_b, 'CR-EJE-0002', Credito.EstadoCredito.PENDIENTE_FIRMA, Decimal('900000.00'), Decimal('900000.00'))

        self.client.login(username='ejecutivo-filtro', password='123456')
        response = self.client.get(reverse('asesores:dashboard'), {'empresa': empresa_a.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Empresa Activa')
        self.assertNotContains(response, 'CR-EJE-0002')
        self.assertContains(response, '$1.200.000')


class EmpresaAdminConfigTests(TestCase):
    def test_empresa_admin_usa_form_de_referidos(self):
        model_admin = EmpresaAdmin(Empresa, AdminSite())
        self.assertIs(model_admin.form, EmpresaAdminForm)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test',
    PRIMARY_DOMAIN_HOST='aprobado.test',
)
class ExecutiveAdminActivationTests(TestCase):
    def test_creacion_desde_admin_envia_activacion(self):
        staff = User.objects.create_user(
            username='staff-ejecutivo',
            email='staff-ejecutivo@aprobado.test',
            password='123456',
            is_staff=True,
        )
        asesor = AsesorComercial(
            nombre='Ejecutivo Admin',
            cedula='77889900',
            email='ejecutivo-admin@aprobado.test',
            telefono='3005550000',
        )
        request = RequestFactory().post('/admin/gestion_creditos/asesorcomercial/add/')
        request.user = staff
        setattr(request, 'session', self.client.session)
        setattr(request, '_messages', FallbackStorage(request))
        model_admin = __import__('gestion_creditos.admin', fromlist=['AsesorComercialAdmin']).AsesorComercialAdmin(AsesorComercial, AdminSite())

        model_admin.save_model(request, asesor, form=None, change=False)
        asesor.refresh_from_db()

        self.assertIsNotNone(asesor.usuario)
        self.assertFalse(asesor.usuario.is_active)
        self.assertFalse(asesor.usuario.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/asesores/activar/', mail.outbox[0].body)

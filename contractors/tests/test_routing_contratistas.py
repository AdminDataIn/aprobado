from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from contractors.models import ConfiguracionPortalContratistas
from gestion_creditos.models import Credito, CreditoLibranza


@override_settings(
    PRIMARY_DOMAIN_HOST='aprobado.com.co',
    CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
    ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
)
class RoutingContratistasTests(TestCase):
    def setUp(self):
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Prestadores de Servicios Aprobado',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            color_primario='#112233',
            color_secundario='#445566',
            texto_landing='Portal contratistas.',
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('10000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )

    def test_raiz_contratistas_redirige_a_solicitar(self):
        response = self.client.get('/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_solicitar_sin_login_redirige_a_login(self):
        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/login/?next=/solicitar/')

    def test_solicitar_autenticado_muestra_formulario(self):
        usuario = get_user_model().objects.create_user(
            username='contratista',
            email='contratista@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'contractors/solicitud_contratista.html')
        self.assertContains(response, 'Solicita tu Crédito para Prestadores de Servicios')
        self.assertNotContains(response, 'Por que elegir nuestro')
        self.assertNotContains(response, 'Preguntas frecuentes')

    def test_navbar_contratistas_solo_muestra_solicitar_mi_credito_y_sesion(self):
        usuario = get_user_model().objects.create_user(
            username='contratista-nav',
            email='contratista-nav@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Solicitar')
        self.assertContains(response, 'Mi cr')
        self.assertContains(response, 'dropdown menu-us contractors-user-menu')
        self.assertContains(response, 'contractors-logout-form')
        self.assertContains(response, 'contratista-nav@example.com')
        self.assertContains(response, 'Cerrar sesi')
        self.assertNotContains(response, 'Inicio')
        self.assertNotContains(response, 'Requisitos')
        self.assertNotContains(response, 'Como funciona')
        self.assertNotContains(response, 'FAQ')

    def test_navbar_contratistas_no_autenticado_muestra_iniciar_sesion(self):
        response = self.client.get('/login/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inicia sesion en Prestadores de Servicios Aprobado')

    def test_footer_contratistas_claro_y_minimo(self):
        usuario = get_user_model().objects.create_user(
            username='contratista-footer',
            email='contratista-footer@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        response = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'contractors-footer-claro')
        self.assertContains(response, 'logo-dark.png')
        self.assertContains(response, 'Aprobado Libranza ofrece')
        self.assertContains(response, 'Info@aprobado.com.co')
        self.assertContains(response, '+57 315 856 2162')
        self.assertContains(response, 'aria-label="WhatsApp Aprobado"')
        self.assertContains(response, 'aria-label="Facebook Aprobado"')
        self.assertContains(response, 'aria-label="Instagram Aprobado"')
        self.assertNotContains(response, 'footer-title')
        self.assertNotContains(response, 'footer-links')
        self.assertNotContains(response, 'Preguntas frecuentes')
        self.assertNotContains(response, 'Acceso pagadores')

    def test_logout_contratistas_cierra_sesion(self):
        usuario = get_user_model().objects.create_user(
            username='contratista-logout',
            email='contratista-logout@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        response = self.client.post('/logout/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/login/')
        response_solicitar = self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')
        self.assertEqual(response_solicitar.status_code, 302)
        self.assertEqual(response_solicitar['Location'], '/login/?next=/solicitar/')

    def test_mi_credito_usa_dashboard_libranza_sin_url_localhost_rota(self):
        usuario = get_user_model().objects.create_user(
            username='contratista-mi-credito',
            email='contratista-mi-credito@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'usuariocreditos/sin_creditos.html')
        self.assertNotIn('localhost/libranza/mi-credito', response.content.decode('utf-8', errors='ignore'))

    def test_libranza_contiene_cta_contratistas(self):
        response = self.client.get('/libranza/', HTTP_HOST='aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Prestador de Servicios</span>?')
        self.assertContains(response, 'iniciar tu solicitud con contrato vigente')
        self.assertContains(response, 'Carga tus documentos')
        self.assertContains(response, 'Validamos tu contrato')
        self.assertContains(response, 'Simula con tus datos')
        self.assertContains(response, 'Solicitar como Prestador de Servicios')
        self.assertContains(response, 'https://contratistas.aprobado.com.co/solicitar/')
        self.assertNotContains(response, 'Simula tu adelanto')
        self.assertNotContains(response, 'Antes de simular')
        self.assertNotContains(response, 'Registro + documentos')

    def test_login_contratistas_carga_sin_namespace_libranza(self):
        response = self.client.get('/login/?next=/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inicia sesion en Prestadores de Servicios Aprobado')
        self.assertNotContains(response, 'NoReverseMatch')

    def test_politica_privacidad_contratistas_usa_layout_legal_completo(self):
        response = self.client.get('/politica-de-privacidad/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pol')
        self.assertContains(response, 'Ultima actualizacion')
        self.assertContains(response, 'legal-intro')
        self.assertContains(response, 'legal-card contractors-legal-sections')
        self.assertContains(response, 'section-number')
        self.assertContains(response, 'Responsable del tratamiento')
        self.assertContains(response, 'Derechos del titular')
        self.assertContains(response, 'Canales de atenci')
        self.assertContains(response, 'Seguridad de la informaci')
        self.assertContains(response, 'Info@aprobado.com.co')
        self.assertGreater(len(response.content), 9000)
        self.assertNotContains(response, 'placeholder')
        self.assertNotContains(response, 'form-sidebar')

    def test_terminos_contratistas_usa_layout_legal_completo(self):
        response = self.client.get('/terminos-y-condiciones/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'T')
        self.assertContains(response, 'Ultima actualizacion')
        self.assertContains(response, 'legal-intro')
        self.assertContains(response, 'legal-card contractors-legal-sections')
        self.assertContains(response, 'section-number')
        self.assertContains(response, 'Objeto del portal')
        self.assertContains(response, 'Registro de usuario')
        self.assertContains(response, 'Carga documental')
        self.assertContains(response, 'Simulaci')
        self.assertContains(response, 'Protecci')
        self.assertGreater(len(response.content), 9000)
        self.assertNotContains(response, 'placeholder')
        self.assertNotContains(response, 'form-sidebar')

    def test_no_crea_creditos_en_rutas_publicas(self):
        usuario = get_user_model().objects.create_user(
            username='contratista-no-creditos',
            email='contratista-no-creditos@example.com',
            password='password-test',
        )
        self.client.force_login(usuario)

        self.client.get('/', HTTP_HOST='contratistas.aprobado.com.co')
        self.client.get('/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')
        self.client.get('/mi-credito/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

from urllib.parse import parse_qs, urlparse

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings


class AutenticacionPrestadoresTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        self.site, _ = Site.objects.update_or_create(
            pk=1,
            defaults={
                'domain': 'aprobado.com.co',
                'name': 'Aprobado',
            },
        )
        self.google_app = SocialApp.objects.create(
            provider='google',
            name='Google pruebas Prestadores',
            client_id='google-client-id-de-prueba',
            secret='google-secret-de-prueba',
        )
        self.google_app.sites.add(self.site)
        self.usuario = get_user_model().objects.create_user(
            username='auth-prestador',
            email='auth-prestador@example.com',
            password='Clave-segura-123',
        )

    def test_solicitar_sin_sesion_redirige_al_login_con_next(self):
        response = self.client.get('/solicitar/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            '/auth/login/?next=/solicitar/',
        )

    def test_login_muestra_las_cuatro_opciones_de_acceso(self):
        response = self.client.get(
            '/accounts/login/?next=/solicitar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continuar con Google')
        self.assertContains(response, 'data-auth-google')
        self.assertContains(response, 'btn-social-account is-google')
        self.assertContains(response, '/static/images/google-g.svg')
        self.assertContains(response, 'Iniciar sesión')
        self.assertContains(response, 'Crear cuenta')
        self.assertContains(response, 'Recuperar acceso')
        self.assertContains(response, '/accounts/google/login/')
        self.assertContains(response, 'next=%2Fsolicitar%2F')

    def test_login_oculta_google_si_no_hay_socialapp_configurada(self):
        self.google_app.delete()

        response = self.client.get(
            '/accounts/login/?next=/solicitar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-auth-google')
        self.assertNotContains(response, 'Continuar con Google')

    @override_settings(
        CONTRACTORS_PORTAL_HOSTS=['contratistas.aprobado.com.co'],
        ACCOUNT_DEFAULT_HTTP_PROTOCOL='https',
    )
    def test_google_genera_callback_https_para_host_productivo(self):
        response = self.client.post(
            '/accounts/google/login/?process=login&next=/solicitar/',
            HTTP_HOST='contratistas.aprobado.com.co',
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertEqual(response.status_code, 302)
        authorization_url = urlparse(response['Location'])
        query = parse_qs(authorization_url.query)
        self.assertEqual(
            f'{authorization_url.scheme}://{authorization_url.netloc}{authorization_url.path}',
            'https://accounts.google.com/o/oauth2/v2/auth',
        )
        self.assertEqual(
            query['redirect_uri'],
            ['https://contratistas.aprobado.com.co/accounts/google/login/callback/'],
        )
        self.assertFalse(query['redirect_uri'][0].startswith('http://'))
        self.assertTrue(query.get('state'))

    def test_signup_es_accesible_y_conserva_contexto_prestadores(self):
        response = self.client.get(
            '/accounts/signup/?next=/solicitar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'REGISTRO PRESTADORES')
        self.assertContains(response, 'Crea tu cuenta de Prestadores de Servicios')
        self.assertContains(response, 'name="next" value="/solicitar/"', html=False)
        self.assertContains(response, 'auth-body auth-flow-shell')

    def test_login_exitoso_regresa_a_solicitar(self):
        response = self.client.post(
            '/accounts/login/?next=/solicitar/',
            {
                'login': self.usuario.email,
                'password': 'Clave-segura-123',
                'next': '/solicitar/',
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_registro_exitoso_regresa_a_solicitar(self):
        response = self.client.post(
            '/accounts/signup/?next=/solicitar/',
            {
                'email': 'nuevo-prestador@example.com',
                'password1': 'Otra-clave-segura-123',
                'password2': 'Otra-clave-segura-123',
                'next': '/solicitar/',
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')
        self.assertTrue(
            get_user_model().objects.filter(email='nuevo-prestador@example.com').exists()
        )

    def test_next_externo_no_se_expone_ni_se_usa(self):
        response = self.client.get(
            '/accounts/login/?next=https://evil.example/robo',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next" value="/solicitar/"', html=False)
        self.assertNotContains(response, 'evil.example')

    def test_password_reset_usa_la_misma_estructura_visual(self):
        response = self.client.get(
            '/accounts/password/reset/?next=/solicitar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'auth-body auth-flow-shell')
        self.assertContains(response, 'hero-section-account')
        self.assertContains(response, 'div-card-agent-account')
        self.assertContains(response, 'Restablece tu contraseña')
        self.assertTemplateUsed(response, 'account/_base.html')

    def test_otro_host_conserva_el_copy_general(self):
        response = self.client.get('/accounts/login/', HTTP_HOST='localhost')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acceso general')
        self.assertNotContains(response, 'ACCESO PRESTADORES')
        self.assertNotContains(response, 'Volver al portal de Prestadores')

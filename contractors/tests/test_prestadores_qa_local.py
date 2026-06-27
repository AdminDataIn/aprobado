from decimal import Decimal
from io import StringIO

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase, override_settings

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionPortalContratistas,
)
from contractors.selectors import obtener_configuracion_portal_contratistas_por_host
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialPago


class PrestadoresQALocalTests(TestCase):
    def _crear_configuracion(self, host='contratistas.localhost', slug='prestadores'):
        return ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Prestadores de Servicios',
            host=host,
            slug=slug,
            activo=True,
            color_primario='#16b8d8',
            color_secundario='#07172b',
            texto_landing='Prestadores de Servicios.',
            monto_minimo=Decimal('300000.00'),
            monto_maximo=Decimal('10000000.00'),
            plazo_minimo_meses=1,
            plazo_maximo_meses=8,
            tasa_mensual=Decimal('2.2000'),
            tasa_comision=Decimal('10.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )

    @override_settings(DEBUG=True)
    def test_lookup_encuentra_configuracion_por_host_exacto_con_puerto(self):
        configuracion = self._crear_configuracion(host='contratistas.localhost:8000')

        resultado = obtener_configuracion_portal_contratistas_por_host('contratistas.localhost:8000')

        self.assertEqual(resultado, configuracion)

    @override_settings(DEBUG=True)
    def test_lookup_encuentra_configuracion_por_host_sin_puerto(self):
        configuracion = self._crear_configuracion(host='contratistas.localhost')

        resultado = obtener_configuracion_portal_contratistas_por_host('contratistas.localhost:8000')

        self.assertEqual(resultado, configuracion)

    @override_settings(DEBUG=True)
    def test_lookup_debug_usa_fallback_si_solo_hay_un_portal_activo(self):
        configuracion = self._crear_configuracion(host='qa-prestadores.localhost', slug='qa-prestadores')

        resultado = obtener_configuracion_portal_contratistas_por_host('contratistas.localhost:8002')

        self.assertEqual(resultado, configuracion)

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_error_sin_configuracion_incluye_host_en_debug(self):
        response = self.client.get('/', HTTP_HOST='contratistas.localhost:8000')

        self.assertEqual(response.status_code, 404)
        contenido = response.content.decode('utf-8', errors='ignore')
        self.assertIn('contratistas.localhost:8000', contenido)
        self.assertIn('seed_prestadores_qa_local', contenido)

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_login_prestadores_carga_sin_google_socialapp(self):
        self._crear_configuracion(host='contratistas.aprobado.com.co')

        response = self.client.get('/login/?next=/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continuar con correo')
        self.assertNotContains(response, 'Continuar con Google')

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_login_prestadores_muestra_google_si_socialapp_existe(self):
        self._crear_configuracion(host='contratistas.aprobado.com.co')
        site = Site.objects.get_current()
        site.domain = 'contratistas.aprobado.com.co'
        site.name = 'Prestadores'
        site.save()
        app = SocialApp.objects.create(
            provider='google',
            name='Google Prestadores',
            client_id='client-id-demo',
            secret='secret-demo',
        )
        app.sites.add(site)

        response = self.client.get('/login/?next=/solicitar/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continuar con Google')
        self.assertContains(response, '/accounts/google/login/?process=login&amp;next=%2Fsolicitar%2F')

    @override_settings(DEBUG=True, ALLOWED_HOSTS=['.localhost', 'testserver'])
    def test_login_prestadores_local_conserva_next_google_si_socialapp_existe(self):
        self._crear_configuracion(host='contratistas.localhost')
        site = Site.objects.get_current()
        site.domain = 'contratistas.localhost:8000'
        site.name = 'Prestadores Local'
        site.save()
        app = SocialApp.objects.create(
            provider='google',
            name='Google Prestadores Local',
            client_id='client-id-local',
            secret='secret-local',
        )
        app.sites.add(site)

        response = self.client.get('/login/?next=/solicitar/', HTTP_HOST='contratistas.localhost:8000')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continuar con Google')
        self.assertContains(response, 'next=%2Fsolicitar%2F')

    @override_settings(DEBUG=True)
    def test_seed_local_es_idempotente_y_crea_datos_minimos(self):
        salida_uno = StringIO()
        salida_dos = StringIO()

        call_command(
            'seed_prestadores_qa_local',
            '--host',
            'contratistas.localhost:8000',
            stdout=salida_uno,
        )
        call_command(
            'seed_prestadores_qa_local',
            '--host',
            'contratistas.localhost:8000',
            stdout=salida_dos,
        )

        self.assertEqual(
            ConfiguracionPortalContratistas.objects.filter(host='contratistas.localhost:8000').count(),
            1,
        )
        self.assertTrue(Empresa.objects.filter(nombre='Empresa Demo Prestadores SAS', convenio_activo=True).exists())
        self.assertIn('solicitante@aprobado.local', salida_dos.getvalue())
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 0)

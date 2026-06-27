from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from gestion_creditos.services.dashboard_metrics import get_admin_dashboard_context
from gestion_creditos.services.empresa_geografia import (
    normalizar_ciudad,
    normalizar_departamento,
    normalizar_departamento_mapa,
    normalizar_municipio,
    obtener_empresas_aliadas_visibles,
    obtener_presencia_empresas,
)


User = get_user_model()


class EmpresaGeografiaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username='geo-user', password='123456')

    def test_empresa_acepta_geografia_vacia(self):
        empresa = Empresa.objects.create(nombre='Empresa Sin Geografia')

        self.assertIsNone(empresa.pais)
        self.assertIsNone(empresa.departamento)
        self.assertIsNone(empresa.municipio)
        self.assertIsNone(empresa.ciudad)
        self.assertIsNone(empresa.direccion_principal)
        self.assertIsNone(empresa.latitud)
        self.assertIsNone(empresa.longitud)

    def test_normalizacion_geografica_limpia_espacios_y_vacios(self):
        self.assertEqual(normalizar_departamento('  meta  '), 'Meta')
        self.assertEqual(normalizar_municipio(' villavicencio  '), 'Villavicencio')
        self.assertEqual(normalizar_ciudad(' bogota d.c. '), 'Bogota D.C.')
        self.assertIsNone(normalizar_departamento(''))
        self.assertIsNone(normalizar_departamento('Sin departamento registrado'))
        self.assertEqual(normalizar_departamento_mapa('Antioquia'), 'antioquia')
        self.assertEqual(normalizar_departamento_mapa('Bogota'), 'bogota-d-c')

    def test_presencia_agrupa_solo_empresas_con_ubicacion_real(self):
        Empresa.objects.create(nombre='Empresa Sin Ubicacion')
        Empresa.objects.create(nombre='Empresa Placeholder', departamento='Sin departamento registrado')
        Empresa.objects.create(
            nombre='Empresa Meta Uno',
            departamento=' meta ',
            municipio=' villavicencio ',
            ciudad=' villavicencio ',
        )
        Empresa.objects.create(
            nombre='Empresa Meta Dos',
            departamento='Meta',
            municipio='Villavicencio',
            ciudad='Villavicencio',
        )

        presencia = obtener_presencia_empresas()

        self.assertEqual(presencia['total_empresas'], 4)
        self.assertEqual(presencia['con_ubicacion_registrada'], 2)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 2)
        self.assertEqual(len(presencia['ubicaciones']), 1)
        ubicacion = presencia['ubicaciones'][0]
        self.assertEqual(ubicacion['departamento'], 'Meta')
        self.assertEqual(ubicacion['municipio'], 'Villavicencio')
        self.assertEqual(ubicacion['ciudad'], 'Villavicencio')
        self.assertEqual(ubicacion['empresas'], 2)
        self.assertEqual(ubicacion['posicion_mapa']['fuente'], 'centroide_interno')
        self.assertIn('x', ubicacion['posicion_mapa'])
        self.assertIn('y', ubicacion['posicion_mapa'])
        self.assertEqual(ubicacion['coordenadas_mapa']['fuente'], 'centroide_interno')
        self.assertEqual(len(presencia['mapa_ubicaciones']), 1)
        self.assertEqual(presencia['departamentos_mapa'], ['Meta'])

    def test_presencia_usa_coordenadas_registradas_si_existen(self):
        Empresa.objects.create(
            nombre='Empresa Coordenadas',
            departamento='Meta',
            municipio='Villavicencio',
            ciudad='Villavicencio',
            latitud=Decimal('4.1420'),
            longitud=Decimal('-73.6266'),
        )

        presencia = obtener_presencia_empresas()

        ubicacion = presencia['ubicaciones'][0]
        self.assertEqual(ubicacion['posicion_mapa']['fuente'], 'coordenadas_registradas')
        self.assertEqual(ubicacion['coordenadas_mapa']['fuente'], 'coordenadas_registradas')
        self.assertGreater(ubicacion['posicion_mapa']['x'], 0)
        self.assertGreater(ubicacion['posicion_mapa']['y'], 0)
        self.assertEqual(presencia['mapa_ubicaciones'][0]['latitud'], 4.142)
        self.assertEqual(presencia['mapa_ubicaciones'][0]['longitud'], -73.6266)

    def test_presencia_cuenta_creditos_activos_sin_exponer_pii(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Con Credito',
            departamento='Meta',
            municipio='Villavicencio',
            ciudad='Villavicencio',
        )
        self.crear_credito_libranza(empresa, estado=Credito.EstadoCredito.ACTIVO, cedula='123456789')
        self.crear_credito_libranza(empresa, estado=Credito.EstadoCredito.EN_MORA, cedula='987654321')
        self.crear_credito_libranza(empresa, estado=Credito.EstadoCredito.EN_REVISION, cedula='555555555')

        presencia = obtener_presencia_empresas(Empresa.objects.filter(pk=empresa.pk))

        ubicacion = presencia['ubicaciones'][0]
        self.assertEqual(ubicacion['creditos_activos'], 2)
        texto_resultado = str(ubicacion)
        self.assertNotIn('123456789', texto_resultado)
        self.assertNotIn('987654321', texto_resultado)
        self.assertNotIn('555555555', texto_resultado)
        self.assertNotIn('geo-user', texto_resultado)

    def test_dashboard_admin_expone_presencia_empresas_real(self):
        staff = User.objects.create_user(username='geo-staff', password='123456', is_staff=True)
        Empresa.objects.create(
            nombre='Empresa Dashboard Meta',
            convenio_activo=True,
            departamento='Meta',
            municipio='Villavicencio',
            ciudad='Villavicencio',
        )
        Empresa.objects.create(nombre='Empresa Dashboard Sin Ubicacion', convenio_activo=True)

        context = get_admin_dashboard_context(staff)

        self.assertIn('presencia_empresas', context)
        presencia = context['presencia_empresas']
        self.assertEqual(presencia['con_ubicacion_registrada'], 1)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 1)
        self.assertEqual(presencia['departamentos_con_presencia'][0]['nombre'], 'Meta')
        self.assertNotIn('Sin departamento registrado', str(presencia['departamentos_con_presencia']))

    def test_empresas_aliadas_visibles_no_incluyen_inactivas_ni_pii(self):
        Empresa.objects.create(
            nombre='Empresa Aliada Activa',
            convenio_activo=True,
            departamento='Meta',
            ciudad='Villavicencio',
            nit='900123456',
            correo_contacto='contacto@aliada.test',
            telefono_contacto='3001112233',
        )
        Empresa.objects.create(nombre='Empresa Inactiva', convenio_activo=False)

        aliadas = obtener_empresas_aliadas_visibles()

        self.assertEqual(len(aliadas), 1)
        self.assertEqual(aliadas[0]['nombre'], 'Empresa Aliada Activa')
        texto = str(aliadas)
        self.assertNotIn('Empresa Inactiva', texto)
        self.assertNotIn('900123456', texto)
        self.assertNotIn('contacto@aliada.test', texto)
        self.assertNotIn('3001112233', texto)

    def test_landing_muestra_presencia_y_respaldos_sin_datos_sensibles(self):
        Empresa.objects.create(
            nombre='Empresa Landing Aliada',
            convenio_activo=True,
            departamento='Meta',
            municipio='Villavicencio',
            ciudad='Villavicencio',
            nit='800555999',
            correo_contacto='privado@landing.test',
        )
        Empresa.objects.create(
            nombre='Empresa Antioquia Aliada',
            convenio_activo=True,
            departamento='Antioquia',
            municipio='Medellin',
            ciudad='Medellin',
        )
        Empresa.objects.create(
            nombre='Empresa Casanare Aliada',
            convenio_activo=True,
            departamento='Casanare',
            municipio='Yopal',
            ciudad='Yopal',
        )

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Presencia nacional')
        self.assertNotContains(response, 'impacto zonal')
        self.assertContains(response, 'Mapa real de Colombia')
        self.assertContains(response, 'data-geojson-local-url="/static/maps/colombia.geo.json"')
        self.assertContains(response, 'libranza-map-tooltip')
        self.assertContains(response, 'libranza-presence-map-data')
        self.assertContains(response, 'renderizarMapaPresenciaNacional')
        self.assertContains(response, 'dataset.rendered')
        self.assertContains(response, 'fetch(urlGeojson')
        self.assertContains(response, 'construirPathMapa')
        self.assertContains(response, 'proyectarCoordenadaMapa')
        self.assertNotContains(response, 'cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js')
        self.assertContains(response, 'libranza-map-point-group')
        self.assertContains(response, 'libranza-map-point-glow')
        self.assertContains(response, 'libranza-map-point')
        self.assertContains(response, 'libranza-map-department')
        self.assertContains(response, 'libranza-map-label')
        self.assertContains(response, 'Fuente cartográfica local')
        self.assertNotContains(response, '>M</text>')
        self.assertNotContains(response, '>B</text>')
        self.assertNotContains(response, '>V</text>')
        self.assertNotContains(response, '>Y</text>')
        self.assertContains(response, 'Villavicencio')
        self.assertContains(response, 'Medellin')
        self.assertContains(response, 'Yopal')
        self.assertNotContains(response, 'cdn.jsdelivr.net/npm/colombia-geojson@1.0.0/colombia.geo.json')
        self.assertNotContains(response, 'gist.github')
        self.assertNotContains(response, 'libranza-presence-data')
        self.assertContains(response, 'Empresas que conf')
        self.assertContains(response, 'Empresa Landing Aliada')
        self.assertContains(response, 'libranza-logo-rail')
        self.assertContains(response, 'images/respaldos/datacredito-experian.png')
        self.assertContains(response, 'images/respaldos/figarantias.svg')
        self.assertContains(response, 'images/respaldos/orinoco-tic.png')
        self.assertContains(response, 'images/respaldos/seguros-sura.svg')
        self.assertContains(response, 'Seguro de vida deudores')
        self.assertContains(response, 'Tecnolog')
        self.assertNotContains(response, 'Espacio reservado para testimonios')
        self.assertNotContains(response, 'Historias reales, sección lista para activarse')
        self.assertNotContains(response, '800555999')
        self.assertNotContains(response, 'privado@landing.test')

    def test_geojson_departamental_colombia_es_local_y_tiene_departamentos(self):
        ruta_geojson = Path('static/maps/colombia.geo.json')

        self.assertTrue(ruta_geojson.exists())
        self.assertTrue(finders.find('maps/colombia.geo.json'))
        contenido = ruta_geojson.read_text(encoding='utf-8')

        self.assertIn('"FeatureCollection"', contenido)
        self.assertIn('"NOMBRE_DPT": "META"', contenido)
        self.assertIn('"NOMBRE_DPT": "ANTIOQUIA"', contenido)
        self.assertIn('"NOMBRE_DPT": "CASANARE"', contenido)
        self.assertGreaterEqual(contenido.count('"type": "Feature"'), 30)
        self.assertNotIn('gist.github', contenido)
        self.assertNotIn('cdn.jsdelivr.net', contenido)

    def test_landing_no_pinta_empresas_sin_ubicacion_como_zona(self):
        Empresa.objects.create(nombre='Empresa Sin Zona Publica', convenio_activo=True)

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'empresas sin ubicaci')
        self.assertNotContains(response, '"ciudad": "Empresa Sin Zona Publica"')
        self.assertContains(response, 'Cargando mapa de Colombia')
        self.assertContains(response, 'data-geojson-local-url="/static/maps/colombia.geo.json"')

    def test_logo_rail_renderiza_activas_sin_inactivas_ni_datos_sensibles(self):
        Empresa.objects.create(
            nombre='Empresa Rail Activa Uno',
            convenio_activo=True,
            logo=SimpleUploadedFile('rail-uno.svg', b'<svg></svg>', content_type='image/svg+xml'),
            nit='900111222',
            correo_contacto='privado-rail@example.com',
            telefono_contacto='3009998888',
        )
        Empresa.objects.create(nombre='Empresa Rail Activa Dos', convenio_activo=True)
        Empresa.objects.create(nombre='Empresa Rail Inactiva', convenio_activo=False)

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'libranza-logo-rail is-static')
        self.assertContains(response, 'libranza-logo-static-grid')
        self.assertContains(response, '--logo-rail-duration: 55s')
        self.assertContains(response, '--logo-rail-duration-alt: 65s')
        self.assertContains(response, 'object-fit: contain')
        self.assertContains(response, 'prefers-reduced-motion')
        self.assertNotContains(response, '<span class="libranza-logo-rail-repeat"')
        self.assertContains(response, 'Empresa Rail Activa Uno')
        self.assertContains(response, 'Empresa Rail Activa Dos')
        self.assertNotContains(response, '<strong>Empresa Rail Activa Uno</strong>', html=True)
        self.assertContains(response, '<strong>Empresa Rail Activa Dos</strong>', html=True)
        self.assertNotContains(response, 'Empresa Rail Inactiva')
        self.assertNotContains(response, '900111222')
        self.assertNotContains(response, 'privado-rail@example.com')
        self.assertNotContains(response, '3009998888')

    def crear_credito_libranza(self, empresa, *, estado, cedula):
        credito = Credito.objects.create(
            usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            monto_solicitado=Decimal('1000000.00'),
            monto_aprobado=Decimal('1000000.00'),
            plazo_solicitado=6,
            plazo=6,
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa,
            nombres='Persona',
            apellidos='Prueba',
            cedula=cedula,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico='persona@example.com',
            cedula_frontal=SimpleUploadedFile('frontal.pdf', b'frontal', content_type='application/pdf'),
            cedula_trasera=SimpleUploadedFile('trasera.pdf', b'trasera', content_type='application/pdf'),
        )
        return credito

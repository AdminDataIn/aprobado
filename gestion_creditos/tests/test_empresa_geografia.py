from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from gestion_creditos.services.dashboard_metrics import get_admin_dashboard_context
from gestion_creditos.services.empresa_geografia import (
    normalizar_ciudad,
    normalizar_departamento,
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

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Presencia nacional')
        self.assertContains(response, 'Empresas que confían en')
        self.assertContains(response, 'Empresa Landing Aliada')
        self.assertContains(response, 'images/respaldos/datacredito-experian.svg')
        self.assertContains(response, 'images/respaldos/figarantias.svg')
        self.assertContains(response, 'images/respaldos/orinoco-tic.svg')
        self.assertNotContains(response, '800555999')
        self.assertNotContains(response, 'privado@landing.test')

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

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from gestion_creditos.services.empresa_geografia import (
    normalizar_ciudad,
    normalizar_departamento,
    normalizar_municipio,
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

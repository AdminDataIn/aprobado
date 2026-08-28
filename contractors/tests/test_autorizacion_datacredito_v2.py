from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ContractorApplication,
)
from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from gestion_creditos.models import Empresa


@override_settings(
    DATACREDITO_AUTHORIZATION_TEXT_VERSION='prestadores-v1',
    DATACREDITO_AUTHORIZATION_TEXT='Autorizo la consulta ante centrales de información.',
)
class AutorizacionDatacreditoPrestadorV2Test(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='titular-autorizacion',
            email='titular-autorizacion@example.com',
            password='test-password',
        )
        self.empresa = Empresa.objects.create(nombre='Empresa Autorización', convenio_activo=True)
        self.solicitud = ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento='123456789',
            nombres='Ana',
            apellidos='Pérez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1',
            cargo='Consultora',
            autoriza_consulta_centrales=True,
        )
        self.request = RequestFactory().post(
            '/solicitar/',
            HTTP_USER_AGENT='Navegador seguro',
            REMOTE_ADDR='192.0.2.20',
        )

    def test_evidencia_equivalente_no_se_duplica_y_es_vigente(self):
        primera = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
            request=self.request,
        )
        segunda = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
            request=self.request,
        )

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 1)
        self.assertEqual(obtener_autorizacion_datacredito_vigente(self.solicitud), primera)
        self.assertNotEqual(primera.ip_hash, '192.0.2.20')

    @override_settings(
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='prestadores-v2',
        DATACREDITO_AUTHORIZATION_TEXT='Nueva versión jurídica de autorización.',
    )
    def test_cambio_version_crea_nueva_evidencia(self):
        primera = AutorizacionConsultaDatacreditoPrestador.objects.create(
            solicitud=self.solicitud,
            usuario=self.usuario,
            autorizada=True,
            version_texto='prestadores-v1',
            texto_hash='a' * 64,
            aceptada_en=timezone.now(),
        )

        segunda = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
            request=self.request,
        )

        self.assertNotEqual(primera.pk, segunda.pk)
        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 2)
        self.assertEqual(obtener_autorizacion_datacredito_vigente(self.solicitud), segunda)

    def test_evidencia_historica_es_inmutable(self):
        evidencia = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
            request=self.request,
        )
        evidencia.user_agent = 'Modificado'

        with self.assertRaisesMessage(ValidationError, 'inmutable'):
            evidencia.save()

    @override_settings(
        DATACREDITO_AUTHORIZATION_TEXT_VERSION='',
        DATACREDITO_AUTHORIZATION_TEXT='',
    )
    def test_sin_texto_juridico_no_crea_evidencia_vigente(self):
        evidencia = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.usuario,
            request=self.request,
        )

        self.assertIsNone(evidencia)
        self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))

from django.test import TestCase

from contractors.models import ContractorBranding, ContractorOrganization
from contractors.selectors import obtener_branding_activo_por_organizacion
from contractors.services.branding import obtener_contexto_branding_con_defaults


class BrandingContratistasTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='acme',
        )
        self.otra_organizacion = ContractorOrganization.objects.create(
            name='Beta Contractors',
            slug='beta',
            subdomain='beta',
        )

    def test_branding_activo_por_organizacion(self):
        branding = ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Credit',
            primary_color='#112233',
            secondary_color='#445566',
            support_email='soporte@acme.test',
            landing_copy='Credito para contratistas Acme.',
        )

        self.assertEqual(obtener_branding_activo_por_organizacion(self.organizacion), branding)
        contexto = obtener_contexto_branding_con_defaults(self.organizacion)
        self.assertEqual(contexto['nombre_visual'], 'Acme Credit')
        self.assertEqual(contexto['color_primario'], '#112233')
        self.assertEqual(contexto['color_secundario'], '#445566')
        self.assertEqual(contexto['correo_soporte'], 'soporte@acme.test')
        self.assertEqual(contexto['texto_landing'], 'Credito para contratistas Acme.')
        self.assertTrue(contexto['tiene_branding_personalizado'])

    def test_branding_inactivo_no_se_usa(self):
        ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Inactivo',
            primary_color='#111111',
            is_active=False,
        )

        self.assertIsNone(obtener_branding_activo_por_organizacion(self.organizacion))
        contexto = obtener_contexto_branding_con_defaults(self.organizacion)
        self.assertEqual(contexto['nombre_visual'], 'Acme Contractors')
        self.assertEqual(contexto['color_primario'], '#0d6efd')
        self.assertFalse(contexto['tiene_branding_personalizado'])

    def test_organizacion_sin_branding_recibe_defaults(self):
        contexto = obtener_contexto_branding_con_defaults(self.organizacion)

        self.assertEqual(contexto['organizacion_id'], self.organizacion.id)
        self.assertEqual(contexto['nombre_visual'], 'Acme Contractors')
        self.assertEqual(contexto['url_logo'], '')
        self.assertEqual(contexto['color_primario'], '#0d6efd')
        self.assertEqual(contexto['color_secundario'], '#6c757d')
        self.assertEqual(contexto['correo_soporte'], '')
        self.assertEqual(contexto['texto_landing'], '')
        self.assertFalse(contexto['tiene_branding_personalizado'])

    def test_branding_de_una_organizacion_no_se_mezcla_con_otra(self):
        ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Credit',
            primary_color='#112233',
        )
        ContractorBranding.objects.create(
            organization=self.otra_organizacion,
            display_name='Beta Credit',
            primary_color='#abcdef',
        )

        contexto_acme = obtener_contexto_branding_con_defaults(self.organizacion)
        contexto_beta = obtener_contexto_branding_con_defaults(self.otra_organizacion)

        self.assertEqual(contexto_acme['nombre_visual'], 'Acme Credit')
        self.assertEqual(contexto_acme['color_primario'], '#112233')
        self.assertEqual(contexto_beta['nombre_visual'], 'Beta Credit')
        self.assertEqual(contexto_beta['color_primario'], '#abcdef')

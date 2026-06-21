from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from aprobado_web.middleware import SubdomainRoutingMiddleware
from contractors.middleware import ContractorTenantMiddleware
from contractors.models import (
    ContractorOrganization,
    ContractorProductConfig,
    ContractorProfile,
    ConfiguracionPortalContratistas,
)
from contractors.selectors import (
    obtener_configuracion_portal_contratistas_por_host,
    obtener_configuracion_producto_activa,
    obtener_organizacion_por_subdominio,
    obtener_perfil_contratista_usuario,
    usuario_pertenece_a_organizacion,
)
from contractors.services.simulation import (
    ErrorSimulacionContratista,
    simular_credito_contratista,
    simular_credito_portal_contratistas,
)
from contractors.services import simulation as simulation_service
from gestion_creditos.models import Credito


User = get_user_model()


class DominioContratistasTests(TestCase):
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
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Contratistas',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('100000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('5000000.00'),
            min_term_months=3,
            max_term_months=24,
            monthly_rate=Decimal('2.5000'),
            commission_rate=Decimal('5.0000'),
            commission_amount=Decimal('100000.00'),
            vat_rate=Decimal('19.0000'),
            allows_second_credit=True,
            allows_portfolio_takeover=False,
        )
        self.usuario = User.objects.create_user(username='contractor-user')
        self.perfil = ContractorProfile.objects.create(
            user=self.usuario,
            organization=self.organizacion,
            role=ContractorProfile.Role.MANAGER,
        )

    def test_organizacion_activa_resuelve_por_subdominio(self):
        self.assertEqual(obtener_organizacion_por_subdominio('acme'), self.organizacion)
        self.assertEqual(obtener_organizacion_por_subdominio(' ACME '), self.organizacion)

    def test_organizacion_inactiva_no_resuelve(self):
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])

        self.assertIsNone(obtener_organizacion_por_subdominio('acme'))

    def test_configuracion_activa_resuelve_por_producto(self):
        self.assertEqual(
            obtener_configuracion_producto_activa(
                self.organizacion,
                ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            ),
            self.configuracion,
        )

    def test_configuracion_portal_activa_resuelve_por_host(self):
        self.assertEqual(
            obtener_configuracion_portal_contratistas_por_host('contratistas.aprobado.com.co'),
            self.configuracion_portal,
        )
        self.assertEqual(
            obtener_configuracion_portal_contratistas_por_host('https://contratistas.aprobado.com.co:443/'),
            self.configuracion_portal,
        )

    def test_usuario_pertenece_solo_a_su_organizacion(self):
        self.assertEqual(obtener_perfil_contratista_usuario(self.usuario, self.organizacion), self.perfil)
        self.assertTrue(usuario_pertenece_a_organizacion(self.usuario, self.organizacion))
        self.assertFalse(usuario_pertenece_a_organizacion(self.usuario, self.otra_organizacion))

    def test_simulacion_respeta_monto_minimo_y_maximo(self):
        with self.assertRaisesRegex(ErrorSimulacionContratista, 'monto_menor_al_minimo'):
            simular_credito_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                monto=Decimal('99999.99'),
                plazo_meses=12,
            )

        with self.assertRaisesRegex(ErrorSimulacionContratista, 'monto_supera_maximo'):
            simular_credito_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                monto=Decimal('5000000.01'),
                plazo_meses=12,
            )

    def test_simulacion_respeta_plazo_minimo_y_maximo(self):
        with self.assertRaisesRegex(ErrorSimulacionContratista, 'plazo_menor_al_minimo'):
            simular_credito_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                monto=Decimal('1000000.00'),
                plazo_meses=2,
            )

        with self.assertRaisesRegex(ErrorSimulacionContratista, 'plazo_supera_maximo'):
            simular_credito_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                monto=Decimal('1000000.00'),
                plazo_meses=25,
            )

    def test_simulacion_usa_tasa_y_comision_de_la_organizacion(self):
        resultado = simular_credito_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            monto=Decimal('1000000.00'),
            plazo_meses=12,
        )

        self.assertEqual(resultado.tasa_mensual, Decimal('2.5000'))
        self.assertEqual(resultado.comision, Decimal('150000.00'))
        self.assertEqual(resultado.iva_comision, Decimal('28500.00'))
        self.assertEqual(resultado.capital_financiado, Decimal('1178500.00'))
        self.assertGreater(resultado.cuota_mensual, Decimal('0.00'))
        self.assertGreater(resultado.total_a_pagar, resultado.capital_financiado)

    def test_simulacion_no_crea_credito(self):
        creditos_antes = Credito.objects.count()

        simular_credito_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            monto=Decimal('1000000.00'),
            plazo_meses=12,
        )

        self.assertEqual(Credito.objects.count(), creditos_antes)

    def test_simulacion_portal_prestadores_v2_caso_oficial(self):
        self.configuracion_portal.monto_minimo = Decimal('300000.00')
        self.configuracion_portal.monto_maximo = Decimal('10000000.00')
        self.configuracion_portal.plazo_maximo_meses = 8
        self.configuracion_portal.tasa_mensual = Decimal('2.2000')
        self.configuracion_portal.tasa_comision = Decimal('10.0000')
        self.configuracion_portal.comision_fija = Decimal('0.00')
        self.configuracion_portal.tasa_iva = Decimal('19.0000')
        self.configuracion_portal.tasa_fondo_garantia = Decimal('2.0000')
        self.configuracion_portal.iva_fondo_garantia = Decimal('19.0000')
        self.configuracion_portal.fondo_garantia_incluye_iva = True
        self.configuracion_portal.factor_seguro_vida = Decimal('0.003711')
        self.configuracion_portal.seguro_vida_financiado = True

        resultado = simular_credito_portal_contratistas(
            configuracion_portal=self.configuracion_portal,
            monto=Decimal('10000000.00'),
            plazo_meses=8,
        )

        self.assertEqual(resultado.costo_originacion, Decimal('1000000.00'))
        self.assertEqual(resultado.iva_costo_originacion, Decimal('190000.00'))
        self.assertEqual(resultado.fondo_garantia_total, Decimal('200000.00'))
        self.assertEqual(resultado.fondo_garantia_base, Decimal('168067.23'))
        self.assertEqual(resultado.fondo_garantia_iva, Decimal('31932.77'))
        self.assertEqual(resultado.fondo_garantia_base + resultado.fondo_garantia_iva, Decimal('200000.00'))
        self.assertEqual(resultado.seguro_vida, Decimal('37110.00'))
        self.assertEqual(resultado.capital_total_financiado, Decimal('11427110.00'))
        self.assertEqual(resultado.cuota_mensual, Decimal('1573387.58'))
        self.assertEqual(resultado.total_estimado, Decimal('12587100.64'))
        self.assertEqual(resultado.intereses_estimados, Decimal('1159990.64'))

    def test_simulacion_portal_tasa_cero(self):
        self.configuracion_portal.tasa_mensual = Decimal('0.0000')
        self.configuracion_portal.tasa_comision = Decimal('0.0000')
        self.configuracion_portal.comision_fija = Decimal('0.00')
        self.configuracion_portal.tasa_fondo_garantia = Decimal('0.0000')
        self.configuracion_portal.factor_seguro_vida = Decimal('0.000000')

        resultado = simular_credito_portal_contratistas(
            configuracion_portal=self.configuracion_portal,
            monto=Decimal('1200000.00'),
            plazo_meses=12,
        )

        self.assertEqual(resultado.cuota_mensual, Decimal('100000.00'))
        self.assertEqual(resultado.intereses_estimados, Decimal('0.00'))

    def test_aliases_legacy_en_ingles_siguen_funcionando(self):
        self.assertEqual(
            simulation_service.ContractorSimulationError,
            ErrorSimulacionContratista,
        )
        resultado = simulation_service.simulate_contractor_credit(
            organization=self.organizacion,
            product_config=self.configuracion,
            amount=Decimal('1000000.00'),
            term_months=12,
        )

        self.assertEqual(resultado.monthly_rate, resultado.tasa_mensual)
        self.assertEqual(resultado.commission_amount, resultado.comision)
        self.assertEqual(resultado.as_dict()['monthly_rate'], Decimal('2.5000'))


class MiddlewareContratistasTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Contratistas',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('100000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.organizacion = ContractorOrganization.objects.create(
            name='Portal Contratistas',
            slug='contratistas',
            subdomain='contratistas',
        )

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_middleware_resuelve_portal_unico_contratistas(self):
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.aprobado.com.co')

        middleware(request)

        self.assertEqual(request.contractor_organization, self.organizacion)
        self.assertEqual(request.configuracion_portal_contratistas, self.configuracion_portal)

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_middleware_resuelve_configuracion_portal_aunque_no_exista_organizacion_legacy(self):
        ContractorOrganization.objects.all().delete()
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.aprobado.com.co')

        middleware(request)

        self.assertIsNone(request.contractor_organization)
        self.assertEqual(request.configuracion_portal_contratistas, self.configuracion_portal)

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_middleware_no_resuelve_dominio_raiz(self):
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='aprobado.com.co')

        middleware(request)

        self.assertIsNone(request.contractor_organization)
        self.assertIsNone(request.configuracion_portal_contratistas)

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_middleware_subdominio_empresa_no_resuelve_portal(self):
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='datain.aprobado.com.co')

        middleware(request)

        self.assertIsNone(request.contractor_organization)
        self.assertIsNone(request.configuracion_portal_contratistas)

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_middleware_resuelve_contratistas_localhost_en_debug(self):
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.localhost:8000')

        middleware(request)

        self.assertEqual(request.contractor_organization, self.organizacion)
        self.assertEqual(request.configuracion_portal_contratistas, self.configuracion_portal)

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.localhost:8000',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_middleware_resuelve_portal_local_configurado_con_puerto(self):
        middleware = ContractorTenantMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.localhost:8000')

        middleware(request)

        self.assertEqual(request.contractor_organization, self.organizacion)
        self.assertEqual(request.configuracion_portal_contratistas, self.configuracion_portal)

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_routing_solo_portal_contratistas_usa_urls_contractors(self):
        middleware = SubdomainRoutingMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.aprobado.com.co')

        middleware(request)

        self.assertEqual(request.urlconf, 'aprobado_web.urls_contractors')

    @override_settings(
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
    )
    def test_routing_subdominio_empresa_no_usa_urls_contractors(self):
        middleware = SubdomainRoutingMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='datain.aprobado.com.co')

        middleware(request)

        self.assertEqual(request.urlconf, 'aprobado_web.urls_main')

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_routing_contratistas_localhost_usa_urls_contractors_en_debug(self):
        middleware = SubdomainRoutingMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.localhost:8000')

        middleware(request)

        self.assertEqual(request.urlconf, 'aprobado_web.urls_contractors')

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.localhost:8000',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_routing_portal_local_configurado_con_puerto_usa_urls_contractors(self):
        middleware = SubdomainRoutingMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='contratistas.localhost:8000')

        middleware(request)

        self.assertEqual(request.urlconf, 'aprobado_web.urls_contractors')

    @override_settings(
        DEBUG=True,
        PRIMARY_DOMAIN_HOST='aprobado.com.co',
        CONTRACTORS_PORTAL_HOST='contratistas.localhost',
        ALLOWED_HOSTS=['.localhost', '.aprobado.com.co', 'testserver'],
    )
    def test_routing_datain_localhost_no_usa_urls_contractors(self):
        middleware = SubdomainRoutingMiddleware(lambda request: None)
        request = self.factory.get('/', HTTP_HOST='datain.localhost:8000')

        middleware(request)

        self.assertEqual(request.urlconf, 'aprobado_web.urls_main')

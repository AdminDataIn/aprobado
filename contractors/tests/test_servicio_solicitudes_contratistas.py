from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from contractors.models import ContractorApplication, ContractorOrganization, ContractorProductConfig
from contractors.services.solicitudes import DatosSolicitudContratista, crear_solicitud_contratista
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago


class ServicioSolicitudesContratistasTests(TestCase):
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
        )
        self.otra_configuracion = ContractorProductConfig.objects.create(
            organization=self.otra_organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('50000.00'),
            max_amount=Decimal('2000000.00'),
            min_term_months=1,
            max_term_months=12,
            monthly_rate=Decimal('7.0000'),
            commission_rate=Decimal('1.0000'),
            commission_amount=Decimal('0.00'),
            vat_rate=Decimal('19.0000'),
        )

    def _datos(self, **overrides):
        datos = {
            'monto_solicitado': Decimal('1000000.00'),
            'plazo_meses': 12,
            'cuota_mensual_estimada': Decimal('120000.00'),
            'payload_simulacion': {
                'monto_solicitado': '1000000.00',
                'plazo_meses': 12,
                'cuota_mensual': '120000.00',
            },
            'tipo_documento': 'CC',
            'numero_documento': '123456789',
            'nombres': 'Ana',
            'apellidos': 'Perez',
            'celular': '3001234567',
            'correo': 'ana@example.com',
            'escenario_credito': ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            'direccion': 'Calle 1 # 2-3',
            'terminos_aceptados': True,
            'subdominio_origen': 'acme',
            'ip_address': '127.0.0.1',
            'user_agent': 'Prueba',
        }
        datos.update(overrides)
        return DatosSolicitudContratista(**datos)

    def test_crea_solicitud_valida(self):
        resultado = crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
        )

        self.assertIsNotNone(resultado.solicitud_id)
        self.assertEqual(resultado.solicitud.organization, self.organizacion)
        self.assertEqual(resultado.solicitud.product_config, self.configuracion)
        self.assertEqual(resultado.solicitud.document_number, '123456789')
        self.assertEqual(resultado.solicitud.escenario_credito, ContractorApplication.EscenarioCredito.NUEVO_CREDITO)

    def test_crea_solicitud_con_usuario_asociado(self):
        usuario = get_user_model().objects.create_user(username='contratista-owner')

        resultado = crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
            usuario=usuario,
        )

        self.assertEqual(resultado.solicitud.usuario, usuario)

    def test_estado_inicial_recibida(self):
        resultado = crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
        )

        self.assertEqual(resultado.estado, ContractorApplication.Estado.RECIBIDA)

    def test_rechaza_organizacion_inactiva(self):
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])

        with self.assertRaises(ValidationError) as contexto:
            crear_solicitud_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                datos=self._datos(),
            )

        self.assertIn('organization', contexto.exception.message_dict)

    def test_rechaza_configuracion_de_otra_organizacion(self):
        with self.assertRaises(ValidationError) as contexto:
            crear_solicitud_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.otra_configuracion,
                datos=self._datos(),
            )

        self.assertIn('product_config', contexto.exception.message_dict)

    def test_rechaza_terminos_no_aceptados(self):
        with self.assertRaises(ValidationError) as contexto:
            crear_solicitud_contratista(
                organizacion=self.organizacion,
                configuracion_producto=self.configuracion,
                datos=self._datos(terminos_aceptados=False),
            )

        self.assertIn('accepted_terms', contexto.exception.message_dict)

    def test_guarda_ip_user_agent_y_subdominio(self):
        resultado = crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(ip_address='10.0.0.1', user_agent='Navegador Prueba', subdominio_origen=' acme '),
        )

        self.assertEqual(resultado.solicitud.ip_address, '10.0.0.1')
        self.assertEqual(resultado.solicitud.user_agent, 'Navegador Prueba')
        self.assertEqual(resultado.solicitud.source_subdomain, 'acme')

    def test_guarda_payload_simulacion(self):
        payload = {'cuota_mensual': '150000.00', 'total_a_pagar': '1800000.00'}

        resultado = crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(payload_simulacion=payload),
        )

        self.assertEqual(resultado.solicitud.simulation_payload, payload)

    def test_no_crea_credito(self):
        creditos_antes = Credito.objects.count()

        crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
        )

        self.assertEqual(Credito.objects.count(), creditos_antes)

    def test_no_crea_credito_libranza(self):
        detalles_antes = CreditoLibranza.objects.count()

        crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
        )

        self.assertEqual(CreditoLibranza.objects.count(), detalles_antes)

    def test_no_crea_historial_estado_ni_historial_pago(self):
        estados_antes = HistorialEstado.objects.count()
        pagos_antes = HistorialPago.objects.count()

        crear_solicitud_contratista(
            organizacion=self.organizacion,
            configuracion_producto=self.configuracion,
            datos=self._datos(),
        )

        self.assertEqual(HistorialEstado.objects.count(), estados_antes)
        self.assertEqual(HistorialPago.objects.count(), pagos_antes)

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from contractors.models import (
    ContractorApplication,
    ContractorOrganization,
    ContractorProductConfig,
    InformacionLaboralSolicitudContratista,
)
from contractors.selectors import obtener_datos_contractuales_solicitud, solicitud_tiene_datos_contractuales
from contractors.services.datos_contractuales import (
    DatosContractualesContratista,
    ErrorDatosContractualesContratista,
    calcular_valor_pendiente_contrato,
    registrar_datos_contractuales_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialEstado, HistorialPago, Pagare


class DatosContractualesContratistaTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Portal Contratistas',
            slug='contratistas',
            subdomain='contratistas',
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
        self.empresa = Empresa.objects.create(
            nombre='Empresa Convenio Contratistas',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
            nit='900123456-7',
        )
        self.empresa_no_elegible = Empresa.objects.create(
            nombre='Empresa No Elegible',
            convenio_activo=False,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            product_config=self.configuracion,
            status=ContractorApplication.Estado.RECIBIDA,
            requested_amount=Decimal('1000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('120000.00'),
            simulation_payload={'cuota_mensual': '120000.00'},
            document_type='CC',
            document_number='123456789',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )

    def _datos(self, **overrides):
        datos = {
            'cargo': 'Contratista comercial',
            'tipo_contrato': InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            'fecha_inicio_contrato': date(2026, 1, 1),
            'fecha_fin_contrato': date(2026, 12, 31),
            'valor_total_contrato': Decimal('12000000.00'),
            'valor_pagado_contrato': Decimal('4000000.00'),
            'valor_pendiente_cobrar': Decimal('8000000.00'),
            'empresa': self.empresa,
            'observaciones': 'Contrato vigente validado documentalmente.',
        }
        datos.update(overrides)
        return DatosContractualesContratista(**datos)

    def test_crea_datos_contractuales_validos(self):
        resultado = registrar_datos_contractuales_contratista(
            solicitud=self.solicitud,
            datos=self._datos(),
        )

        self.assertIsNotNone(resultado.informacion_laboral_id)
        self.assertEqual(resultado.solicitud_id, self.solicitud.id)
        self.assertEqual(resultado.informacion_laboral.solicitud, self.solicitud)
        self.assertEqual(resultado.informacion_laboral.cargo, 'Contratista comercial')
        self.assertEqual(resultado.informacion_laboral.empresa, self.empresa)
        self.assertEqual(resultado.informacion_laboral.empresa_contratante_nombre, self.empresa.nombre)

    def test_rechaza_empresa_requerida(self):
        with self.assertRaises(ErrorDatosContractualesContratista) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(empresa=None),
            )

        self.assertEqual(str(contexto.exception), 'empresa_requerida')

    def test_rechaza_empresa_no_elegible(self):
        with self.assertRaises(ErrorDatosContractualesContratista) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(empresa=self.empresa_no_elegible),
            )

        self.assertEqual(str(contexto.exception), 'empresa_no_elegible_libranza')

    def test_rechaza_fecha_fin_menor_a_fecha_inicio(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(
                    fecha_inicio_contrato=date(2026, 12, 31),
                    fecha_fin_contrato=date(2026, 1, 1),
                ),
            )

        self.assertIn('fecha_fin_contrato', contexto.exception.message_dict)

    def test_rechaza_valores_negativos(self):
        escenarios = {
            'valor_total_contrato': Decimal('-1.00'),
            'valor_pagado_contrato': Decimal('-1.00'),
            'valor_pendiente_cobrar': Decimal('-1.00'),
        }

        for campo, valor in escenarios.items():
            with self.subTest(campo=campo), self.assertRaises(ValidationError) as contexto:
                registrar_datos_contractuales_contratista(
                    solicitud=self.solicitud,
                    datos=self._datos(**{campo: valor}),
                )

            self.assertIn(campo, contexto.exception.message_dict)

    def test_rechaza_cargo_vacio(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(cargo=''),
            )

        self.assertIn('cargo', contexto.exception.message_dict)

    def test_rechaza_tipo_contrato_vacio(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(tipo_contrato=''),
            )

        self.assertIn('tipo_contrato', contexto.exception.message_dict)

    def test_rechaza_suma_pagado_y_pendiente_mayor_a_total(self):
        with self.assertRaises(ValidationError) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(
                    valor_total_contrato=Decimal('10000000.00'),
                    valor_pagado_contrato=Decimal('4000000.00'),
                    valor_pendiente_cobrar=Decimal('7000000.00'),
                ),
            )

        self.assertIn('valor_pendiente_cobrar', contexto.exception.message_dict)

    def test_rechaza_solicitud_de_organizacion_inactiva(self):
        self.organizacion.is_active = False
        self.organizacion.save(update_fields=['is_active'])
        self.solicitud = ContractorApplication.objects.select_related('organization').get(id=self.solicitud.id)

        with self.assertRaises(ValidationError) as contexto:
            registrar_datos_contractuales_contratista(
                solicitud=self.solicitud,
                datos=self._datos(),
            )

        self.assertIn('solicitud', contexto.exception.message_dict)

    def test_selector_obtiene_datos_de_solicitud(self):
        resultado = registrar_datos_contractuales_contratista(
            solicitud=self.solicitud,
            datos=self._datos(),
        )

        informacion = obtener_datos_contractuales_solicitud(self.solicitud)

        self.assertEqual(informacion, resultado.informacion_laboral)

    def test_selector_indica_si_solicitud_tiene_datos_contractuales(self):
        self.assertFalse(solicitud_tiene_datos_contractuales(self.solicitud))

        registrar_datos_contractuales_contratista(
            solicitud=self.solicitud,
            datos=self._datos(),
        )

        self.assertTrue(solicitud_tiene_datos_contractuales(self.solicitud))

    def test_calcula_valor_pendiente_contrato(self):
        self.assertEqual(
            calcular_valor_pendiente_contrato(Decimal('12000000.00'), Decimal('4000000.00')),
            Decimal('8000000.00'),
        )

    def test_no_crea_modelos_financieros_del_flujo(self):
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        registrar_datos_contractuales_contratista(
            solicitud=self.solicitud,
            datos=self._datos(),
        )

        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])

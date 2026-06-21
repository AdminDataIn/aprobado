from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.test import TestCase
from django.utils import timezone

from contractors.models import (
    ContractorApplication,
    ContractorOrganization,
    ContractorProductConfig,
    InformacionLaboralSolicitudContratista,
)
from contractors.services.capacidad_contractual import (
    calcular_meses_restantes_contrato,
    evaluar_capacidad_contractual_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialEstado, HistorialPago, Pagare


class CapacidadContractualContratistaTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
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
        )
        self.empresa_no_elegible = Empresa.objects.create(
            nombre='Empresa Sin Convenio',
            convenio_activo=False,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = self._crear_solicitud()

    def _crear_solicitud(self, **overrides):
        datos = {
            'organization': self.organizacion,
            'product_config': self.configuracion,
            'status': ContractorApplication.Estado.RECIBIDA,
            'requested_amount': Decimal('1000000.00'),
            'term_months': 6,
            'estimated_monthly_payment': Decimal('120000.00'),
            'simulation_payload': {'cuota_mensual': '120000.00'},
            'document_type': 'CC',
            'document_number': '123456789',
            'first_name': 'Ana',
            'last_name': 'Perez',
            'phone': '3001234567',
            'email': 'ana@example.com',
            'address': 'Calle 1 # 2-3',
            'accepted_terms': True,
            'source_subdomain': 'contratistas',
        }
        datos.update(overrides)
        return ContractorApplication.objects.create(**datos)

    def _datos_contractuales(self, **overrides):
        datos = {
            'solicitud': self.solicitud,
            'cargo': 'Contratista comercial',
            'tipo_contrato': InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            'fecha_inicio_contrato': self.hoy - relativedelta(months=1),
            'fecha_fin_contrato': self.hoy + relativedelta(months=8),
            'valor_total_contrato': Decimal('12000000.00'),
            'valor_pagado_contrato': Decimal('4000000.00'),
            'valor_pendiente_cobrar': Decimal('8000000.00'),
            'empresa': self.empresa,
            'empresa_contratante_nombre': 'Empresa Contratante SAS',
            'empresa_contratante_nit': '900123456-7',
            'pagador_nombre': 'Pagador Principal',
            'pagador_email': 'pagador@example.com',
            'pagador_telefono': '3007654321',
            'observaciones': '',
        }
        datos.update(overrides)
        return InformacionLaboralSolicitudContratista.objects.create(**datos)

    def test_elegible_con_contrato_vigente_valor_suficiente_y_plazo_dentro_de_vigencia(self):
        self._datos_contractuales()

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertTrue(resultado.elegible)
        self.assertTrue(resultado.eligible)
        self.assertEqual(resultado.razon, 'capacidad_contractual_suficiente')
        self.assertEqual(resultado.reason, 'capacidad_contractual_suficiente')
        self.assertEqual(resultado.solicitud_id, self.solicitud.id)
        self.assertEqual(resultado.application_id, self.solicitud.id)
        self.assertEqual(resultado.valor_pendiente_cobrar, Decimal('8000000.00'))
        self.assertEqual(resultado.monto_solicitado, Decimal('1000000.00'))
        self.assertEqual(resultado.plazo_solicitado, 6)
        self.assertEqual(resultado.capacidad_maxima_estimada, Decimal('8000000.00'))

    def test_no_elegible_sin_datos_contractuales(self):
        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('datos_contractuales_requeridos', resultado.razones)

    def test_no_elegible_sin_empresa(self):
        self._datos_contractuales(empresa=None, empresa_contratante_nombre='Empresa Legacy')

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('empresa_requerida', resultado.razones)

    def test_no_elegible_empresa_no_elegible(self):
        self._datos_contractuales(empresa=self.empresa_no_elegible)

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('empresa_no_elegible_libranza', resultado.razones)

    def test_no_elegible_con_contrato_vencido(self):
        self._datos_contractuales(fecha_fin_contrato=self.hoy - relativedelta(days=1))

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('contrato_vencido', resultado.razones)
        self.assertEqual(resultado.meses_restantes_contrato, 0)
        self.assertEqual(resultado.valor_pendiente_cobrar, Decimal('0.00'))
        self.assertEqual(resultado.capacidad_maxima_estimada, Decimal('0.00'))
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIn('contrato_vencido', resultado.bloqueos)

    def test_capacidad_marca_revision_manual_por_valor_pendiente_inferido(self):
        self.solicitud.simulation_payload = {
            'analisis_contractual_seguro': {
                'metadata': {
                    'valor_pendiente_fuente': 'inferida_mensualidad_vigencia',
                    'valor_pendiente_requiere_confirmacion': True,
                },
            },
        }
        self.solicitud.save(update_fields=['simulation_payload'])
        self._datos_contractuales()

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertTrue(resultado.elegible)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIn('valor_pendiente_inferido_requiere_confirmacion', resultado.advertencias)

    def test_no_elegible_con_valor_pendiente_cero(self):
        self.solicitud.requested_amount = Decimal('0.00')
        self.solicitud.save(update_fields=['requested_amount'])
        self._datos_contractuales(
            valor_total_contrato=Decimal('4000000.00'),
            valor_pagado_contrato=Decimal('4000000.00'),
            valor_pendiente_cobrar=Decimal('0.00'),
        )

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('valor_pendiente_cobrar_insuficiente', resultado.razones)

    def test_no_elegible_si_monto_solicitado_supera_valor_pendiente(self):
        self.solicitud.requested_amount = Decimal('2000000.00')
        self.solicitud.save(update_fields=['requested_amount'])
        self._datos_contractuales(
            valor_total_contrato=Decimal('8000000.00'),
            valor_pagado_contrato=Decimal('7000000.00'),
            valor_pendiente_cobrar=Decimal('1000000.00'),
        )

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('monto_supera_valor_pendiente_cobrar', resultado.razones)

    def test_no_elegible_si_plazo_supera_meses_restantes_del_contrato(self):
        self.solicitud.term_months = 10
        self.solicitud.save(update_fields=['term_months'])
        self._datos_contractuales(fecha_fin_contrato=self.hoy + relativedelta(months=4))

        resultado = evaluar_capacidad_contractual_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('plazo_supera_meses_restantes_contrato', resultado.razones)
        self.assertLess(resultado.meses_restantes_contrato, self.solicitud.term_months)

    def test_calcula_meses_restantes_correctamente(self):
        fecha_fin = self.hoy + relativedelta(months=3, days=1)

        self.assertEqual(
            calcular_meses_restantes_contrato(fecha_fin, fecha_base=self.hoy),
            4,
        )
        self.assertEqual(
            calcular_meses_restantes_contrato(self.hoy, fecha_base=self.hoy),
            1,
        )
        self.assertEqual(
            calcular_meses_restantes_contrato(self.hoy - relativedelta(days=1), fecha_base=self.hoy),
            0,
        )

    def test_no_crea_modelos_financieros_ni_cambia_estado(self):
        self._datos_contractuales()
        estado_inicial = self.solicitud.status
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        evaluar_capacidad_contractual_contratista(self.solicitud)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_inicial)
        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])

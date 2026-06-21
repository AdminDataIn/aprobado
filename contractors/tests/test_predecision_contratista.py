from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.datacredito.dto import ResultadoDatacreditoPrestador
from contractors.models import (
    ConfiguracionPortalContratistas,
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorOrganization,
    ContractorProductConfig,
    InformacionLaboralSolicitudContratista,
)
from contractors.services.elegibilidad_conversion import TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION
from contractors.services.predecision import (
    DECISION_BLOQUEADO_READ_ONLY,
    DECISION_INCOMPLETO,
    DECISION_PREAPROBADO_READ_ONLY,
    DECISION_REQUIERE_REVISION_MANUAL,
    ESTADO_APROBADO,
    ESTADO_EVALUADO_READ_ONLY,
    ESTADO_NO_EVALUADO,
    ESTADO_PENDIENTE,
    ESTADO_RECHAZADO,
    evaluar_predecision_contratista,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialEstado, HistorialPago, Pagare


User = get_user_model()


@override_settings(CONTRACTORS_DATACREDITO_ENABLED=False)
class PredecisionContratistaTests(TestCase):
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
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Prestadores',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('1200000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=4,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.usuario_credito_previo = User.objects.create_user(
            username='cliente-previo',
            email='cliente-previo@example.com',
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagadora Test',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = ContractorApplication.objects.create(
            organization=self.organizacion,
            configuracion_portal=self.configuracion_portal,
            product_config=self.configuracion,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('1000000.00'),
            term_months=6,
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

    def _documentos_minimos_aprobados(self, omitir=None):
        omitir = set(omitir or [])
        for tipo_documento in TIPOS_DOCUMENTO_REQUERIDOS_CONVERSION:
            if tipo_documento in omitir:
                continue
            ContractorApplicationDocument.objects.create(
                application=self.solicitud,
                document_type=tipo_documento,
                file=f'contractors/applications/documents/{tipo_documento}.pdf',
                original_filename=f'{tipo_documento}.pdf',
                content_type='application/pdf',
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )

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

    def _definir_escenario(self, escenario_credito):
        self.solicitud.escenario_credito = escenario_credito
        self.solicitud.save(update_fields=['escenario_credito'])

    def _credito_previo(
        self,
        *,
        porcentaje_pagado=Decimal('40.00'),
        estado=Credito.EstadoCredito.ACTIVO,
        monto_aprobado=Decimal('1000000.00'),
        saldo_pendiente=None,
    ):
        capital_pendiente = (monto_aprobado * (Decimal('100.00') - porcentaje_pagado) / Decimal('100.00')).quantize(
            Decimal('0.01'),
        )
        saldo_pendiente = saldo_pendiente if saldo_pendiente is not None else capital_pendiente
        credito = Credito.objects.create(
            usuario=self.usuario_credito_previo,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            monto_solicitado=monto_aprobado,
            plazo_solicitado=12,
            monto_aprobado=monto_aprobado,
            plazo=12,
            tasa_interes=Decimal('2.50'),
            saldo_pendiente=saldo_pendiente,
            capital_pendiente=capital_pendiente,
            valor_cuota=Decimal('120000.00'),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            nombres='Ana',
            apellidos='Perez',
            cedula=self.solicitud.document_number,
            direccion='Calle 1 # 2-3',
            telefono='3001234567',
            correo_electronico='ana@example.com',
            empresa=self.empresa,
            ingresos_mensuales=Decimal('3000000.00'),
            cedula_frontal='cedula_frontal.pdf',
            cedula_trasera='cedula_trasera.pdf',
            certificado_bancario='certificado_bancario.pdf',
        )
        return credito

    def test_predecision_elegible_cuando_documental_y_capacidad_pasan(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertFalse(resultado.eligible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertEqual(resultado.razon, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertEqual(resultado.application_id, self.solicitud.id)
        self.assertEqual(resultado.documental['eligible'], True)
        self.assertEqual(resultado.capacidad_contractual['eligible'], True)
        self.assertEqual(resultado.riesgo['status'], ESTADO_APROBADO)
        self.assertEqual(resultado.riesgo['reason'], 'sin_credito_previo')
        self.assertEqual(resultado.score_status, ESTADO_EVALUADO_READ_ONLY)
        self.assertEqual(resultado.score_resultado['fuente'], 'score_interno_read_only')
        self.assertIn('datacredito', resultado.score_resultado['componentes_pendientes'])
        self.assertEqual(resultado.datacredito_status, ESTADO_PENDIENTE)
        self.assertEqual(resultado.blockers, ())
        self.assertEqual(resultado.como_dict()['eligible'], False)

    def test_falla_por_documentos_faltantes(self):
        self._documentos_minimos_aprobados(
            omitir={ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO},
        )
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_INCOMPLETO)
        self.assertEqual(resultado.documental_status, 'INCOMPLETO')
        self.assertIn(
            'documental:documento_faltante:certificado_bancario',
            resultado.razones,
        )
        self.assertIn('documental:documento_faltante:certificado_bancario', resultado.bloqueantes)
        self.assertEqual(resultado.score_status, ESTADO_NO_EVALUADO)

    def test_falla_por_capacidad_contractual(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales(
            valor_total_contrato=Decimal('8000000.00'),
            valor_pagado_contrato=Decimal('7500000.00'),
            valor_pendiente_cobrar=Decimal('500000.00'),
        )

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_BLOQUEADO_READ_ONLY)
        self.assertEqual(resultado.capacidad_status, ESTADO_RECHAZADO)
        self.assertIn('capacidad_contractual:monto_supera_valor_pendiente_cobrar', resultado.razones)
        self.assertIn('capacidad_contractual:monto_supera_valor_pendiente_cobrar', resultado.bloqueos)
        self.assertEqual(resultado.capacidad_contractual['eligible'], False)

    def test_falla_si_no_hay_empresa_seleccionada(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales(empresa=None, empresa_contratante_nombre='Empresa Legacy')

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertIn('capacidad_contractual:empresa_requerida', resultado.razones)

    def test_score_queda_pendiente(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertEqual(resultado.score_status, ESTADO_EVALUADO_READ_ONLY)
        self.assertEqual(resultado.score_resultado['version_configuracion'], 'prestadores_score_v2_2026_06')
        self.assertEqual(resultado.score_resultado['datacredito_status'], ESTADO_PENDIENTE)

    def test_datacredito_queda_pendiente(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertEqual(resultado.datacredito_status, ESTADO_PENDIENTE)

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='bueno',
    )
    def test_score_alto_capacidad_ok_datacredito_ok_preaprueba_read_only(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertTrue(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_PREAPROBADO_READ_ONLY)
        self.assertFalse(resultado.requiere_revision_manual)
        self.assertEqual(resultado.datacredito_status, 'DISPONIBLE')
        self.assertEqual(resultado.score_status, ESTADO_EVALUADO_READ_ONLY)
        self.assertEqual(resultado.score_resultado['banda']['nombre'], 'PREMIUM')
        self.assertEqual(resultado.monto_maximo_sugerido, Decimal('1000000.00'))
        self.assertEqual(resultado.plazo_maximo_sugerido, 4)
        self.assertEqual(resultado.fuente, 'predecision_prestadores_read_only')

    @override_settings(
        CONTRACTORS_DATACREDITO_ENABLED=True,
        CONTRACTORS_DATACREDITO_PROVIDER='mock',
        CONTRACTORS_DATACREDITO_MOCK_SCENARIO='no_disponible',
    )
    def test_datacredito_no_disponible_requiere_revision_manual(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertIn('datacredito:no_disponible', resultado.advertencias)
        self.assertIn('datacredito', resultado.score_resultado['componentes_pendientes'])

    def test_score_revision_requiere_revision_manual(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        resultado_datacredito = ResultadoDatacreditoPrestador(
            disponible=True,
            fuente='mock',
            score_externo=100,
            score_normalizado_0_1000=100,
            nivel_riesgo='ALTO',
            requiere_revision_manual=False,
            metadata_segura={'solicitud_id': self.solicitud.id},
        )

        with patch('contractors.services.predecision.consultar_datacredito_prestador', return_value=resultado_datacredito):
            resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertEqual(resultado.score_resultado['banda']['nombre'], 'REVISION')
        self.assertIn('score:revision_manual', resultado.advertencias)

    def test_capacidad_bloqueada_no_evalua_score(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales(valor_pendiente_cobrar=Decimal('500000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.score_status, ESTADO_NO_EVALUADO)
        self.assertEqual(resultado.score_resultado, {})

    def test_sin_credito_previo_riesgo_no_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertEqual(resultado.riesgo['status'], ESTADO_APROBADO)
        self.assertEqual(resultado.riesgo['reason'], 'sin_credito_previo')

    def test_nuevo_credito_con_credito_previo_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._credito_previo(porcentaje_pagado=Decimal('40.00'), saldo_pendiente=Decimal('600000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertEqual(resultado.riesgo['reason'], 'credito_previo_existente_requiere_escenario')
        self.assertIn('riesgo:credito_previo_existente_requiere_escenario', resultado.razones)
        self.assertIsNone(resultado.riesgo['segundo_credito'])
        self.assertIsNone(resultado.riesgo['recogida_cartera'])

    def test_segundo_credito_sin_credito_previo_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO)

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertEqual(resultado.riesgo['reason'], 'no_existe_credito_previo')
        self.assertIn('riesgo:no_existe_credito_previo', resultado.razones)

    def test_segundo_credito_con_menos_de_40_por_ciento_pagado_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO)
        self._credito_previo(porcentaje_pagado=Decimal('20.00'), saldo_pendiente=Decimal('800000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertIn('riesgo:minimo_pagado_no_cumplido', resultado.razones)
        self.assertIsNone(resultado.riesgo['recogida_cartera'])

    def test_segundo_credito_con_40_por_ciento_o_mas_pagado_no_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO)
        credito = self._credito_previo(porcentaje_pagado=Decimal('40.00'), saldo_pendiente=Decimal('600000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertEqual(resultado.riesgo['status'], ESTADO_APROBADO)
        self.assertEqual(resultado.riesgo['credito_previo_id'], credito.id)
        self.assertEqual(resultado.riesgo['segundo_credito']['eligible'], True)
        self.assertIsNone(resultado.riesgo['recogida_cartera'])

    def test_segundo_credito_en_mora_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO)
        self._credito_previo(
            porcentaje_pagado=Decimal('50.00'),
            estado=Credito.EstadoCredito.EN_MORA,
            saldo_pendiente=Decimal('500000.00'),
        )

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertIn('riesgo:mora_activa_relevante', resultado.razones)

    def test_recogida_cartera_sin_credito_previo_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA)

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertEqual(resultado.riesgo['reason'], 'no_existe_credito_previo')
        self.assertIn('riesgo:no_existe_credito_previo', resultado.razones)

    def test_recogida_cartera_valida_no_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA)
        self._credito_previo(porcentaje_pagado=Decimal('50.00'), saldo_pendiente=Decimal('500000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.decision, DECISION_REQUIERE_REVISION_MANUAL)
        self.assertIsNone(resultado.riesgo['segundo_credito'])
        self.assertEqual(resultado.riesgo['recogida_cartera']['applies'], True)
        self.assertEqual(resultado.riesgo['recogida_cartera']['eligible'], True)
        self.assertEqual(resultado.riesgo['recogida_cartera']['net_disbursement_amount'], Decimal('500000.00'))

    def test_recogida_cartera_con_monto_menor_o_igual_a_saldo_bloquea(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA)
        self.solicitud.requested_amount = Decimal('500000.00')
        self.solicitud.save(update_fields=['requested_amount'])
        self._credito_previo(porcentaje_pagado=Decimal('50.00'), saldo_pendiente=Decimal('600000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertFalse(resultado.elegible)
        self.assertEqual(resultado.riesgo['status'], ESTADO_RECHAZADO)
        self.assertIn('riesgo:monto_solicitado_menor_o_igual_al_saldo', resultado.razones)
        self.assertIsNone(resultado.riesgo['segundo_credito'])

    def test_recogida_cartera_no_evalua_segundo_credito_como_decision_final(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA)
        self._credito_previo(porcentaje_pagado=Decimal('50.00'), saldo_pendiente=Decimal('500000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertIsNone(resultado.riesgo['segundo_credito'])
        self.assertIsNotNone(resultado.riesgo['recogida_cartera'])

    def test_segundo_credito_no_evalua_recogida_como_decision_final(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        self._definir_escenario(ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO)
        self._credito_previo(porcentaje_pagado=Decimal('50.00'), saldo_pendiente=Decimal('500000.00'))

        resultado = evaluar_predecision_contratista(self.solicitud)

        self.assertIsNotNone(resultado.riesgo['segundo_credito'])
        self.assertIsNone(resultado.riesgo['recogida_cartera'])

    def test_no_crea_modelos_financieros_ni_cambia_estado(self):
        self._documentos_minimos_aprobados()
        self._datos_contractuales()
        estado_inicial = self.solicitud.status
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'pagare': Pagare.objects.count(),
        }

        evaluar_predecision_contratista(self.solicitud)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.status, estado_inicial)
        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(Pagare.objects.count(), conteos_antes['pagare'])

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos.models import Credito
from risk.selectors import obtener_credito_vigente_para_recogida_cartera
from risk.services.portfolio_takeover import (
    MOTIVO_CREDITO_VIGENTE_SIN_SALDO,
    MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO,
    MOTIVO_MORA_ACTIVA_RELEVANTE,
    MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO,
    MOTIVO_RECOGIDA_CARTERA_APLICA,
    MOTIVO_SIN_CREDITO_VIGENTE,
    PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA,
    PortfolioTakeoverService,
    ServicioRecogidaCartera,
    SolicitudRecogidaCartera,
    evaluate_portfolio_takeover,
    evaluar_recogida_cartera,
)


User = get_user_model()


class RecogidaCarteraTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='risk-portfolio-takeover',
            email='risk.portfolio@example.com',
        )

    def crear_credito(self, **overrides):
        data = {
            'usuario': self.user,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'monto_solicitado': Decimal('1000000.00'),
            'monto_aprobado': Decimal('1000000.00'),
            'plazo_solicitado': 6,
            'plazo': 6,
            'capital_pendiente': Decimal('300000.00'),
            'saldo_pendiente': Decimal('300000.00'),
            'total_a_pagar': Decimal('1200000.00'),
        }
        data.update(overrides)
        return Credito.objects.create(**data)

    def test_sin_credito_vigente_no_aplica_y_es_elegible(self):
        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertFalse(resultado['applies'])
        self.assertTrue(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_SIN_CREDITO_VIGENTE)
        self.assertIsNone(resultado['current_credit_id'])
        self.assertIsNone(resultado['outstanding_balance'])
        self.assertIsNone(resultado['takeover_amount'])
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('1000000.00'))
        self.assertEqual(resultado['requested_amount'], Decimal('1000000.00'))

    def test_credito_vigente_sin_saldo_no_aplica(self):
        credito = self.crear_credito(
            saldo_pendiente=Decimal('0.00'),
            capital_pendiente=Decimal('0.00'),
        )

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertFalse(resultado['applies'])
        self.assertTrue(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_CREDITO_VIGENTE_SIN_SALDO)
        self.assertEqual(resultado['current_credit_id'], credito.id)
        self.assertEqual(resultado['outstanding_balance'], Decimal('0.00'))
        self.assertEqual(resultado['takeover_amount'], Decimal('0.00'))
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('1000000.00'))

    def test_credito_vigente_con_saldo_a_recoger(self):
        credito = self.crear_credito(saldo_pendiente=Decimal('300000.00'))

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(resultado['applies'])
        self.assertTrue(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_RECOGIDA_CARTERA_APLICA)
        self.assertEqual(resultado['current_credit_id'], credito.id)
        self.assertEqual(resultado['outstanding_balance'], Decimal('300000.00'))
        self.assertEqual(resultado['takeover_amount'], Decimal('300000.00'))
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('700000.00'))
        self.assertEqual(resultado['paid_percentage'], Decimal('70.00'))
        self.assertEqual(resultado['required_percentage'], PORCENTAJE_MINIMO_PAGADO_RECOGIDA_CARTERA)

    def test_recogida_invalida_por_mora(self):
        credito = self.crear_credito(
            estado=Credito.EstadoCredito.EN_MORA,
            capital_pendiente=Decimal('300000.00'),
            saldo_pendiente=Decimal('300000.00'),
        )

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(resultado['applies'])
        self.assertFalse(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_MORA_ACTIVA_RELEVANTE)
        self.assertEqual(resultado['current_credit_id'], credito.id)

    def test_recogida_invalida_por_porcentaje_pagado(self):
        credito = self.crear_credito(
            capital_pendiente=Decimal('700000.00'),
            saldo_pendiente=Decimal('700000.00'),
        )

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(resultado['applies'])
        self.assertFalse(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_MINIMO_PAGADO_NO_CUMPLIDO)
        self.assertEqual(resultado['current_credit_id'], credito.id)
        self.assertEqual(resultado['paid_percentage'], Decimal('30.00'))
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('0.00'))

    def test_monto_solicitado_menor_al_saldo_no_es_elegible(self):
        credito = self.crear_credito(saldo_pendiente=Decimal('300000.00'))

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('200000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(resultado['applies'])
        self.assertFalse(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO)
        self.assertEqual(resultado['current_credit_id'], credito.id)
        self.assertEqual(resultado['outstanding_balance'], Decimal('300000.00'))
        self.assertEqual(resultado['takeover_amount'], Decimal('300000.00'))
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('0.00'))

    def test_monto_solicitado_igual_al_saldo_no_es_elegible(self):
        self.crear_credito(saldo_pendiente=Decimal('300000.00'))

        resultado = evaluar_recogida_cartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('300000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertTrue(resultado['applies'])
        self.assertFalse(resultado['eligible'])
        self.assertEqual(resultado['reason'], MOTIVO_MONTO_SOLICITADO_MENOR_O_IGUAL_AL_SALDO)
        self.assertEqual(resultado['net_disbursement_amount'], Decimal('0.00'))

    def test_selector_ignora_credito_pagado_y_usa_credito_activo(self):
        self.crear_credito(
            estado=Credito.EstadoCredito.PAGADO,
            saldo_pendiente=Decimal('0.00'),
            capital_pendiente=Decimal('0.00'),
        )
        credito_activo = self.crear_credito(saldo_pendiente=Decimal('250000.00'))

        self.assertEqual(
            obtener_credito_vigente_para_recogida_cartera(
                cliente_id=self.user.id,
                linea_credito=Credito.LineaCredito.LIBRANZA,
            ),
            credito_activo,
        )

    def test_clase_de_servicio_retorna_mismo_payload(self):
        self.crear_credito(saldo_pendiente=Decimal('300000.00'))
        solicitud = SolicitudRecogidaCartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertEqual(
            ServicioRecogidaCartera().evaluar(solicitud),
            evaluar_recogida_cartera(
                cliente_id=self.user.id,
                monto_solicitado=Decimal('1000000.00'),
                linea_credito=Credito.LineaCredito.LIBRANZA,
            ),
        )

    def test_alias_legacy_en_ingles_siguen_funcionando(self):
        self.crear_credito(saldo_pendiente=Decimal('300000.00'))
        solicitud = SolicitudRecogidaCartera(
            cliente_id=self.user.id,
            monto_solicitado=Decimal('1000000.00'),
            linea_credito=Credito.LineaCredito.LIBRANZA,
        )

        self.assertEqual(
            PortfolioTakeoverService().evaluate(solicitud),
            evaluate_portfolio_takeover(
                customer_id=self.user.id,
                requested_amount=Decimal('1000000.00'),
                product_type=Credito.LineaCredito.LIBRANZA,
            ),
        )

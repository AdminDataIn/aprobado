from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from gestion_creditos.credit_services import marcar_creditos_en_mora
from gestion_creditos.models import Credito
from gestion_creditos.tasks import enviar_alertas_mora_task, enviar_recordatorios_pago_task


class LibranzaAutomationFlagsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='flags-user', password='123', email='flags@example.com')

    def _crear_credito(self, numero_credito, linea, estado, fecha_pago):
        return Credito.objects.create(
            usuario=self.user,
            numero_credito=numero_credito,
            linea=linea,
            estado=estado,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=6,
            fecha_proximo_pago=fecha_pago,
        )

    @override_settings(LIBRANZA_AUTO_MARK_MORA_ENABLED=False)
    @patch('gestion_creditos.credit_services.gestionar_cambio_estado_credito')
    def test_marcar_mora_omite_libranza_si_flag_esta_apagado(self, cambio_estado_mock):
        hoy = timezone.localdate()
        credito_libranza = self._crear_credito(
            'CR-2099-00100',
            Credito.LineaCredito.LIBRANZA,
            Credito.EstadoCredito.ACTIVO,
            hoy - timedelta(days=2),
        )
        credito_emprendimiento = self._crear_credito(
            'CR-2099-00101',
            Credito.LineaCredito.EMPRENDIMIENTO,
            Credito.EstadoCredito.ACTIVO,
            hoy - timedelta(days=2),
        )

        total = marcar_creditos_en_mora()

        self.assertEqual(total, 1)
        cambio_estado_mock.assert_called_once()
        self.assertEqual(cambio_estado_mock.call_args.kwargs['credito'].pk, credito_emprendimiento.pk)
        self.assertNotEqual(cambio_estado_mock.call_args.kwargs['credito'].pk, credito_libranza.pk)

    @override_settings(LIBRANZA_PAYMENT_REMINDERS_ENABLED=False)
    @patch('gestion_creditos.tasks.enviar_recordatorio_pago', return_value=True)
    @patch('gestion_creditos.tasks.Credito.objects.filter')
    def test_recordatorios_omiten_libranza_si_flag_esta_apagado(self, filter_mock, enviar_mock):
        fecha_objetivo = timezone.localdate() + timedelta(days=3)
        credito_libranza = self._crear_credito(
            'CR-2099-00102',
            Credito.LineaCredito.LIBRANZA,
            Credito.EstadoCredito.ACTIVO,
            fecha_objetivo,
        )
        credito_adelanto = self._crear_credito(
            'CR-2099-00102A',
            Credito.LineaCredito.ADELANTO_NOMINA,
            Credito.EstadoCredito.ACTIVO,
            fecha_objetivo,
        )
        credito_emprendimiento = self._crear_credito(
            'CR-2099-00103',
            Credito.LineaCredito.EMPRENDIMIENTO,
            Credito.EstadoCredito.ACTIVO,
            fecha_objetivo,
        )

        class FakeQuery(list):
            def select_related(self, *args, **kwargs):
                return self

        filter_mock.return_value = FakeQuery([credito_libranza, credito_adelanto, credito_emprendimiento])

        resultado = enviar_recordatorios_pago_task()

        self.assertEqual(resultado['recordatorios_enviados'], 2)
        self.assertEqual(enviar_mock.call_count, 2)
        enviar_mock.assert_any_call(credito_emprendimiento, 3)
        self.assertNotIn(credito_libranza, [args[0] for args, _kwargs in enviar_mock.call_args_list])
        self.assertNotIn(credito_adelanto, [args[0] for args, _kwargs in enviar_mock.call_args_list])

    @override_settings(LIBRANZA_MORA_ALERTS_ENABLED=False)
    @patch('gestion_creditos.tasks.enviar_alerta_mora', return_value=True)
    @patch('gestion_creditos.tasks.Credito.objects.filter')
    def test_alertas_mora_omiten_libranza_si_flag_esta_apagado(self, filter_mock, enviar_mock):
        hoy = timezone.now().date()
        credito_libranza = self._crear_credito(
            'CR-2099-00104',
            Credito.LineaCredito.LIBRANZA,
            Credito.EstadoCredito.EN_MORA,
            hoy - timedelta(days=1),
        )
        credito_adelanto = self._crear_credito(
            'CR-2099-00104A',
            Credito.LineaCredito.ADELANTO_NOMINA,
            Credito.EstadoCredito.EN_MORA,
            hoy - timedelta(days=1),
        )
        credito_emprendimiento = self._crear_credito(
            'CR-2099-00105',
            Credito.LineaCredito.EMPRENDIMIENTO,
            Credito.EstadoCredito.EN_MORA,
            hoy - timedelta(days=1),
        )

        class FakeQuery(list):
            def select_related(self, *args, **kwargs):
                return self

        filter_mock.return_value = FakeQuery([credito_libranza, credito_adelanto, credito_emprendimiento])

        resultado = enviar_alertas_mora_task()
        dias_mora_esperados = credito_emprendimiento.dias_en_mora

        self.assertEqual(resultado['alertas_enviadas'], 1)
        enviar_mock.assert_called_once_with(credito_emprendimiento, dias_mora_esperados)
        self.assertNotIn(credito_libranza, [args[0] for args, _kwargs in enviar_mock.call_args_list])
        self.assertNotIn(credito_adelanto, [args[0] for args, _kwargs in enviar_mock.call_args_list])

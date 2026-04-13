from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gestion_creditos.models import Credito, CuotaAmortizacion, HistorialPago, ReestructuracionCredito


class AjustarFechasPagoLibranzaCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='credito_user_cmd', password='123')

    def _crear_credito_libranza(self, numero_credito, fecha_base, tipo_regla=Credito.TipoReglaCredito.NORMAL):
        credito = Credito.objects.create(
            usuario=self.user,
            numero_credito=numero_credito,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=3,
            monto_aprobado=Decimal('1000000.00'),
            plazo=3,
            tasa_interes=Decimal('1.90'),
            valor_cuota=Decimal('350000.00'),
            total_a_pagar=Decimal('1050000.00'),
            saldo_pendiente=Decimal('1050000.00'),
            capital_pendiente=Decimal('1000000.00'),
            fecha_proximo_pago=fecha_base,
            fecha_desembolso=timezone.make_aware(datetime.combine(fecha_base, datetime.min.time())),
            tipo_regla_credito=tipo_regla,
        )
        for idx in range(1, 4):
            CuotaAmortizacion.objects.create(
                credito=credito,
                numero_cuota=idx,
                fecha_vencimiento=fecha_base + relativedelta(months=idx - 1),
                capital_a_pagar=Decimal('300000.00'),
                interes_a_pagar=Decimal('50000.00'),
                valor_cuota=Decimal('350000.00'),
                saldo_capital_pendiente=max(Decimal('0.00'), Decimal('1000000.00') - (Decimal('300000.00') * idx)),
            )
        return credito

    def test_dry_run_reporta_credito_listo_para_aplicar(self):
        credito = self._crear_credito_libranza('CR-2099-00007', date(2026, 3, 10))
        out = StringIO()

        call_command(
            'ajustar_fechas_pago_libranza',
            '--creditos', credito.numero_credito,
            stdout=out,
        )

        credito.refresh_from_db()
        self.assertIn('listo para aplicar', out.getvalue())
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 3, 10))

    def test_apply_reprograma_solo_cuotas_pendientes(self):
        credito = self._crear_credito_libranza('CR-2099-00005', date(2026, 3, 10))
        out = StringIO()

        call_command(
            'ajustar_fechas_pago_libranza',
            '--creditos', credito.numero_credito,
            '--apply',
            stdout=out,
        )

        credito.refresh_from_db()
        cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 4, 1))
        self.assertEqual(cuotas[0].fecha_vencimiento, date(2026, 4, 1))
        self.assertEqual(cuotas[1].fecha_vencimiento, date(2026, 5, 1))
        self.assertEqual(cuotas[2].fecha_vencimiento, date(2026, 6, 1))
        self.assertIn('ajuste aplicado correctamente', out.getvalue())

    def test_omite_credito_con_pago_exitoso(self):
        credito = self._crear_credito_libranza('CR-2099-00004', date(2026, 3, 10))
        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('350000.00'),
            referencia_pago='PAGO-TEST-001',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )
        out = StringIO()

        call_command(
            'ajustar_fechas_pago_libranza',
            '--creditos', credito.numero_credito,
            '--apply',
            stdout=out,
        )

        credito.refresh_from_db()
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 3, 10))
        self.assertIn('pago(s) exitoso(s)', out.getvalue())

    def test_omite_credito_con_reestructuracion(self):
        credito = self._crear_credito_libranza('CR-2099-00003', date(2026, 3, 10))
        ReestructuracionCredito.objects.create(
            credito=credito,
            tipo_abono=ReestructuracionCredito.TipoAbono.NORMAL,
            monto_abonado=Decimal('100000.00'),
            plan_anterior={'cuotas': []},
            plan_nuevo={'cuotas': []},
            saldo_pendiente_anterior=Decimal('1050000.00'),
            capital_pendiente_anterior=Decimal('1000000.00'),
            saldo_pendiente_nuevo=Decimal('950000.00'),
            capital_pendiente_nuevo=Decimal('900000.00'),
            plazo_restante_anterior=3,
            plazo_restante_nuevo=3,
            ahorro_intereses=Decimal('0.00'),
        )
        out = StringIO()

        call_command(
            'ajustar_fechas_pago_libranza',
            '--creditos', credito.numero_credito,
            '--apply',
            stdout=out,
        )

        credito.refresh_from_db()
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 3, 10))
        self.assertIn('reestructuracion(es)', out.getvalue())

    def test_omite_credito_excluido(self):
        credito = self._crear_credito_libranza('CR-2099-00008', date(2026, 3, 10), tipo_regla=Credito.TipoReglaCredito.ESPECIAL)
        out = StringIO()

        call_command(
            'ajustar_fechas_pago_libranza',
            '--creditos', credito.numero_credito,
            '--excluir-creditos', credito.numero_credito,
            '--apply',
            stdout=out,
        )

        self.assertIn('Exclusion explicita', out.getvalue())

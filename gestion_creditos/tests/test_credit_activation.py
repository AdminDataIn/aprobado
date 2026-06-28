from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CuotaAmortizacion
from gestion_creditos.services.credit_activation import activar_credito


User = get_user_model()


class CreditActivationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='activation-user', password='123456')

    def test_activacion_normal_calcula_componentes_y_tabla(self):
        credito = self.create_credito(
            monto=Decimal('1000000.00'),
            plazo=12,
            tasa=Decimal('2.00'),
        )

        activar_credito(credito)

        credito.refresh_from_db()
        self.assertEqual(credito.tasa_interes, Decimal('2.00'))
        self.assertEqual(credito.plazo, 12)
        self.assertEqual(credito.comision, Decimal('100000.0000'))
        self.assertEqual(credito.iva_comision, Decimal('19000.000000'))
        self.assertEqual(credito.saldo_pendiente, Decimal('1119000.000000'))
        self.assertEqual(credito.capital_pendiente, Decimal('1000000.00'))
        self.assertIsNotNone(credito.valor_cuota)
        self.assertIsNotNone(credito.total_a_pagar)
        self.assertIsNotNone(credito.fecha_desembolso)
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito).count(), 12)

    def test_wrapper_legacy_y_servicio_nuevo_son_equivalentes(self):
        credito_directo = self.create_credito(
            monto=Decimal('2000000.00'),
            plazo=10,
            tasa=Decimal('1.75'),
        )
        credito_legacy = self.create_credito(
            monto=Decimal('2000000.00'),
            plazo=10,
            tasa=Decimal('1.75'),
        )

        activar_credito(credito_directo)
        credit_services.activar_credito(credito_legacy)

        credito_directo.refresh_from_db()
        credito_legacy.refresh_from_db()
        fields = [
            'tasa_interes',
            'plazo',
            'comision',
            'iva_comision',
            'total_a_pagar',
            'valor_cuota',
            'saldo_pendiente',
            'capital_pendiente',
            'fecha_proximo_pago',
        ]
        for field in fields:
            with self.subTest(field=field):
                self.assertEqual(getattr(credito_directo, field), getattr(credito_legacy, field))
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito_directo).count(), 10)
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito_legacy).count(), 10)

    def test_activacion_especial_respeta_tasa_plazo_y_comision_persistida(self):
        credito = self.create_credito(
            monto=Decimal('5000000.00'),
            plazo=12,
            tasa=Decimal('1.00'),
            tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL,
            plazo_forzado=24,
            tasa_forzada=Decimal('3.00'),
            comision=Decimal('250000.00'),
            iva_comision=Decimal('47500.00'),
        )

        activar_credito(credito)

        credito.refresh_from_db()
        self.assertEqual(credito.tasa_interes, Decimal('3.00'))
        self.assertEqual(credito.plazo, 24)
        self.assertEqual(credito.plazo_forzado, 24)
        self.assertEqual(credito.tasa_forzada, Decimal('3.00'))
        self.assertEqual(credito.comision, Decimal('250000.00'))
        self.assertEqual(credito.iva_comision, Decimal('47500.00'))
        self.assertEqual(credito.saldo_pendiente, Decimal('5297500.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('5000000.00'))
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito).count(), 24)

    def create_credito(
        self,
        *,
        monto,
        plazo,
        tasa,
        tipo_regla_credito=Credito.TipoReglaCredito.NORMAL,
        plazo_forzado=None,
        tasa_forzada=None,
        comision=None,
        iva_comision=None,
    ):
        return Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=monto,
            monto_aprobado=monto,
            plazo_solicitado=plazo,
            plazo=plazo,
            tasa_interes=tasa,
            tipo_regla_credito=tipo_regla_credito,
            plazo_forzado=plazo_forzado,
            tasa_forzada=tasa_forzada,
            comision=comision,
            iva_comision=iva_comision,
        )

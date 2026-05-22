from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from gestion_creditos.services import capacidad_descuento_service as legacy_capacity
from libranza.services import payment_capacity
from libranza.services.payment_capacity import (
    LibranzaCapacityInput,
    LibranzaPaymentCapacityService,
    evaluar_capacidad_descuento_libranza,
)


User = get_user_model()


class PaymentCapacityMirrorTests(SimpleTestCase):
    @override_settings(ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE='25')
    def test_calcular_capacidad_descuento_matches_legacy_import(self):
        cases = [
            {
                'salario': Decimal('1750905'),
                'auxilio_transporte': Decimal('249095'),
                'descuentos': Decimal('140072'),
                'monto_solicitado': Decimal('400000'),
            },
            {
                'salario': '2000000.50',
                'auxilio_transporte': '0',
                'descuentos': '250000.25',
                'monto_solicitado': '100000',
            },
            {
                'salario': 'invalid',
                'auxilio_transporte': '100000',
                'descuentos': '300000',
                'monto_solicitado': None,
            },
        ]

        for params in cases:
            with self.subTest(params=params):
                self.assertEqual(
                    payment_capacity.calcular_capacidad_descuento(**params),
                    legacy_capacity.calcular_capacidad_descuento(**params),
                )

    @override_settings(ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE='30')
    def test_calcular_capacidad_descuento_uses_same_settings_value_as_legacy(self):
        self.assertEqual(
            payment_capacity.calcular_capacidad_descuento(
                salario=Decimal('1800000'),
                auxilio_transporte=Decimal('200000'),
                descuentos=Decimal('100000'),
                monto_solicitado=Decimal('500000'),
            ),
            legacy_capacity.calcular_capacidad_descuento(
                salario=Decimal('1800000'),
                auxilio_transporte=Decimal('200000'),
                descuentos=Decimal('100000'),
                monto_solicitado=Decimal('500000'),
            ),
        )

    @override_settings(ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE='25')
    def test_simular_adelanto_nomina_matches_legacy_import(self):
        cases = [
            {
                'salario': Decimal('1750905'),
                'auxilio_transporte': Decimal('249095'),
                'descuentos': Decimal('140072'),
                'dias_adelanto': 5,
                'tasa_mensual': Decimal('1.9'),
                'porcentaje_comision': Decimal('10'),
            },
            {
                'salario': '0',
                'auxilio_transporte': '0',
                'descuentos': '0',
                'dias_adelanto': 3,
                'tasa_mensual': '2.1',
                'porcentaje_comision': '8',
            },
        ]

        for params in cases:
            with self.subTest(params=params):
                self.assertEqual(
                    payment_capacity.simular_adelanto_nomina(**params),
                    legacy_capacity.simular_adelanto_nomina(**params),
                )

    @override_settings(ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE='27.5')
    def test_obtener_porcentaje_capacidad_descuento_matches_legacy_import(self):
        self.assertEqual(
            payment_capacity.obtener_porcentaje_capacidad_descuento(),
            legacy_capacity.obtener_porcentaje_capacidad_descuento(),
        )


class LibranzaPaymentCapacityTests(SimpleTestCase):
    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_capacidad_disponible_positiva(self):
        result = evaluar_capacidad_descuento_libranza(
            LibranzaCapacityInput(
                ingreso_base=Decimal('2000000.00'),
                descuentos_actuales=Decimal('200000.00'),
                cuota_actual_libranza=Decimal('100000.00'),
                cuota_proyectada=Decimal('300000.00'),
            )
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, 'capacidad_disponible')
        self.assertEqual(result.capacidad_maxima, Decimal('1000000.00'))
        self.assertEqual(result.cuota_maxima_permitida, Decimal('700000.00'))
        self.assertEqual(result.capacidad_disponible, Decimal('400000.00'))
        self.assertEqual(result.porcentaje_comprometido, Decimal('30.00'))

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_capacidad_insuficiente(self):
        result = evaluar_capacidad_descuento_libranza(
            {
                'ingreso_base': Decimal('1000000.00'),
                'descuentos_actuales': Decimal('250000.00'),
                'cuota_actual_libranza': Decimal('150000.00'),
                'cuota_proyectada': Decimal('200000.00'),
            }
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, 'capacidad_insuficiente')
        self.assertEqual(result.capacidad_maxima, Decimal('500000.00'))
        self.assertEqual(result.capacidad_disponible, Decimal('-100000.00'))

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_cuota_proyectada_supera_limite(self):
        result = evaluar_capacidad_descuento_libranza(
            LibranzaCapacityInput(
                ingreso_base=Decimal('1000000.00'),
                descuentos_actuales=Decimal('0.00'),
                cuota_actual_libranza=Decimal('0.00'),
                cuota_proyectada=Decimal('600000.00'),
            )
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, 'cuota_proyectada_supera_limite')

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_descuentos_actuales_reducen_capacidad(self):
        base = evaluar_capacidad_descuento_libranza(
            LibranzaCapacityInput(
                ingreso_base=Decimal('2000000.00'),
                cuota_proyectada=Decimal('300000.00'),
            )
        )
        con_descuentos = evaluar_capacidad_descuento_libranza(
            LibranzaCapacityInput(
                ingreso_base=Decimal('2000000.00'),
                descuentos_actuales=Decimal('300000.00'),
                cuota_proyectada=Decimal('300000.00'),
            )
        )

        self.assertEqual(base.capacidad_disponible, Decimal('700000.00'))
        self.assertEqual(con_descuentos.capacidad_disponible, Decimal('400000.00'))


class LibranzaPaymentCapacitySelectorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='libranza-capacity')
        self.empresa = Empresa.objects.create(nombre='Empresa Libranza', convenio_activo=True)

    def crear_credito_libranza(self, **overrides):
        data = {
            'usuario': self.user,
            'linea': Credito.LineaCredito.LIBRANZA,
            'estado': Credito.EstadoCredito.ACTIVO,
            'monto_solicitado': Decimal('1000000.00'),
            'monto_aprobado': Decimal('1000000.00'),
            'plazo_solicitado': 6,
            'plazo': 6,
            'capital_pendiente': Decimal('600000.00'),
            'saldo_pendiente': Decimal('600000.00'),
            'valor_cuota': Decimal('180000.00'),
            'total_a_pagar': Decimal('1080000.00'),
        }
        data.update(overrides)
        credito = Credito.objects.create(**data)
        file_stub = SimpleUploadedFile('doc.pdf', b'file')
        CreditoLibranza.objects.create(
            credito=credito,
            nombres='Cliente',
            apellidos='Prueba',
            cedula='123456',
            direccion='Calle 1',
            telefono='3000000000',
            correo_electronico='cliente@example.com',
            empresa=self.empresa,
            ingresos_mensuales=Decimal('2000000.00'),
            cedula_frontal=file_stub,
            cedula_trasera=file_stub,
            certificado_bancario=file_stub,
        )
        return credito

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_caso_sin_credito_vigente(self):
        result = LibranzaPaymentCapacityService().evaluate_for_customer(
            cliente_id=self.user.id,
            ingreso_base=Decimal('2000000.00'),
            cuota_proyectada=Decimal('300000.00'),
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.cuota_actual_libranza, Decimal('0.00'))
        self.assertIsNone(result.metadata['current_credit_id'])

    @override_settings(LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE='50')
    def test_caso_con_credito_vigente(self):
        credito = self.crear_credito_libranza()

        result = LibranzaPaymentCapacityService().evaluate_for_customer(
            cliente_id=self.user.id,
            ingreso_base=Decimal('2000000.00'),
            cuota_proyectada=Decimal('300000.00'),
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.cuota_actual_libranza, Decimal('180000.00'))
        self.assertEqual(result.metadata['current_credit_id'], credito.id)

from datetime import date
from decimal import Decimal
import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, Empresa, HistorialPago, LotePagoEmpresa


User = get_user_model()


class PagosOfflineServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tesoreria', password='123456')
        self.empresa = Empresa.objects.create(nombre='FERTOBRA TEST')

    def _crear_credito_libranza(self, numero, saldo, cuota, cuotas_pagadas=0, cuotas_totales=2):
        credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito=numero,
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=cuotas_totales,
            comision=Decimal('50.00'),
            iva_comision=Decimal('9.50'),
            valor_cuota=Decimal(str(cuota)),
            total_a_pagar=Decimal(str(cuota)) * cuotas_totales,
            saldo_pendiente=Decimal(str(saldo)),
            capital_pendiente=Decimal(str(saldo)),
            plazo=cuotas_totales,
            tasa_interes=Decimal('2.00'),
            fecha_proximo_pago=date(2026, 4, 30),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=self.empresa,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico='pago.offline@example.com',
            cedula=f'{numero[-3:]}123',
            nombres='Pago',
            apellidos='Offline',
            cedula_frontal=SimpleUploadedFile('cedula_frontal.pdf', b'front', content_type='application/pdf'),
            cedula_trasera=SimpleUploadedFile('cedula_trasera.pdf', b'back', content_type='application/pdf'),
            certificado_bancario=SimpleUploadedFile('cert_bancario.pdf', b'bank', content_type='application/pdf'),
        )
        for numero_cuota in range(1, cuotas_totales + 1):
            pagada = numero_cuota <= cuotas_pagadas
            CuotaAmortizacion.objects.create(
                credito=credito,
                numero_cuota=numero_cuota,
                fecha_vencimiento=date(2026, numero_cuota if numero_cuota <= 12 else 12, 28),
                capital_a_pagar=Decimal('80.00'),
                interes_a_pagar=Decimal('20.00'),
                valor_cuota=Decimal(str(cuota)),
                saldo_capital_pendiente=Decimal('0.00') if numero_cuota == cuotas_totales else Decimal('100.00'),
                pagada=pagada,
                monto_pagado=Decimal(str(cuota)) if pagada else None,
                fecha_pago=timezone.now() if pagada else None,
            )
        return credito

    def test_registrar_pago_offline_marca_credito_pagado_si_cancela_ultima_cuota(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0001',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=1,
            cuotas_totales=2,
        )

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('100.00'),
            referencia_pago='OFFLINE-TEST-0001',
            metodo_pago=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
            empresa=self.empresa,
            notas='Pago final por transferencia',
        )

        self.assertTrue(created)
        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PAGADO)
        self.assertEqual(credito.saldo_pendiente, Decimal('0.00'))
        self.assertEqual(pago.metodo_pago, HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA)
        self.assertEqual(pago.origen_registro, HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN)

    def test_procesar_pagos_masivos_archivo_crea_lote_y_aplica_pago(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0002',
            saldo='200.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )
        csv_content = "numero_credito,monto_a_pagar,referencia_pago,fecha_pago\nCR-TEST-0002,200.00,FERTOBRA-LOTE-01,2026-03-31\n"
        archivo = SimpleUploadedFile('pagos_fertobra.csv', csv_content.encode('utf-8'), content_type='text/csv')

        pagos_exitosos, errores, lote = credit_services.procesar_pagos_masivos_archivo(
            archivo,
            self.empresa,
            usuario=self.user,
            notas='Recaudo quincena',
        )

        self.assertEqual(pagos_exitosos, 1)
        self.assertEqual(errores, [])
        self.assertIsNotNone(lote)
        self.assertTrue(LotePagoEmpresa.objects.filter(pk=lote.pk).exists())

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PAGADO)
        pago = HistorialPago.objects.get(referencia_pago='FERTOBRA-LOTE-01')
        self.assertEqual(pago.metodo_pago, HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA)
        self.assertEqual(pago.origen_registro, HistorialPago.OrigenRegistro.CARGA_MASIVA_EMPRESA)
        self.assertEqual(pago.lote_pago_id, lote.id)

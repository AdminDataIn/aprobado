from datetime import date
from decimal import Decimal
import io
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, Empresa, HistorialPago, LotePagoEmpresa


User = get_user_model()


class PagosOfflineServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tesoreria',
            email='tesoreria@aprobado.test',
            password='123456',
        )
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
        archivo = self._build_xlsx_file([
            ['numero_credito', 'monto_a_pagar', 'referencia_pago', 'fecha_pago'],
            ['CR-TEST-0002', '200.00', 'FERTOBRA-LOTE-01', '2026-03-31'],
        ])

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

    def test_crear_borrador_y_confirmar_lote_excel(self):
        self._crear_credito_libranza(
            numero='CR-TEST-0003',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=1,
            cuotas_totales=2,
        )
        archivo = self._build_xlsx_file([
            ['numero_credito', 'monto_a_pagar', 'referencia_pago', 'fecha_pago', 'nota'],
            ['CR-TEST-0003', '100.00', 'FERTOBRA-LOTE-02', '2026-03-31', 'Pago final'],
        ])

        pagos_validos, errores, lote = credit_services.crear_borrador_pagos_masivos_archivo(
            archivo,
            self.empresa,
            usuario=self.user,
        )

        self.assertEqual(errores, [])
        self.assertEqual(len(pagos_validos), 1)
        self.assertEqual(lote.estado, LotePagoEmpresa.EstadoLote.CARGADO)

        pagos_exitosos, errores = credit_services.procesar_lote_pago_empresa(
            lote,
            usuario=self.user,
            notas='Confirmado desde prueba',
        )

        self.assertEqual(errores, [])
        self.assertEqual(pagos_exitosos, 1)
        lote.refresh_from_db()
        self.assertEqual(lote.estado, LotePagoEmpresa.EstadoLote.PROCESADO)
        self.assertEqual(lote.pagos_aplicados, 1)

    def test_validacion_fecha_invalida_explica_formato_esperado(self):
        self._crear_credito_libranza(
            numero='CR-TEST-0004',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=1,
        )
        archivo = self._build_xlsx_file([
            ['cedula', 'monto_a_pagar', 'fecha_pago'],
            ['004123', '100.00', '125425'],
        ])

        pagos_validos, errores, _ = credit_services.validar_archivo_pagos_masivos(
            archivo,
            self.empresa,
        )

        self.assertEqual(pagos_validos, [])
        self.assertEqual(len(errores), 1)
        self.assertIn('DD/MM/AAAA', errores[0])
        self.assertIn('30/03/2026', errores[0])

    def test_referencia_automatica_es_corta_y_legible(self):
        self._crear_credito_libranza(
            numero='CR-TEST-0005',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=1,
        )
        archivo = self._build_xlsx_file([
            ['cedula', 'monto_a_pagar'],
            ['005123', '100.00'],
        ])

        pagos_validos, errores, _ = credit_services.validar_archivo_pagos_masivos(
            archivo,
            self.empresa,
        )

        self.assertEqual(errores, [])
        self.assertEqual(len(pagos_validos), 1)
        self.assertRegex(pagos_validos[0]['referencia_pago'], r'^PAG-[A-F0-9]{6}-2$')

    def test_pago_equivalente_a_dos_cuotas_marca_dos_y_mueve_vencimiento(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0006',
            saldo='300.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=3,
        )
        cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
        cuotas[0].fecha_vencimiento = date(2026, 4, 30)
        cuotas[1].fecha_vencimiento = date(2026, 5, 30)
        cuotas[2].fecha_vencimiento = date(2026, 6, 30)
        for cuota in cuotas:
            cuota.save(update_fields=['fecha_vencimiento'])

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('200.00'),
            referencia_pago='OFFLINE-TEST-0006',
            metodo_pago=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
            usuario=self.user,
            empresa=self.empresa,
            notas='Pago de dos cuotas',
        )

        self.assertTrue(created)
        credito.refresh_from_db()
        resumen = credit_services.obtener_resumen_pagos_credito(credito)
        self.assertEqual(resumen['cuotas_pagadas'], 2)
        self.assertEqual(resumen['cuotas_restantes'], 1)
        self.assertEqual(resumen['fecha_proximo_pago'], date(2026, 6, 30))
        self.assertEqual(credito.estado, Credito.EstadoCredito.ACTIVO)

    def test_pago_parcial_no_rompe_tabla_ni_marca_cuota_completa(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0007',
            saldo='200.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('40.00'),
            referencia_pago='OFFLINE-TEST-0007',
            metodo_pago=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_PAGADOR,
            usuario=self.user,
            empresa=self.empresa,
            notas='Pago parcial de prueba',
        )

        self.assertTrue(created)
        credito.refresh_from_db()
        primera_cuota = credito.tabla_amortizacion.order_by('numero_cuota').first()
        resumen = credit_services.obtener_resumen_pagos_credito(credito)
        self.assertFalse(primera_cuota.pagada)
        self.assertEqual(primera_cuota.monto_pagado, Decimal('40.00'))
        self.assertEqual(resumen['cuotas_pagadas'], 0)
        self.assertEqual(resumen['cuotas_restantes'], 2)
        self.assertEqual(credito.estado, Credito.EstadoCredito.ACTIVO)

    def test_resumen_operativo_se_envia_al_confirmar_carga_de_pagos(self):
        self._crear_credito_libranza(
            numero='CR-TEST-0008',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=1,
        )
        archivo = self._build_xlsx_file([
            ['numero_credito', 'monto_a_pagar', 'referencia_pago', 'fecha_pago'],
            ['CR-TEST-0008', '100.00', 'FERTOBRA-LOTE-08', '2026-03-31'],
        ])

        pagos_validos, errores, lote = credit_services.crear_borrador_pagos_masivos_archivo(
            archivo,
            self.empresa,
            usuario=self.user,
        )

        self.assertEqual(errores, [])
        self.assertEqual(len(pagos_validos), 1)
        mail.outbox = []

        pagos_exitosos, errores = credit_services.procesar_lote_pago_empresa(
            lote,
            usuario=self.user,
            notas='Carga confirmada desde prueba',
        )

        self.assertEqual(pagos_exitosos, 1)
        self.assertEqual(errores, [])
        self.assertEqual(len(mail.outbox), 1)
        from gestion_creditos.email_service import enviar_resumen_pago_masivo_pagador
        enviado = enviar_resumen_pago_masivo_pagador(
            lote=lote,
            pagos_aplicados=pagos_exitosos,
            monto_total=Decimal('100.00'),
            pagador_email=self.user.email,
            pagador_nombre='Tesorería Demo',
        )
        self.assertTrue(enviado)
        self.assertEqual(len(mail.outbox), 2)
        resumen = next(msg for msg in mail.outbox if 'Carga de pagos confirmada' in msg.subject)
        self.assertEqual(resumen.to, [self.user.email])

    def _build_xlsx_file(self, rows):
        from openpyxl import Workbook

        output = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            'pagos_fertobra.xlsx',
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

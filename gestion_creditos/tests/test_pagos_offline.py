from datetime import date
from decimal import Decimal
import io
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, DetalleContablePago, Empresa, HistorialPago, LotePagoEmpresa


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

    def test_pago_total_con_interes_cierra_credito_sin_segunda_transicion(self):
        credito = self._crear_credito_libranza(
            numero='CR-2026-00040-TEST',
            saldo='3357000.00',
            cuota='3420783.00',
            cuotas_pagadas=0,
            cuotas_totales=1,
        )
        credito.monto_aprobado = Decimal('3000000.00')
        credito.comision = Decimal('300000.00')
        credito.iva_comision = Decimal('57000.00')
        credito.capital_pendiente = Decimal('3000000.00')
        credito.total_a_pagar = Decimal('3420783.00')
        credito.tasa_interes = Decimal('1.90')
        credito.save(update_fields=[
            'monto_aprobado', 'comision', 'iva_comision', 'capital_pendiente',
            'total_a_pagar', 'tasa_interes',
        ])
        cuota = credito.tabla_amortizacion.get(numero_cuota=1)
        cuota.capital_a_pagar = Decimal('3357000.00')
        cuota.interes_a_pagar = Decimal('63783.00')
        cuota.saldo_capital_pendiente = Decimal('0.00')
        cuota.save(update_fields=[
            'capital_a_pagar', 'interes_a_pagar', 'saldo_capital_pendiente',
        ])

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('3420783.00'),
            referencia_pago='MANUAL-CR-2026-00040-TEST',
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
        )

        self.assertTrue(created)
        credito.refresh_from_db()
        cuota.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PAGADO)
        self.assertEqual(credito.saldo_pendiente, Decimal('0.00'))
        self.assertEqual(credito.capital_pendiente, Decimal('0.00'))
        self.assertIsNone(credito.fecha_proximo_pago)
        self.assertTrue(cuota.pagada)
        self.assertEqual(cuota.monto_pagado, Decimal('3420783.00'))
        self.assertIsNotNone(cuota.fecha_pago)
        self.assertEqual(HistorialPago.objects.filter(credito=credito).count(), 1)
        self.assertEqual(pago.estado, HistorialPago.EstadoPago.EXITOSO)

    def test_credito_pagado_bloquea_pago_manual_adicional(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-PAGADO',
            saldo='0.00',
            cuota='100.00',
            cuotas_pagadas=1,
            cuotas_totales=1,
        )
        Credito.objects.filter(pk=credito.pk).update(
            estado=Credito.EstadoCredito.PAGADO,
            saldo_pendiente=Decimal('0.00'),
            capital_pendiente=Decimal('0.00'),
        )
        credito.refresh_from_db()

        with self.assertRaisesMessage(
            ValidationError,
            'El crédito ya se encuentra pagado y no permite registrar pagos manuales adicionales.',
        ):
            credit_services.registrar_pago_credito(
                credito=credito,
                monto=Decimal('100.00'),
                referencia_pago='MANUAL-PAGADO-NO-PERMITIDO',
                metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
                origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
                usuario=self.user,
            )

        self.assertFalse(HistorialPago.objects.filter(credito=credito).exists())

    def test_pago_manual_con_diferencia_dentro_tolerancia_cierra_cuota(self):
        credito = self._crear_credito_libranza(
            numero='CR-TOL-001',
            saldo='1460132.42',
            cuota='730066.21',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )
        cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
        cuotas[0].fecha_vencimiento = date(2026, 7, 1)
        cuotas[1].fecha_vencimiento = date(2026, 8, 1)
        CuotaAmortizacion.objects.bulk_update(cuotas, ['fecha_vencimiento'])

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('730000.00'),
            referencia_pago='MANUAL-TOLERANCIA-001',
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
        )

        self.assertTrue(created)
        pago.refresh_from_db()
        credito.refresh_from_db()
        cuotas[0].refresh_from_db()
        cuotas[1].refresh_from_db()
        self.assertTrue(cuotas[0].pagada)
        self.assertEqual(cuotas[0].monto_pagado, Decimal('730000.00'))
        self.assertEqual(cuotas[0].fecha_pago, pago.fecha_aplicacion)
        self.assertFalse(cuotas[1].pagada)
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 8, 1))
        self.assertEqual(credito.saldo_pendiente, Decimal('730066.21'))
        self.assertEqual(pago.monto, Decimal('730000.00'))
        self.assertIn('tolerancia de redondeo', pago.notas)
        self.assertIn('$66.21 COP', pago.notas)

    def test_pago_manual_con_diferencia_mayor_a_tolerancia_sigue_parcial(self):
        credito = self._crear_credito_libranza(
            numero='CR-TOL-002',
            saldo='1460132.42',
            cuota='730066.21',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )
        primera_cuota = credito.tabla_amortizacion.get(numero_cuota=1)

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('729900.00'),
            referencia_pago='MANUAL-TOLERANCIA-002',
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
        )

        self.assertTrue(created)
        pago.refresh_from_db()
        credito.refresh_from_db()
        primera_cuota.refresh_from_db()
        self.assertFalse(primera_cuota.pagada)
        self.assertEqual(primera_cuota.monto_pagado, Decimal('729900.00'))
        self.assertEqual(credito.fecha_proximo_pago, primera_cuota.fecha_vencimiento)
        self.assertNotIn('tolerancia de redondeo', pago.notas or '')

    def test_pago_wompi_no_aplica_tolerancia_manual(self):
        credito = self._crear_credito_libranza(
            numero='CR-TOL-WOMPI',
            saldo='730066.21',
            cuota='730066.21',
            cuotas_pagadas=0,
            cuotas_totales=1,
        )
        cuota = credito.tabla_amortizacion.get(numero_cuota=1)

        credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('730000.00'),
            referencia_pago='WOMPI-SIN-TOLERANCIA-001',
            metodo_pago=HistorialPago.MetodoPago.WOMPI,
            origen_registro=HistorialPago.OrigenRegistro.PASARELA_WOMPI,
            usuario=self.user,
        )

        cuota.refresh_from_db()
        self.assertFalse(cuota.pagada)
        self.assertEqual(cuota.monto_pagado, Decimal('730000.00'))

    def test_reconciliar_pago_legacy_cierra_cuota_y_corrige_proxima_fecha(self):
        credito = self._crear_credito_libranza(
            numero='CR-LEG-REDONDEO',
            saldo='1460132.42',
            cuota='730066.21',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )
        cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
        cuotas[0].fecha_vencimiento = date(2026, 7, 1)
        cuotas[1].fecha_vencimiento = date(2026, 8, 1)
        CuotaAmortizacion.objects.bulk_update(cuotas, ['fecha_vencimiento'])

        pago, _ = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('730000.00'),
            referencia_pago='MANUAL-LEGACY-REDONDEO-001',
            metodo_pago=HistorialPago.MetodoPago.NO_DEFINIDO,
            origen_registro=HistorialPago.OrigenRegistro.LEGACY,
            usuario=self.user,
        )
        credito.fecha_proximo_pago = date(2026, 9, 1)
        credito.save(update_fields=['fecha_proximo_pago'])

        reconciliadas, resumen = credit_services.reconciliar_pago_manual_por_tolerancia(
            pago,
            usuario=self.user,
        )

        pago.refresh_from_db()
        credito.refresh_from_db()
        cuotas[0].refresh_from_db()
        self.assertEqual([cuota.pk for cuota in reconciliadas], [cuotas[0].pk])
        self.assertTrue(cuotas[0].pagada)
        self.assertEqual(cuotas[0].monto_pagado, Decimal('730000.00'))
        self.assertEqual(credito.fecha_proximo_pago, date(2026, 8, 1))
        self.assertEqual(resumen['fecha_proximo_pago'], date(2026, 8, 1))
        self.assertEqual(HistorialPago.objects.filter(credito=credito).count(), 1)
        self.assertIn('tolerancia de redondeo', pago.notas)

    def test_registrar_pago_crea_detalle_contable_por_cuota(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0001A',
            saldo='100.00',
            cuota='100.00',
            cuotas_pagadas=1,
            cuotas_totales=2,
        )

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('100.00'),
            referencia_pago='OFFLINE-TEST-0001A',
            metodo_pago=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
            empresa=self.empresa,
            notas='Pago final con detalle contable',
        )

        self.assertTrue(created)
        detalles = list(DetalleContablePago.objects.filter(pago=pago).order_by('secuencia_aplicacion'))
        self.assertEqual(len(detalles), 1)
        self.assertEqual(detalles[0].monto_total_aplicado, Decimal('100.00'))
        self.assertEqual(detalles[0].interes_aplicado, Decimal('20.00'))
        self.assertEqual(detalles[0].capital_aplicado, Decimal('80.00'))
        self.assertEqual(
            detalles[0].capital_aplicado,
            detalles[0].capital_principal_aplicado + detalles[0].comision_aplicada + detalles[0].iva_aplicado,
        )
        pago.refresh_from_db()
        self.assertEqual(pago.capital_abonado, Decimal('80.00'))
        self.assertEqual(pago.intereses_pagados, Decimal('20.00'))

    def test_registrar_pago_que_cubre_varias_cuotas_crea_varios_detalles(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0001B',
            saldo='200.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )

        pago, created = credit_services.registrar_pago_credito(
            credito=credito,
            monto=Decimal('200.00'),
            referencia_pago='OFFLINE-TEST-0001B',
            metodo_pago=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
            usuario=self.user,
            empresa=self.empresa,
            notas='Pago total con dos cuotas',
        )

        self.assertTrue(created)
        detalles = list(DetalleContablePago.objects.filter(pago=pago).order_by('secuencia_aplicacion'))
        self.assertEqual(len(detalles), 2)
        self.assertEqual(detalles[0].cuota.numero_cuota, 1)
        self.assertEqual(detalles[1].cuota.numero_cuota, 2)
        self.assertEqual(sum((item.monto_total_aplicado for item in detalles), Decimal('0.00')), Decimal('200.00'))

    def test_abono_normal_tambien_crea_detalle_contable(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0001C',
            saldo='200.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )

        pago, reestructuracion = credit_services.aplicar_abono_credito(
            credito=credito,
            monto_abono=Decimal('100.00'),
            tipo_abono='NORMAL',
            usuario=self.user,
            referencia_pago='ABONO-TEST-0001C',
        )

        self.assertIsNone(reestructuracion)
        detalles = list(DetalleContablePago.objects.filter(pago=pago).order_by('secuencia_aplicacion'))
        self.assertEqual(len(detalles), 1)
        self.assertEqual(detalles[0].monto_total_aplicado, Decimal('100.00'))
        self.assertEqual(detalles[0].capital_aplicado, Decimal('80.00'))
        self.assertEqual(detalles[0].interes_aplicado, Decimal('20.00'))

    def test_abono_a_capital_crea_detalle_contable_directo(self):
        credito = self._crear_credito_libranza(
            numero='CR-TEST-0001D',
            saldo='200.00',
            cuota='100.00',
            cuotas_pagadas=0,
            cuotas_totales=2,
        )

        pago, reestructuracion = credit_services.aplicar_abono_credito(
            credito=credito,
            monto_abono=Decimal('60.00'),
            tipo_abono='CAPITAL',
            usuario=self.user,
            referencia_pago='ABONO-CAPITAL-0001D',
        )

        self.assertIsNotNone(reestructuracion)
        detalles = list(DetalleContablePago.objects.filter(pago=pago))
        self.assertEqual(len(detalles), 1)
        self.assertIsNone(detalles[0].cuota)
        self.assertEqual(detalles[0].monto_total_aplicado, Decimal('60.00'))
        self.assertEqual(detalles[0].capital_aplicado, Decimal('60.00'))
        self.assertEqual(detalles[0].interes_aplicado, Decimal('0.00'))

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

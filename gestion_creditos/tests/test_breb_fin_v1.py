from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings

from gestion_creditos.models import Credito, CuotaAmortizacion, DetalleContablePago, HistorialPago, PagoBREB
from gestion_creditos.services.admin_dashboard_filters import parse_admin_dashboard_filters
from gestion_creditos.services.admin_excel_report import build_admin_excel_report
from gestion_creditos.services.breb_payments import aprobar_pago_breb, rechazar_pago_breb
from gestion_creditos.services.dashboard_metrics import _build_accounting_metrics, _obligaciones_por_empresa
from gestion_creditos.services.libranza_rules import calcular_primera_fecha_pago_libranza, sumar_meses_con_dia_ancla
from gestion_creditos.services.reporte_sme import construir_cierre
from .test_pagos_breb import PagoBREBBaseMixin
from .test_reporte_sme import CierreSMEFixture, fecha


class ConciliacionBREBTest(PagoBREBBaseMixin, TransactionTestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = override_settings(PRIVATE_DOCUMENTS_ROOT=tmp.name,
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
        settings.enable()
        self.addCleanup(settings.disable)
        self._crear_escenario_base()

    def aprobar(self, pago, **cambios):
        campos = dict(referencia_conciliada=pago.referencia_reportada, importe_conciliado=pago.valor_reportado)
        campos.update(cambios)
        return aprobar_pago_breb(pago_breb=pago, usuario=self.revisor, **campos)

    def test_importe_referencia_y_soporte_independientes(self):
        p = self._reportar()
        for campos in ({'importe_conciliado': None}, {'importe_conciliado': '0.001'},
                       {'importe_conciliado': '109999.99'}, {'referencia_conciliada': 'OTRA'}):
            with self.subTest(campos=campos), self.assertRaises(ValidationError):
                self.aprobar(p, **campos)
        PagoBREB.objects.filter(pk=p.pk).update(hash_comprobante='0' * 64)
        with self.assertRaises(ValidationError):
            self.aprobar(p)
        p.comprobante.storage.delete(p.comprobante.name)
        with self.assertRaises(ValidationError):
            self.aprobar(p)
        self.assertFalse(HistorialPago.objects.exists())
        self.assertFalse(DetalleContablePago.objects.exists())

    def test_referencia_omitida_se_registra_desde_el_banco(self):
        p = self._reportar(referencia_reportada='')
        with self.assertRaises(ValidationError):
            self.aprobar(p)
        self.aprobar(p, referencia_conciliada='  banco-sintetico  ')
        p.refresh_from_db()
        self.assertEqual(p.referencia_conciliada, 'BANCO-SINTETICO')
        self.assertEqual(p.importe_conciliado, p.valor_aprobado)

    def test_parcial_previo_sin_traza_no_se_completa(self):
        self.cuota.monto_pagado = 50000
        self.cuota.save(update_fields=['monto_pagado'])
        p = self._reportar([{'credito_id': self.credito.pk, 'valor_reportado': '60000'}])
        with self.assertRaisesRegex(ValidationError, 'asientos previos'):
            self.aprobar(p)
        self.assertFalse(HistorialPago.objects.exists())

    def test_rechazo_conserva_cuota_saldos_soporte_y_auditoria(self):
        p = self._reportar()
        antes = (self.credito.saldo_pendiente, self.credito.capital_pendiente,
                 self.cuota.pagada, self.cuota.monto_pagado, p.hash_comprobante, p.comprobante.name)
        rechazar_pago_breb(pago_breb=p, usuario=self.revisor, motivo='No acreditado')
        for entidad in (p, self.credito, self.cuota):
            entidad.refresh_from_db()
        self.assertEqual(antes, (self.credito.saldo_pendiente, self.credito.capital_pendiente,
            self.cuota.pagada, self.cuota.monto_pagado, p.hash_comprobante, p.comprobante.name))
        self.assertTrue(p.comprobante.storage.exists(p.comprobante.name))
        self.assertEqual((p.revisado_por_id, p.motivo_rechazo), (self.revisor.pk, 'No acreditado'))
        self.assertIsNotNone(p.revisado_en)
        self.assertFalse(HistorialPago.objects.exists())
        self.assertFalse(DetalleContablePago.objects.exists())

    def test_rollback_no_notifica_y_reintento_notifica_saldo_final_una_vez(self):
        p = self._reportar([{'credito_id': self.credito.pk, 'valor_reportado': '50000'}])
        with patch('gestion_creditos.email_service.enviar_confirmacion_pago') as aviso:
            with transaction.atomic():
                self.aprobar(p)
                transaction.set_rollback(True)
            aviso.assert_not_called()
            self.aprobar(p)
            self.aprobar(p)
            self.credito.refresh_from_db()
            aviso.assert_called_once_with(self.credito, 50000, self.credito.saldo_pendiente)

    def carrera(self, *acciones):
        self.assertEqual(connection.vendor, 'postgresql')
        barrera = Barrier(2, timeout=10)
        def operar(accion):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout TO '10s'")
                barrera.wait()
                accion()
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(operar, acciones), [True, False])

    def reportes_distintos(self, *, misma_ref=False, mismo_soporte=False, misma_cuota=False):
        otro = self.credito if misma_cuota else self._crear_credito('BREB-0002')[0]
        a = self._reportar()
        campos = dict(referencia_reportada=a.referencia_reportada if misma_ref else 'OTRA')
        if not mismo_soporte:
            campos['comprobante'] = self._pdf('otro.pdf', b'%PDF-1.4\notro asiento sintetico')
        b = self._reportar([{'credito_id': otro.pk, 'valor_reportado': '110000'}], **campos)
        self.carrera(lambda: self.aprobar(a), lambda: self.aprobar(b))
        self.assertEqual(PagoBREB.objects.filter(estado='APROBADO').count(), 1)
        self.assertEqual(HistorialPago.objects.count(), 1)
        self.assertEqual(DetalleContablePago.objects.count(), 1)

    def test_concurrencia_misma_referencia_distinto_credito(self):
        self.reportes_distintos(misma_ref=True)

    def test_concurrencia_mismo_soporte_distinto_credito(self):
        self.reportes_distintos(mismo_soporte=True)

    def test_concurrencia_dos_transferencias_para_una_cuota(self):
        self.reportes_distintos(misma_cuota=True)

    def test_aprobacion_concurrente_con_rechazo(self):
        a = self._reportar()
        self.carrera(lambda: self.aprobar(a), lambda: rechazar_pago_breb(
            pago_breb=a, usuario=self.revisor, motivo='Revision bancaria sintetica'))
        a.refresh_from_db()
        esperado = int(a.estado == 'APROBADO')
        self.assertIn(a.estado, ['APROBADO', 'RECHAZADO'])
        self.assertEqual(HistorialPago.objects.count(), esperado)
        self.assertEqual(DetalleContablePago.objects.count(), esperado)

    def test_salida_de_mora_no_reorigina_ni_cambia_plan(self):
        Credito.objects.filter(pk=self.credito.pk).update(estado='EN_MORA')
        CuotaAmortizacion.objects.create(credito=self.credito, numero_cuota=2,
            fecha_vencimiento=date(2026, 10, 1), capital_a_pagar=100000, interes_a_pagar=10000,
            valor_cuota=110000, saldo_capital_pendiente=0)
        with patch('gestion_creditos.credit_services.activar_credito') as activar, patch(
                'gestion_creditos.services.breb_payments.timezone.now', return_value=fecha(9)):
            p = self._reportar([{'credito_id': self.credito.pk, 'valor_reportado': '50000'}])
            self.aprobar(p)
            self.credito.refresh_from_db()
            self.assertEqual(self.credito.estado, 'EN_MORA')
            q = self._reportar([{'credito_id': self.credito.pk, 'valor_reportado': '60000'}],
                referencia_reportada='SEGUNDA', comprobante=self._pdf('p.pdf', b'%PDF-1.4\nsegundo'))
            self.aprobar(q)
            activar.assert_not_called()
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, 'ACTIVO')
        self.assertEqual(self.credito.tabla_amortizacion.count(), 2)
        self.assertEqual(self.credito.saldo_pendiente, 110000)

    def test_dos_obligaciones_parcial_completa_saldo_dashboard_empresa_y_sme(self):
        otro, segunda = self._crear_credito('BREB-0002')
        inicio = fecha(8)
        primera = calcular_primera_fecha_pago_libranza(fecha_aprobacion=inicio.date())
        for credito, cuota in [(self.credito, self.cuota), (otro, segunda)]:
            Credito.objects.filter(pk=credito.pk).update(fecha_desembolso=inicio)
            cuota.fecha_vencimiento = primera
            cuota.save(update_fields=['fecha_vencimiento'])
            CuotaAmortizacion.objects.create(credito=credito, numero_cuota=2,
                fecha_vencimiento=sumar_meses_con_dia_ancla(primera, 1, primera.day),
                capital_a_pagar=100000, interes_a_pagar=10000, valor_cuota=110000, saldo_capital_pendiente=0)
            CierreSMEFixture.estado(self, credito, 'PENDIENTE_TRANSFERENCIA', inicio-timedelta(hours=1))
            CierreSMEFixture.estado(self, credito, 'ACTIVO', inicio+timedelta(seconds=1), 'sintetico.pdf')
        p = self._reportar([{'credito_id': self.credito.pk, 'valor_reportado': '50000'},
                            {'credito_id': otro.pk, 'valor_reportado': '110000'}])
        with patch('gestion_creditos.services.breb_payments.timezone.now', return_value=fecha(9)):
            self.aprobar(p)
        for credito, cuota, saldo, capital, abonado, completa in (
            (self.credito, self.cuota, 170000, 160000, 50000, False), (otro, segunda, 110000, 100000, 110000, True)):
            credito.refresh_from_db()
            cuota.refresh_from_db()
            self.assertEqual((credito.saldo_pendiente, credito.capital_pendiente), (saldo, capital))
            self.assertEqual((cuota.monto_pagado, cuota.pagada), (abonado, completa))
        qs = Credito.objects.filter(pk__in=[self.credito.pk, otro.pk])
        for dia, esperado in [('2026-09-10', 160000), ('2026-08-29', 0)]:
            req = RequestFactory().get('/', {'fecha_desde': dia, 'fecha_hasta': dia})
            filtros = parse_admin_dashboard_filters(req)
            self.assertEqual(_build_accounting_metrics(qs, filtros)['total_recaudado'], esperado)
            _, resumen = _obligaciones_por_empresa(qs, qs, filtros, req)
            self.assertEqual(resumen['recaudo_periodo'], esperado)
            if esperado:
                libro, _ = build_admin_excel_report(req)
                self.assertEqual(sum(row[2] for row in libro['Recaudo contable'].values
                                     if not isinstance(row[2], str)), esperado)
                self.assertEqual(libro['Cartera por linea']['C2'].value, 280000)
                self.assertEqual(libro['Distribucion empresas']['C2'].value, 280000)
                libro.close()
        cierre = construir_cierre(date(2026, 9, 30), 'LIBRANZA', ids_revision=[self.credito.pk, otro.pk])
        self.assertEqual({r['credito_id_interno']: r['capital_pendiente'] for r in cierre['filas']},
                         {self.credito.pk: 160000, otro.pk: 100000})

from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import (
    CierreCarteraSME, Credito, CuotaAmortizacion, DetalleContablePago, Empresa, HistorialEstado, HistorialPago,
    ReestructuracionCredito,
)
from gestion_creditos.services.libranza_rules import calcular_primera_fecha_pago_libranza, sumar_meses_con_dia_ancla
from gestion_creditos.services.reporte_sme import ZONA, cierre_solo_lectura, construir_cierre, fin_exclusivo


CORTE = date(2026, 10, 31)


def fecha(mes, dia=10):
    return datetime(2026, mes, dia, 10, tzinfo=ZONA)


class CierreSMEFixture:
    def preparar_usuarios(self):
        User = get_user_model()
        self.operador = User.objects.create_user('operador-sme', password='prueba-sme-no-operativa', is_staff=True)
        self.operador.user_permissions.add(Permission.objects.get(codename='operar_cierre_cartera_sme'))
        self.otro_staff = User.objects.create_user('staff-sin-sme', is_staff=True)
        self.titular = User.objects.create_user('titular-sme')

    def estado(self, credito, estado, cuando, comprobante=''):
        registro = HistorialEstado.objects.create(credito=credito, estado_nuevo=estado, comprobante_pago=comprobante)
        HistorialEstado.objects.filter(pk=registro.pk).update(fecha=cuando)

    def credito(self, cuando, *, linea='LIBRANZA', con_traza=True):
        c = Credito.objects.create(
            usuario=self.titular, linea=linea, estado='ACTIVO', monto_solicitado=750, monto_aprobado=750,
            plazo_solicitado=2, plazo=2, total_a_pagar=900, valor_cuota=450,
            # Deliberately wrong today's fields: the report cannot use them.
            saldo_pendiente=1, capital_pendiente=1, fecha_desembolso=cuando,
        )
        primera = calcular_primera_fecha_pago_libranza(fecha_aprobacion=cuando.date())
        for i in range(2):
            CuotaAmortizacion.objects.create(credito=c, numero_cuota=i+1,
                fecha_vencimiento=sumar_meses_con_dia_ancla(primera, i, primera.day),
                capital_a_pagar=400, interes_a_pagar=50, valor_cuota=450, saldo_capital_pendiente=400*(1-i))
        if con_traza:
            self.estado(c, 'PENDIENTE_TRANSFERENCIA', cuando - timedelta(hours=1))
            self.estado(c, 'ACTIVO', cuando + timedelta(seconds=1), 'sintetico/no-leer.pdf')
        return c

    def pagar(self, credito, cuando, capital, interes, *, cuota_numero=1, con_detalle=True, estado='EXITOSO'):
        cuota = credito.tabla_amortizacion.get(numero_cuota=cuota_numero)
        monto = Decimal(capital) + Decimal(interes)
        p = HistorialPago.objects.create(credito=credito, fecha_aplicacion=cuando, monto=monto,
            estado=estado, referencia_pago=f'ENSAYO-{credito.pk}-{credito.historial_pagos.count()}')
        if con_detalle:
            DetalleContablePago.objects.create(pago=p, credito=credito, cuota=cuota, fecha_aplicacion=cuando,
                monto_total_aplicado=monto, capital_aplicado=capital, interes_aplicado=interes)
        cuota.monto_pagado = (cuota.monto_pagado or 0) + monto
        cuota.pagada = cuota.monto_pagado == cuota.valor_cuota
        cuota.fecha_pago = cuando if cuota.pagada else None
        cuota.save(update_fields=('monto_pagado', 'pagada', 'fecha_pago'))
        return p


class CierreCarteraSMETest(CierreSMEFixture, TransactionTestCase):
    def setUp(self):
        self.preparar_usuarios()

    def fila(self):
        filas = construir_cierre(CORTE)['filas']
        self.assertEqual(len(filas), 1)
        return filas[0]

    def test_al_dia_y_varias_obligaciones_del_mismo_titular(self):
        uno, dos = self.credito(fecha(10)), self.credito(fecha(10))
        resultado = construir_cierre(CORTE)
        self.assertEqual({r['credito_id_interno'] for r in resultado['filas']}, {uno.pk, dos.pk})
        self.assertEqual({r['numero_obligacion_interno'] for r in resultado['filas']},
                         {uno.numero_credito, dos.numero_credito})
        self.assertEqual(resultado['total']['al_dia'], 2)
        self.assertEqual(resultado['total']['capital_pendiente_conciliado'], 1600)
        self.assertEqual(resultado['total']['reportables'], 0)

    def test_parcial_varias_vencidas_y_limite_bogota(self):
        c = self.credito(fecha(8))
        self.pagar(c, fin_exclusivo(CORTE)-timedelta(microseconds=1), 30, 50)
        self.pagar(c, fin_exclusivo(CORTE), 100, 0)
        f = self.fila()
        self.assertEqual(f['categoria'], 'VENCIDA')
        self.assertEqual(f['capital_pendiente'], 770)
        self.assertEqual(f['saldo_vencido'], 820)
        self.assertEqual(f['cuotas_vencidas'], 2)
        self.assertEqual(f['dias_mora'], 60)
        self.assertEqual(f['ultima_aplicacion'], CORTE)
        self.assertTrue(f['novedad_posterior'])
        self.assertIn('COMUNICACION_PREVIA_Y_CUANTIA_NO_CONCILIADAS', f['impedimentos_reporte'])

    def test_credito_desembolsado_despues_y_solicitud_fuera_del_universo(self):
        self.credito(fecha(11))
        Credito.objects.create(usuario=self.titular, linea='LIBRANZA', monto_solicitado=750,
                              plazo_solicitado=2, estado='SOLICITUD')
        self.assertEqual(construir_cierre(CORTE)['total']['obligaciones_inventariadas'], 0)

    def test_pago_y_cierre_posterior_no_reemplazan_el_estado_historico(self):
        c = self.credito(fecha(8))
        for numero in (1, 2):
            self.pagar(c, fecha(11), 400, 50, cuota_numero=numero)
        self.estado(c, 'PAGADO', fecha(11, 11))
        Credito.objects.filter(pk=c.pk).update(estado='PAGADO')
        f = self.fila()
        self.assertEqual((f['categoria'], f['saldo_vencido'], f['capital_pendiente']), ('VENCIDA', 900, 800))
        self.assertTrue(f['novedad_posterior'])

    def test_cierre_conciliado_en_mes_es_pagado_no_cartera_al_dia(self):
        c = self.credito(fecha(8))
        for numero in (1, 2):
            self.pagar(c, fecha(10), 400, 50, cuota_numero=numero)
        self.estado(c, 'PAGADO', fecha(10, 11))
        f = self.fila()
        self.assertEqual((f['categoria'], f['saldo_pendiente']), ('PAGADA', 0))
        self.assertIn('TRATAMIENTO_DE_OBLIGACIONES_CERRADAS_NO_ACREDITADO', f['impedimentos_reporte'])

    def test_pago_pendiente_no_reduce_saldo(self):
        c = self.credito(fecha(8))
        HistorialPago.objects.create(credito=c, fecha_aplicacion=fecha(10), monto=300,
                                    estado='PENDIENTE', referencia_pago='PENDIENTE-SINTETICO')
        self.assertEqual(self.fila()['saldo_vencido'], 900)

    def test_reverso_no_conciliado_no_produce_saldo_inventado(self):
        c = self.credito(fecha(8))
        self.pagar(c, fecha(10), 30, 50, estado='FALLIDO')
        f = self.fila()
        self.assertEqual(f['incidencia_financiera'], 'REVERSO_O_CAMBIO_DE_ESTADO_SIN_TRAZA')
        self.assertNotIn('capital_pendiente', f)

    def test_pago_sin_detalle_es_excepcion(self):
        c = self.credito(fecha(8))
        self.pagar(c, fecha(10), 30, 50, con_detalle=False)
        self.assertEqual(self.fila()['incidencia_financiera'], 'PAGO_EFECTIVO_SIN_DETALLE_CONTABLE')

    def test_fecha_de_desembolso_sin_acta_no_acredita_la_obligacion(self):
        self.credito(fecha(8), con_traza=False)
        self.assertEqual(self.fila()['incidencia_financiera'], 'SIN_TRAZA_DE_DESEMBOLSO_AL_CORTE')

    def test_plan_mutado_no_reusa_la_version_actual(self):
        c = self.credito(fecha(8))
        c.tabla_amortizacion.filter(numero_cuota=1).update(fecha_vencimiento=fecha(11).date())
        self.assertEqual(self.fila()['incidencia_financiera'], 'CALENDARIO_HISTORICO_NO_ACREDITADO')

    def test_redondeo_legacy_se_concilia_y_mantiene_el_delta_separado(self):
        c = self.credito(fecha(10))
        Credito.objects.filter(pk=c.pk).update(total_a_pagar=Decimal('899.99'), comision=50, iva_comision=0)
        c.tabla_amortizacion.filter(numero_cuota=1).update(
            capital_a_pagar=Decimal('399.99'), interes_a_pagar=Decimal('50.01'),
            saldo_capital_pendiente=Decimal('400.01'),
        )
        f = self.fila()
        self.assertEqual(f['capital_financiado'], 800)
        self.assertEqual(f['capital_pendiente'], Decimal('799.99'))
        self.assertEqual(f['diferencia_plan_vs_total_aprobado'], Decimal('0.01'))
        self.assertEqual(f['diferencia_capital_plan_vs_financiado'], Decimal('-0.01'))

    def test_no_presume_que_un_delta_superior_al_redondeo_sea_legitimo(self):
        c = self.credito(fecha(10))
        Credito.objects.filter(pk=c.pk).update(total_a_pagar=Decimal('899.98'), comision=50, iva_comision=0)
        self.assertEqual(self.fila()['incidencia_financiera'], 'PLAN_NO_CONCILIA_CON_CONDICIONES')

    def test_componentes_redondeados_no_justifican_pago_superior_a_cuota(self):
        c = self.credito(fecha(10))
        Credito.objects.filter(pk=c.pk).update(comision=50, iva_comision=0)
        c.tabla_amortizacion.filter(numero_cuota=1).update(interes_a_pagar=Decimal('50.01'))
        self.pagar(c, fecha(10, 11), 400, Decimal('50.01'))
        self.assertEqual(self.fila()['incidencia_financiera'], 'IMPUTACION_EXCEDE_VALOR_CUOTA')

    def test_centavo_en_cuota_cerrada_no_inventa_capital_ni_interes(self):
        c = self.credito(fecha(8))
        Credito.objects.filter(pk=c.pk).update(comision=50, iva_comision=0)
        c.tabla_amortizacion.filter(numero_cuota=1).update(interes_a_pagar=Decimal('49.99'))
        pago = self.pagar(c, fecha(10), 400, Decimal('49.99'))
        HistorialPago.objects.filter(pk=pago.pk).update(monto=450)
        pago.detalles_contables.update(monto_total_aplicado=450)
        c.tabla_amortizacion.filter(numero_cuota=1).update(monto_pagado=450, pagada=True, fecha_pago=fecha(10))
        f = self.fila()
        self.assertEqual((f['cuotas_pagadas'], f['capital_pendiente'], f['saldo_vencido']), (1, 400, 450))
        self.assertEqual(f['cuotas_con_centavo_de_redondeo_contable'], 1)

    def test_centavo_inexplicado_no_pasa_como_redondeo(self):
        c = self.credito(fecha(8))
        Credito.objects.filter(pk=c.pk).update(comision=50, iva_comision=0)
        pago = self.pagar(c, fecha(10), 400, Decimal('49.99'))
        HistorialPago.objects.filter(pk=pago.pk).update(monto=450)
        pago.detalles_contables.update(monto_total_aplicado=450)
        self.assertEqual(self.fila()['incidencia_financiera'], 'DESGLOSE_O_REVERSO_NO_CONCILIADO')

    def test_remanente_posterior_no_reduce_saldo_octubre_y_filtro_focal(self):
        c = self.credito(fecha(8))
        self.credito(fecha(8))
        pago = self.pagar(c, fecha(11), 400, 50)
        HistorialPago.objects.filter(pk=pago.pk).update(monto=451)
        resultado = construir_cierre(CORTE, ids_revision=[c.pk])
        self.assertEqual(len(resultado['filas']), 1)
        f = resultado['filas'][0]
        self.assertEqual((f['saldo_vencido'], f['cuotas_pagadas']), (900, 0))
        self.assertEqual(f['pagos_posteriores_con_residuo_no_imputado'], 1)

    def test_remanente_anterior_no_se_omite(self):
        c = self.credito(fecha(8))
        pago = self.pagar(c, fecha(10), 400, 50)
        HistorialPago.objects.filter(pk=pago.pk).update(monto=451)
        self.assertEqual(self.fila()['incidencia_financiera'], 'MONTO_PAGO_NO_CONCILIA_CON_IMPUTACIONES')

    def test_acta_de_redondeo_no_convierte_residuo_en_dinero_recibido(self):
        c = self.credito(fecha(8))
        pago = self.pagar(c, fecha(10), 398, 50)
        cuota = c.tabla_amortizacion.get(numero_cuota=1)
        c.tabla_amortizacion.filter(pk=cuota.pk).update(pagada=True, fecha_pago=pago.fecha_aplicacion)
        acta = HistorialEstado.objects.create(
            credito=c, estado_nuevo='ACTIVO', usuario_modificacion=self.operador,
            clave_idempotencia=f'reconciliacion-redondeo:cuota:{cuota.pk}',
            motivo=json.dumps({'evento': 'RECONCILIACION_REDONDEO', 'resultado': 'RECONCILIADA',
                'credito_id': c.pk, 'pago_id': pago.pk, 'cuota_id': cuota.pk, 'numero_cuota': 1,
                'actor_id': self.operador.pk, 'monto_pagado_real': '448.00', 'diferencia': '2.00', 'tolerancia': '2.00'}),
        )
        HistorialEstado.objects.filter(pk=acta.pk).update(fecha=fecha(10, 11))
        self.assertEqual(self.fila()['incidencia_financiera'], 'CIERRE_OPERATIVO_AUDITADO_CON_RESIDUO_FINANCIERO')
        HistorialEstado.objects.filter(pk=acta.pk).update(fecha=fecha(11))
        self.assertEqual(self.fila()['saldo_vencido'], 452)

    def test_reestructuracion_no_se_promueve_a_plan_historico(self):
        c = self.credito(fecha(8))
        ReestructuracionCredito.objects.create(credito=c, monto_abonado=450,
            plan_anterior={}, plan_nuevo={}, saldo_pendiente_anterior=900, capital_pendiente_anterior=800,
            plazo_restante_anterior=2, saldo_pendiente_nuevo=450, capital_pendiente_nuevo=400,
            plazo_restante_nuevo=1, ahorro_intereses=0)
        self.assertEqual(self.fila()['incidencia_financiera'], 'PLAN_REESTRUCTURADO_REQUIERE_CONCILIACION_VERSIONADA')

    def test_productos_y_filtro_no_mezclan_creditos(self):
        self.credito(fecha(10))
        adelanto = self.credito(fecha(10), linea='ADELANTO_NOMINA')
        self.assertEqual(set(construir_cierre(CORTE)['por_producto']), {'LIBRANZA', 'ADELANTO_NOMINA'})
        f = construir_cierre(CORTE, 'ADELANTO_NOMINA')['filas']
        self.assertEqual([r['credito_id_interno'] for r in f], [adelanto.pk])

    def test_exclusion_de_pruebas_acreditadas_y_reclamos_sin_conciliar(self):
        ensayo = self.credito(fecha(10))
        real = self.credito(fecha(10))
        with override_settings(SME_CREDITOS_DE_PRUEBA_ACREDITADOS=[ensayo.pk]):
            resultado = construir_cierre(CORTE)
        self.assertEqual(resultado['excluidas_pruebas_identificadas'], 1)
        self.assertEqual(resultado['filas'][0]['credito_id_interno'], real.pk)
        self.assertIn('RECLAMOS_NO_CONCILIADOS', resultado['filas'][0]['impedimentos_reporte'])

    def test_base_no_permite_escribir_en_el_cierre(self):
        with self.assertRaises(DatabaseError), cierre_solo_lectura():
            with connection.cursor() as cur:
                cur.execute('UPDATE gestion_creditos_credito SET plazo = 0 WHERE 1 = 0')

    def test_vista_y_descargas_privadas_historial_y_hash(self):
        self.credito(fecha(10))
        url = reverse('gestion:cartera_sme')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.titular)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.otro_staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.operador)
        self.assertContains(self.client.get(url), 'Remisión oficial bloqueada')
        for _ in range(2):
            r = self.client.post(url, {'corte': CORTE.isoformat(), 'producto': 'LIBRANZA'})
            self.assertEqual(r.status_code, 302)
        self.assertEqual(CierreCarteraSME.objects.count(), 2)
        cierre = CierreCarteraSME.objects.first()
        self.assertContains(self.client.get(r.url), 'Capital pendiente conciliado (COP)')
        plantilla = reverse('gestion:cartera_sme_descarga', args=[cierre.pk, 'oficial'])
        self.assertEqual(self.client.get(plantilla).status_code, 404)
        descarga = reverse('gestion:cartera_sme_descarga', args=[cierre.pk, 'conciliacion'])
        d = self.client.get(descarga)
        self.assertEqual(d.status_code, 200)
        self.assertIn('no-store', d.headers['Cache-Control'])
        self.assertEqual(sha256(d.content).hexdigest(), cierre.sha256)
        self.assertNotContains(d, 'titular-sme')
        self.client.force_login(self.otro_staff)
        self.assertEqual(self.client.get(descarga).status_code, 403)

    def test_snapshot_de_descarga_manipulado_se_rechaza(self):
        self.credito(fecha(10))
        self.client.force_login(self.operador)
        self.client.post(reverse('gestion:cartera_sme'), {'corte': CORTE.isoformat(), 'producto': ''})
        cierre = CierreCarteraSME.objects.get()
        CierreCarteraSME.objects.filter(pk=cierre.pk).update(contenido={})
        url = reverse('gestion:cartera_sme_descarga', args=[cierre.pk, 'excepciones'])
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_perfil_pagador_no_puede_usar_permiso_de_staff(self):
        from usuarios.models import PerfilPagador

        PerfilPagador.objects.create(usuario=self.operador, empresa=Empresa.objects.create(nombre='Empresa de ensayo'))
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse('gestion:cartera_sme')).status_code, 403)

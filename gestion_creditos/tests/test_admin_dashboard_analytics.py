import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.models import (
    AsesorComercial,
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    DetalleContablePago,
    Empresa,
    HistorialPago,
)
from gestion_creditos.services.admin_dashboard_filters import (
    parse_admin_dashboard_filters,
)
from gestion_creditos.services.dashboard_metrics import (
    get_admin_dashboard_context,
    get_admin_obligaciones_context,
)


User = get_user_model()


class AdminDashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.staff = User.objects.create_user(
            username='dashboard-analytics-staff',
            email='dashboard-analytics-staff@aprobado.test',
            password='123456',
            is_staff=True,
        )
        self.cliente = User.objects.create_user(
            username='dashboard-analytics-client',
            email='dashboard-analytics-client@aprobado.test',
            password='123456',
            first_name='Cliente',
            last_name='Analitica',
        )
        self.empresa_a = Empresa.objects.create(
            nombre='Empresa Analitica A',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.empresa_b = Empresa.objects.create(
            nombre='Empresa Analitica B',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self._secuencia = 0

    def _crear_credito(
        self,
        *,
        empresa=None,
        estado=Credito.EstadoCredito.ACTIVO,
        linea=Credito.LineaCredito.LIBRANZA,
        saldo=Decimal('1000.00'),
        capital=Decimal('800.00'),
        fecha_solicitud=None,
        fecha_desembolso=None,
    ):
        self._secuencia += 1
        credito = Credito.objects.create(
            usuario=self.cliente,
            numero_credito=f'CR-DA-{self._secuencia:04d}',
            linea=linea,
            estado=estado,
            monto_solicitado=Decimal('1500.00'),
            monto_aprobado=Decimal('1500.00'),
            plazo_solicitado=4,
            plazo=4,
            valor_cuota=Decimal('400.00'),
            saldo_pendiente=saldo,
            capital_pendiente=capital,
            total_a_pagar=Decimal('1600.00'),
            fecha_desembolso=fecha_desembolso,
        )
        if fecha_solicitud is not None:
            Credito.objects.filter(pk=credito.pk).update(fecha_solicitud=fecha_solicitud)
            credito.fecha_solicitud = fecha_solicitud
        if linea == Credito.LineaCredito.LIBRANZA:
            CreditoLibranza.objects.create(
                credito=credito,
                empresa=empresa or self.empresa_a,
                direccion='Calle 10',
                telefono='3001234567',
                correo_electronico='cliente@empresa.test',
                cedula='1000000000',
                nombres='Cliente',
                apellidos='Analitica',
            )
        return credito

    def _crear_cuota(
        self,
        credito,
        *,
        numero,
        vencimiento,
        valor=Decimal('400.00'),
        pagado=Decimal('0.00'),
        pagada=False,
    ):
        return CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=numero,
            fecha_vencimiento=vencimiento,
            capital_a_pagar=Decimal('350.00'),
            interes_a_pagar=Decimal('50.00'),
            valor_cuota=valor,
            saldo_capital_pendiente=Decimal('1000.00'),
            pagada=pagada,
            monto_pagado=pagado,
            fecha_pago=timezone.now() if pagada else None,
        )

    def _crear_detalle_contable(self, credito, cuota, fecha_aplicacion):
        pago = HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('400.00'),
            referencia_pago=f'REF-DA-{credito.pk}',
            estado=HistorialPago.EstadoPago.EXITOSO,
            metodo_pago=HistorialPago.MetodoPago.OFFLINE_MANUAL,
            origen_registro=HistorialPago.OrigenRegistro.REGISTRO_MANUAL_ADMIN,
        )
        return DetalleContablePago.objects.create(
            pago=pago,
            credito=credito,
            cuota=cuota,
            fecha_aplicacion=fecha_aplicacion,
            monto_total_aplicado=Decimal('400.00'),
            capital_aplicado=Decimal('350.00'),
            interes_aplicado=Decimal('50.00'),
            capital_principal_aplicado=Decimal('300.00'),
            comision_aplicada=Decimal('40.00'),
            iva_aplicado=Decimal('10.00'),
        )

    def _context_empresas(self, **params):
        return get_admin_obligaciones_context(self.factory.get(
            '/gestion/obligaciones-pendientes/', {'vista': 'empresa', **params},
        ))

    def test_por_empresa_agrega_todas_las_impagas_sin_duplicar_creditos(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        for numero, dias, pagado in ((1, -32, '100'), (2, -15, '0'), (3, 0, '0'), (4, 15, '0'), (5, 16, '0')):
            self._crear_cuota(credito, numero=numero, vencimiento=hoy + timedelta(days=dias), pagado=Decimal(pagado))
        self._crear_cuota(credito, numero=6, vencimiento=hoy - timedelta(days=60), pagada=True)
        segundo = self._crear_credito()
        self._crear_cuota(segundo, numero=1, vencimiento=hoy + timedelta(days=5), pagado=None)
        pagado = self._crear_credito(estado=Credito.EstadoCredito.PAGADO)
        self._crear_cuota(pagado, numero=1, vencimiento=hoy - timedelta(days=90))
        proximas = self._crear_credito(empresa=self.empresa_b)
        self._crear_cuota(proximas, numero=1, vencimiento=hoy + timedelta(days=1))

        context = self._context_empresas()

        empresas = context['empresas_obligaciones']
        self.assertEqual(len(empresas), 2)
        a, b = empresas
        self.assertEqual(a['empresa_id_reporte'], self.empresa_a.pk)
        self.assertEqual((a['creditos'], a['vencidas'], a['vencen_hoy'], a['proximas']), (3, 2, 1, 2))
        self.assertEqual(a['total_pendiente'], Decimal('2300'))
        self.assertEqual(a['total_vencido'], Decimal('700'))
        self.assertEqual(a['mora_maxima'], 32)
        self.assertEqual((b['creditos'], b['vencidas'], b['proximas']), (1, 0, 1))
        self.assertEqual(b['total_pendiente'], Decimal('400'))
        self.assertEqual(b['mora_maxima'], 0)
        self.assertEqual(context['resumen_empresas'], {
            'empresas_con_mora': 1, 'cartera_vencida': Decimal('700'), 'mora_maxima': 32,
        })
        detalle = get_admin_obligaciones_context(self.factory.get('/gestion/obligaciones-pendientes/'))
        self.assertEqual(context['obligaciones_distribucion'], detalle['obligaciones_distribucion'])
        self.assertEqual(len(detalle['obligaciones']), 3)

    def test_por_empresa_ordena_por_exposicion_y_luego_mora(self):
        hoy = timezone.localdate()
        tercera = Empresa.objects.create(nombre='Empresa menor exposicion')
        for empresa, dias, valor in ((self.empresa_a, 10, '800'), (self.empresa_b, 30, '800'), (tercera, 90, '400')):
            credito = self._crear_credito(empresa=empresa)
            self._crear_cuota(credito, numero=1, vencimiento=hoy - timedelta(days=dias), valor=Decimal(valor))

        context = self._context_empresas()

        self.assertEqual([row['empresa_id_reporte'] for row in context['empresas_obligaciones']],
                         [self.empresa_b.pk, self.empresa_a.pk, tercera.pk])
        self.assertEqual(context['resumen_empresas']['cartera_vencida'], Decimal('2000'))
        self.assertEqual(context['resumen_empresas']['mora_maxima'], 90)

    def test_por_empresa_accion_reutiliza_filtros_y_detalle_existente(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        self._crear_cuota(credito, numero=1, vencimiento=hoy - timedelta(days=2))
        self._crear_cuota(credito, numero=2, vencimiento=hoy + timedelta(days=3))
        otro = self._crear_credito(empresa=self.empresa_b)
        self._crear_cuota(otro, numero=1, vencimiento=hoy - timedelta(days=2))
        self.client.force_login(self.staff)
        url = reverse('gestion:obligaciones_pendientes')
        params = {'vista': 'empresa', 'obligacion': 'VENCIDA', 'fecha_desde': (hoy - timedelta(days=5)).isoformat(),
                  'fecha_hasta': hoy.isoformat(), 'linea': 'LIBRANZA', 'credito': credito.numero_credito, 'page': '1'}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ver obligaciones')
        self.assertContains(response, 'name="vista" value="empresa"')
        empresa = response.context['empresas_obligaciones'][0]
        self.assertEqual(empresa['vencidas'], 1)
        self.assertEqual(empresa['proximas'], 0)
        self.assertEqual(empresa['total_pendiente'], Decimal('400'))
        query = parse_qs(empresa['detalle_query'])
        self.assertEqual(query['empresa'], [str(self.empresa_a.pk)])
        self.assertEqual(query['vista'], ['detalle'])
        self.assertEqual(query['obligacion'], ['VENCIDA'])
        self.assertEqual(query['fecha_desde'], [params['fecha_desde']])
        self.assertNotIn('page', query)
        detalle = self.client.get(f"{url}?{empresa['detalle_query']}")
        self.assertEqual(detalle.status_code, 200)
        self.assertEqual([row['credito_id'] for row in detalle.context['obligaciones']], [credito.pk])
        self.assertContains(detalle, reverse('gestion:credito_detalle', args=[credito.pk]))
        for link in response.context['vista_links']:
            self.assertNotIn('page', parse_qs(link['query']))

    def test_por_empresa_pagina_empresas_e_indicadores_no_solo_pagina(self):
        hoy = timezone.localdate()
        for numero in range(21):
            empresa = Empresa.objects.create(nombre=f'Empresa agrupada {numero:02d}')
            credito = self._crear_credito(empresa=empresa)
            self._crear_cuota(credito, numero=1, vencimiento=hoy - timedelta(days=1))
        context = self._context_empresas(page='2')
        self.assertEqual(context['pagina_obligaciones'].paginator.count, 21)
        self.assertEqual(len(context['empresas_obligaciones']), 1)
        self.assertEqual(context['resumen_empresas']['empresas_con_mora'], 21)
        self.assertEqual(context['resumen_empresas']['cartera_vencida'], Decimal('8400'))
        self.assertEqual(context['resumen_gerencial']['obligacion_periodo'], Decimal('8400'))
        self.assertEqual(context['resumen_gerencial']['total_pendiente'], Decimal('8400'))
        self.assertEqual(context['resumen_gerencial']['creditos_con_mora'], 21)
        self.assertIn('vista=empresa', context['obligaciones_querystring'])

    def test_por_empresa_sin_resultados_y_permisos_staff(self):
        url = reverse('gestion:obligaciones_pendientes')
        self.assertEqual(self.client.get(url, {'vista': 'empresa'}).status_code, 302)
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get(url, {'vista': 'empresa'}).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(url, {'vista': 'empresa'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No hay empresas con obligaciones')
        self.assertEqual(response.context['resumen_empresas']['mora_maxima'], 0)
        self.assertEqual(self.client.get(url, {'vista': 'invalida'}).context['vista_obligaciones'], 'detalle')

    def test_por_empresa_no_introduce_consultas_por_fila(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        cuota = self._crear_cuota(credito, numero=1, vencimiento=hoy)
        self._crear_detalle_contable(credito, cuota, timezone.now())
        with CaptureQueriesContext(connection) as inicial:
            self._context_empresas()
        for numero in range(5):
            empresa = Empresa.objects.create(nombre=f'Empresa consultas {numero}')
            credito = self._crear_credito(empresa=empresa)
            for cuota in range(1, 4):
                self._crear_cuota(credito, numero=cuota, vencimiento=hoy + timedelta(days=cuota))
            self._crear_detalle_contable(credito, None, timezone.now())
        with CaptureQueriesContext(connection) as final:
            context = self._context_empresas()
        self.assertEqual(len(context['empresas_obligaciones']), 6)
        self.assertEqual(len(final), len(inicial))

    def _aplicacion_gerencial(self, credito, cuota, fecha, monto):
        monto = Decimal(monto)
        pago = HistorialPago.objects.create(
            credito=credito, monto=monto, estado=HistorialPago.EstadoPago.EXITOSO,
            referencia_pago=f'REF-GER-{HistorialPago.objects.count() + 1}',
            fecha_aplicacion=timezone.make_aware(datetime(2026, 7, 1)),
        )
        return DetalleContablePago.objects.create(
            credito=credito, cuota=cuota, pago=pago, fecha_aplicacion=fecha,
            monto_total_aplicado=monto, capital_aplicado=monto, capital_principal_aplicado=monto,
        )

    @patch('django.utils.timezone.localdate', return_value=date(2026, 10, 2))
    def test_gerencial_obligacion_recaudo_y_residuo_son_universos_independientes(self, _hoy):
        credito = self._crear_credito()
        anterior = self._crear_cuota(credito, numero=1, vencimiento=date(2026, 8, 1))
        cuota = self._crear_cuota(credito, numero=2, vencimiento=date(2026, 9, 1),
                                  valor=Decimal('1000'), pagado=Decimal('400'))
        parcial = self._crear_cuota(credito, numero=3, vencimiento=date(2026, 9, 2),
                                    valor=Decimal('500'), pagado=Decimal('100'))
        satisfecha = self._crear_cuota(credito, numero=4, vencimiento=date(2026, 9, 30),
                                       valor=Decimal('300'), pagado=Decimal('300'), pagada=True)
        cerrado = self._crear_credito(empresa=self.empresa_b, estado=Credito.EstadoCredito.PAGADO)
        ultima = self._crear_cuota(cerrado, numero=1, vencimiento=date(2026, 9, 10),
                                  valor=Decimal('700'), pagado=Decimal('700'), pagada=True)
        solo_recaudo = self._crear_credito(empresa=Empresa.objects.create(nombre='Empresa solo recaudo'))
        fecha = timezone.make_aware(datetime(2026, 9, 15, 10))
        self._aplicacion_gerencial(credito, cuota, timezone.make_aware(datetime(2026, 7, 1)), '400')
        self._aplicacion_gerencial(credito, parcial, fecha, '100')
        self._aplicacion_gerencial(credito, satisfecha, fecha, '300')
        self._aplicacion_gerencial(cerrado, ultima, fecha, '700')
        self._aplicacion_gerencial(solo_recaudo, None, fecha, '50')

        params = {'fecha_desde': '2026-09-01', 'fecha_hasta': '2026-09-30'}
        context = self._context_empresas(**params)
        resumen = context['resumen_gerencial']
        self.assertEqual(resumen, {
            'obligacion_periodo': Decimal('2500'), 'recaudo_periodo': Decimal('1150'),
            'total_pendiente': Decimal('1000'), 'total_vencido': Decimal('1000'),
            'vencido_mas_30': Decimal('600'), 'empresas_con_mora': 1,
            'creditos_con_mora': 1, 'mora_maxima': 31,
        })
        self.assertNotEqual(resumen['total_pendiente'], resumen['obligacion_periodo'] - resumen['recaudo_periodo'])
        rows = context['empresas_obligaciones']
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]['creditos'], rows[0]['clientes']), (1, 1))
        self.assertEqual(rows[0]['obligacion_periodo'], Decimal('1800'))
        self.assertEqual(rows[0]['recaudo_periodo'], Decimal('400'))
        for key in ('obligacion_periodo', 'recaudo_periodo', 'total_pendiente', 'total_vencido', 'vencido_mas_30', 'creditos_con_mora'):
            self.assertEqual(resumen[key], sum(row[key] for row in rows))
        detalle = get_admin_obligaciones_context(self.factory.get('/gestion/obligaciones-pendientes/', params))
        self.assertEqual(detalle['resumen_gerencial'], resumen)
        # El detalle conserva la primera pendiente, aunque sea anterior al rango.
        self.assertEqual(detalle['obligaciones'], [])
        anterior.refresh_from_db()
        self.assertFalse(anterior.pagada)

    def test_gerencial_no_multiplica_recaudo_por_cuotas_y_conserva_abonos_sin_cuota(self):
        credito = self._crear_credito()
        for numero in range(1, 4):
            cuota = self._crear_cuota(credito, numero=numero, vencimiento=date(2026, 9, numero))
        fecha = timezone.make_aware(datetime(2026, 9, 15))
        self._aplicacion_gerencial(credito, cuota, fecha, '100')
        self._aplicacion_gerencial(credito, cuota, fecha, '200')
        self._aplicacion_gerencial(credito, None, fecha, '50')
        context = self._context_empresas(fecha_desde='2026-09-01', fecha_hasta='2026-09-30')
        self.assertEqual(context['resumen_gerencial']['obligacion_periodo'], Decimal('1200'))
        self.assertEqual(context['resumen_gerencial']['recaudo_periodo'], Decimal('350'))
        self.assertEqual(context['empresas_obligaciones'][0]['recaudo_periodo'], Decimal('350'))
        self.assertEqual(context['empresas_obligaciones'][0]['creditos'], 1)

    @patch('django.utils.timezone.localdate', return_value=date(2026, 10, 2))
    def test_gerencial_filtros_empresa_fecha_y_estado_no_excluyen_recaudo_pagado(self, _hoy):
        activo = self._crear_credito()
        cerrado = self._crear_credito(estado=Credito.EstadoCredito.PAGADO)
        otro = self._crear_credito(empresa=self.empresa_b)
        for credito in (activo, cerrado, otro):
            cuota = self._crear_cuota(credito, numero=1, vencimiento=date(2026, 9, 1))
            self._aplicacion_gerencial(credito, cuota, timezone.make_aware(datetime(2026, 9, 15)), '100')
            self._aplicacion_gerencial(credito, cuota, timezone.make_aware(datetime(2026, 8, 15)), '200')
        context = self._context_empresas(
            empresa=str(self.empresa_a.pk), fecha_desde='2026-09-01', fecha_hasta='2026-09-30',
            estado='ACTIVO', obligacion='VENCE_HOY',
        )
        self.assertEqual(context['resumen_gerencial']['recaudo_periodo'], Decimal('200'))
        self.assertEqual(context['resumen_gerencial']['obligacion_periodo'], Decimal('0'))
        self.assertEqual(len(context['empresas_obligaciones']), 1)
        busqueda = self._context_empresas(credito=cerrado.numero_credito, fecha_desde='2026-09-01', fecha_hasta='2026-09-30')
        self.assertEqual(busqueda['resumen_gerencial']['recaudo_periodo'], Decimal('100'))
        self.assertEqual(busqueda['resumen_gerencial']['obligacion_periodo'], Decimal('400'))

    def test_gerencial_mora_mayor_30_excluye_satisfechas_y_residuos_no_positivos(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        for numero, dias, pagado, pagada in (
            (1, 31, '100', False), (2, 30, '0', False),
            (3, 90, '400', True), (4, 100, '400', False), (5, 101, '500', False),
        ):
            self._crear_cuota(credito, numero=numero, vencimiento=hoy - timedelta(days=dias),
                              pagado=Decimal(pagado), pagada=pagada)
        resumen = self._context_empresas()['resumen_gerencial']
        self.assertEqual(resumen['total_pendiente'], Decimal('700'))
        self.assertEqual(resumen['total_vencido'], Decimal('700'))
        self.assertEqual(resumen['vencido_mas_30'], Decimal('300'))
        self.assertEqual(resumen['mora_maxima'], 31)
        self.assertEqual(resumen['creditos_con_mora'], 1)

    def test_gerencial_recaudo_limites_bogota_y_no_infiere_historicos(self):
        with timezone.override(ZoneInfo('America/Bogota')):
            credito = self._crear_credito()
            cuota = self._crear_cuota(credito, numero=1, vencimiento=date(2026, 9, 1), pagada=True, pagado=Decimal('400'))
            params = {'fecha_desde': '2026-09-01', 'fecha_hasta': '2026-09-30'}
            self.assertEqual(self._context_empresas(**params)['resumen_gerencial']['recaudo_periodo'], 0)
            inicio = datetime(2026, 9, 1, tzinfo=ZoneInfo('America/Bogota'))
            fin = datetime(2026, 10, 1, tzinfo=ZoneInfo('America/Bogota'))
            for fecha in (inicio - timedelta(seconds=1), inicio, fin - timedelta(seconds=1), fin):
                self._aplicacion_gerencial(credito, cuota, fecha.astimezone(ZoneInfo('UTC')), '10')
            fallido = self._aplicacion_gerencial(credito, cuota, inicio, '100')
            fallido.pago.estado = HistorialPago.EstadoPago.FALLIDO
            fallido.pago.save(update_fields=['estado'])
            resumen = self._context_empresas(**params)['resumen_gerencial']
            self.assertEqual(resumen['recaudo_periodo'], Decimal('20'))
            self.assertEqual(resumen['total_pendiente'], Decimal('0'))

    def test_gerencial_sin_empresa_no_pierde_importes_y_vista_renderiza_cards(self):
        credito = self._crear_credito(linea=Credito.LineaCredito.EMPRENDIMIENTO)
        cuota = self._crear_cuota(credito, numero=1, vencimiento=timezone.localdate() - timedelta(days=2))
        self._crear_detalle_contable(credito, cuota, timezone.now())
        self.client.force_login(self.staff)
        for vista in ('empresa', 'detalle'):
            response = self.client.get(reverse('gestion:obligaciones_pendientes'), {'vista': vista})
            self.assertEqual(response.status_code, 200)
            resumen = response.context['resumen_gerencial']
            self.assertEqual(resumen['recaudo_periodo'], Decimal('400'))
            self.assertEqual(resumen['total_vencido'], Decimal('400'))
            self.assertEqual(resumen['empresas_con_mora'], 0)
            self.assertEqual(resumen['creditos_con_mora'], 1)
            for label in ('Obligación del período', 'Recaudo del período', 'Pendiente actual', 'Vencido actual',
                          'Empresas con mora', 'Créditos con mora', 'Cartera vencida &gt;30 días', 'Mayor mora'):
                self.assertContains(response, label)
            if vista == 'empresa':
                self.assertIsNone(response.context['empresas_obligaciones'][0]['detalle_query'])

    def test_kpi_capital_excluye_null_y_respeta_empresa_estado_y_linea(self):
        self._crear_credito(empresa=self.empresa_a, capital=Decimal('600.00'))
        self._crear_credito(empresa=self.empresa_a, capital=None)
        self._crear_credito(empresa=self.empresa_b, capital=Decimal('900.00'))
        self._crear_credito(
            empresa=self.empresa_a,
            estado=Credito.EstadoCredito.PAGADO,
            capital=Decimal('500.00'),
        )
        request = self.factory.get('/gestion/', {
            'empresa': str(self.empresa_a.pk),
            'estado': Credito.EstadoCredito.ACTIVO,
            'linea': Credito.LineaCredito.LIBRANZA,
        })

        context = get_admin_dashboard_context(self.staff, request)

        self.assertEqual(context['saldo_capital_pendiente'], Decimal('600.00'))
        self.assertEqual(context['capital_pendiente_incompleto'], 1)
        self.assertEqual(context['total_creditos'], 2)

    def test_filtro_asesor_limita_el_universo_a_sus_empresas(self):
        asesor = AsesorComercial.objects.create(
            nombre='Asesor Analitica',
            cedula='900000001',
            activo=True,
        )
        self.empresa_a.asesor_comercial = asesor
        self.empresa_a.save(update_fields=['asesor_comercial'])
        credito_asesor = self._crear_credito(
            empresa=self.empresa_a,
            saldo=Decimal('700.00'),
            capital=Decimal('500.00'),
        )
        self._crear_credito(
            empresa=self.empresa_b,
            saldo=Decimal('900.00'),
            capital=Decimal('800.00'),
        )
        request = self.factory.get('/gestion/', {'asesor': str(asesor.pk)})

        context = get_admin_dashboard_context(self.staff, request)

        self.assertEqual(context['total_creditos'], 1)
        self.assertEqual(context['saldo_cartera_total'], Decimal('700.00'))
        self.assertEqual(
            json.loads(context['cartera_empresa_labels']),
            [self.empresa_a.nombre],
        )
        self.assertEqual(context['selected_asesor'], asesor)
        self.assertEqual(credito_asesor.detalle_libranza.empresa_id, self.empresa_a.pk)

    def test_filtros_rechazan_fechas_invalidas_y_rango_invertido(self):
        invalido = parse_admin_dashboard_filters(
            self.factory.get('/gestion/', {'fecha_desde': 'no-es-fecha'})
        )
        invertido = parse_admin_dashboard_filters(self.factory.get('/gestion/', {
            'fecha_desde': '2026-09-02',
            'fecha_hasta': '2026-09-01',
        }))

        self.assertIsNone(invalido.fecha_desde)
        self.assertIsNone(invalido.fecha_hasta)
        self.assertTrue(invalido.errores)
        self.assertIsNone(invertido.fecha_desde)
        self.assertIsNone(invertido.fecha_hasta)
        self.assertTrue(invertido.errores)

    def test_rango_usa_fecha_propia_por_metrica_y_saldo_sigue_siendo_corte_actual(self):
        tz = timezone.get_current_timezone()
        credito = self._crear_credito(
            fecha_solicitud=timezone.make_aware(datetime(2026, 1, 10, 9), tz),
            fecha_desembolso=timezone.make_aware(datetime(2026, 2, 10, 9), tz),
            saldo=Decimal('1200.00'),
            capital=Decimal('900.00'),
        )
        cuota = self._crear_cuota(
            credito,
            numero=1,
            vencimiento=datetime(2026, 3, 15).date(),
        )
        self._crear_detalle_contable(
            credito,
            cuota,
            timezone.make_aware(datetime(2026, 3, 10, 9), tz),
        )
        request = self.factory.get('/gestion/', {
            'fecha_desde': '2026-03-01',
            'fecha_hasta': '2026-03-31',
        })

        context = get_admin_dashboard_context(self.staff, request)

        self.assertEqual(context['saldo_cartera_total'], Decimal('1200.00'))
        self.assertEqual(context['monto_total_en_mora'], Decimal('400.00'))
        self.assertEqual(json.loads(context['solicitud_cantidad']), [])
        self.assertEqual(json.loads(context['desembolso_cantidad']), [])
        self.assertEqual(json.loads(context['recaudo_capital']), [300.0])

    def test_obligaciones_usan_primera_no_pagada_pago_parcial_y_estados_operativos(self):
        hoy = timezone.localdate()
        vencida = self._crear_credito()
        self._crear_cuota(
            vencida,
            numero=1,
            vencimiento=hoy - timedelta(days=30),
            pagada=True,
            pagado=Decimal('400.00'),
        )
        self._crear_cuota(
            vencida,
            numero=2,
            vencimiento=hoy - timedelta(days=1),
            pagado=Decimal('125.00'),
        )
        vence_hoy = self._crear_credito()
        self._crear_cuota(vence_hoy, numero=1, vencimiento=hoy)
        vence_pronto = self._crear_credito()
        self._crear_cuota(
            vence_pronto,
            numero=1,
            vencimiento=hoy + timedelta(days=10),
        )
        al_dia = self._crear_credito()
        self._crear_cuota(al_dia, numero=1, vencimiento=hoy + timedelta(days=20))
        pagado = self._crear_credito(estado=Credito.EstadoCredito.PAGADO)
        self._crear_cuota(pagado, numero=1, vencimiento=hoy - timedelta(days=2))

        context = get_admin_obligaciones_context(self.factory.get('/gestion/obligaciones-pendientes/'))

        por_credito = {
            item['credito_id']: item for item in context['obligaciones']
        }
        self.assertEqual(set(por_credito), {vencida.pk, vence_hoy.pk, vence_pronto.pk, al_dia.pk})
        self.assertEqual(por_credito[vencida.pk]['numero_cuota'], 2)
        self.assertEqual(por_credito[vencida.pk]['valor_pendiente'], Decimal('275.00'))
        self.assertEqual(por_credito[vencida.pk]['estado_codigo'], 'VENCIDA')
        self.assertEqual(por_credito[vence_hoy.pk]['estado_codigo'], 'VENCE_HOY')
        self.assertEqual(por_credito[vence_pronto.pk]['estado_codigo'], 'VENCE_PRONTO')
        self.assertEqual(por_credito[al_dia.pk]['estado_codigo'], 'AL_DIA')
        self.assertEqual(context['obligaciones_distribucion'], {
            'VENCIDA': 1,
            'VENCE_HOY': 1,
            'VENCE_PRONTO': 1,
            'AL_DIA': 1,
        })

    def test_filtro_rapido_de_obligaciones_se_combina_con_rango(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        self._crear_cuota(credito, numero=1, vencimiento=hoy + timedelta(days=5))
        request = self.factory.get('/gestion/', {
            'obligacion': 'VENCE_PRONTO',
            'fecha_desde': hoy.isoformat(),
            'fecha_hasta': (hoy + timedelta(days=7)).isoformat(),
        })

        context = get_admin_obligaciones_context(request)

        self.assertEqual(context['pagina_obligaciones'].paginator.count, 1)
        self.assertEqual(context['obligaciones'][0]['credito_id'], credito.pk)

    @override_settings(ADMIN_DASHBOARD_EMPRESA_TOP_N=2)
    def test_graficas_usan_eventos_persistidos_y_cartera_top_n_mas_otros(self):
        tz = timezone.get_current_timezone()
        credito_a = self._crear_credito(
            empresa=self.empresa_a,
            saldo=Decimal('1200.00'),
            fecha_desembolso=timezone.make_aware(datetime(2026, 4, 5, 9), tz),
        )
        self._crear_credito(
            empresa=self.empresa_b,
            saldo=Decimal('800.00'),
            fecha_desembolso=timezone.make_aware(datetime(2026, 4, 8, 9), tz),
        )
        empresa_c = Empresa.objects.create(
            nombre='Empresa Analitica C',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self._crear_credito(
            empresa=empresa_c,
            saldo=Decimal('300.00'),
        )
        cuota = self._crear_cuota(
            credito_a,
            numero=1,
            vencimiento=timezone.localdate() + timedelta(days=20),
        )
        self._crear_detalle_contable(
            credito_a,
            cuota,
            timezone.make_aware(datetime(2026, 4, 12, 9), tz),
        )

        context = get_admin_dashboard_context(self.staff)

        self.assertEqual(json.loads(context['recaudo_capital']), [300.0])
        self.assertEqual(json.loads(context['desembolso_cantidad']), [2])
        self.assertEqual(json.loads(context['desembolso_monto']), [3000.0])
        self.assertEqual(
            json.loads(context['cartera_empresa_labels']),
            [self.empresa_a.nombre, self.empresa_b.nombre, 'OTROS'],
        )
        self.assertEqual(json.loads(context['cartera_empresa_data']), [1200.0, 800.0, 300.0])
        self.assertEqual(json.loads(context['solicitud_cantidad']), [3])
        self.assertEqual(json.loads(context['estado_chart_labels']), [Credito.EstadoCredito.ACTIVO])
        self.assertEqual(json.loads(context['estado_chart_data']), [3])
        self.assertEqual(json.loads(context['obligaciones_chart_data']), [0, 0, 0, 1])
        self.assertNotIn('portfolio_labels', context)

    def test_consultas_no_crecen_por_cada_credito_u_obligacion(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        self._crear_cuota(credito, numero=1, vencimiento=hoy + timedelta(days=20))

        with CaptureQueriesContext(connection) as consultas_base:
            get_admin_dashboard_context(self.staff)

        for indice in range(8):
            credito = self._crear_credito(saldo=Decimal(900 - indice))
            self._crear_cuota(
                credito,
                numero=1,
                vencimiento=hoy + timedelta(days=indice + 1),
            )

        with CaptureQueriesContext(connection) as consultas_cartera_ampliada:
            get_admin_dashboard_context(self.staff)

        self.assertLessEqual(
            len(consultas_cartera_ampliada),
            len(consultas_base) + 1,
            'El dashboard agregó consultas proporcionales a créditos u obligaciones.',
        )

    def test_dashboard_renderiza_nueva_analitica_sin_falso_historico(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('gestion:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Saldo Capital Pendiente')
        self.assertContains(response, 'Obligaciones Pendientes')
        self.assertContains(response, 'Recaudo mensual')
        self.assertContains(response, 'id="advancedFiltersToggle"', html=False)
        self.assertContains(response, 'id="analyticsChart"', html=False)
        self.assertContains(response, 'No hay información para el período seleccionado.')
        self.assertNotContains(response, 'id="recaudoChart"', html=False)
        self.assertNotContains(response, 'Análisis Financiero')
        self.assertNotContains(response, 'Evolución de Cartera (Saldo Mensual)')

    def test_dashboard_solo_renderiza_card_de_acceso_a_obligaciones(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('gestion:dashboard'))

        self.assertNotIn('obligaciones_pendientes', response.context)
        self.assertContains(response, reverse('gestion:obligaciones_pendientes'), count=1)
        self.assertContains(response, 'Obligaciones Pendientes')
        self.assertContains(response, 'Consultar cuotas por vencer y vencidas')
        self.assertNotContains(response, 'id="obligacionesTitle"', html=False)
        self.assertNotContains(response, 'Ver obligaciones pendientes')
        self.assertNotContains(response, '<th>Próxima cuota</th>', html=False)

    def test_filtros_avanzados_conservan_valores_y_muestran_estado_activo(self):
        self._crear_credito(empresa=self.empresa_a)
        self.client.force_login(self.staff)

        response = self.client.get(reverse('gestion:dashboard'), {
            'fecha_desde': '2026-08-01',
            'fecha_hasta': '2026-08-31',
            'empresa': self.empresa_a.nombre,
            'estado': Credito.EstadoCredito.ACTIVO,
            'linea': Credito.LineaCredito.LIBRANZA,
        })

        self.assertContains(response, 'aria-expanded="true"', html=False)
        self.assertContains(response, 'value="2026-08-01"', html=False)
        self.assertContains(response, f'value="{self.empresa_a.nombre}" selected', html=False)
        self.assertContains(response, 'class="advanced-filters is-open"', html=False)

    def test_filtros_operativos_de_obligaciones_clasifican_cada_estado(self):
        hoy = timezone.localdate()
        casos = (
            ('VENCIDA', hoy - timedelta(days=1)),
            ('VENCE_HOY', hoy),
            ('VENCE_PRONTO', hoy + timedelta(days=5)),
            ('AL_DIA', hoy + timedelta(days=20)),
        )
        creditos = {}
        for estado, vencimiento in casos:
            credito = self._crear_credito()
            self._crear_cuota(credito, numero=1, vencimiento=vencimiento)
            creditos[estado] = credito.pk

        for estado, credito_id in creditos.items():
            with self.subTest(estado=estado):
                context = get_admin_obligaciones_context(
                    self.factory.get(
                        '/gestion/obligaciones-pendientes/',
                        {'obligacion': estado},
                    )
                )
                self.assertEqual(
                    [item['credito_id'] for item in context['obligaciones']],
                    [credito_id],
                )

        todas = get_admin_obligaciones_context(
            self.factory.get(
                '/gestion/obligaciones-pendientes/',
                {'obligacion': 'TODAS'},
            )
        )
        self.assertEqual(todas['pagina_obligaciones'].paginator.count, 4)

    def test_vista_obligaciones_requiere_staff(self):
        url = reverse('gestion:obligaciones_pendientes')
        response_anonimo = self.client.get(url)
        usuario = User.objects.create_user(username='dashboard-normal', password='123456')
        self.client.force_login(usuario)

        response_usuario = self.client.get(url)

        self.assertEqual(response_anonimo.status_code, 302)
        self.assertEqual(response_usuario.status_code, 302)

    def test_vista_obligaciones_filtra_al_dia_y_busca_numero_credito(self):
        hoy = timezone.localdate()
        credito_al_dia = self._crear_credito()
        self._crear_cuota(credito_al_dia, numero=1, vencimiento=hoy + timedelta(days=20))
        credito_vencido = self._crear_credito()
        self._crear_cuota(credito_vencido, numero=1, vencimiento=hoy - timedelta(days=1))
        self.client.force_login(self.staff)

        response = self.client.get(reverse('gestion:obligaciones_pendientes'), {
            'obligacion': 'AL_DIA',
            'credito': credito_al_dia.numero_credito,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pagina_obligaciones'].paginator.count, 1)
        self.assertContains(response, credito_al_dia.numero_credito)
        self.assertNotContains(response, credito_vencido.numero_credito)
        self.assertContains(response, 'Al día')

    def test_vista_obligaciones_pagina_sin_cargar_toda_la_cartera(self):
        hoy = timezone.localdate()
        for indice in range(21):
            credito = self._crear_credito()
            self._crear_cuota(
                credito,
                numero=1,
                vencimiento=hoy + timedelta(days=indice + 1),
            )
        self.client.force_login(self.staff)

        response = self.client.get(reverse('gestion:obligaciones_pendientes'), {
            'empresa': self.empresa_a.nombre,
            'page': 2,
        })

        pagina = response.context['pagina_obligaciones']
        self.assertEqual(pagina.number, 2)
        self.assertEqual(pagina.paginator.count, 21)
        self.assertEqual(len(response.context['obligaciones']), 1)
        self.assertIn('empresa=Empresa+Analitica+A', response.context['obligaciones_querystring'])
        self.assertNotIn('page=', response.context['obligaciones_querystring'])
        self.assertContains(response, 'empresa=Empresa+Analitica+A&amp;page=1', html=False)

    def test_consultas_vista_obligaciones_no_crecen_por_registro(self):
        hoy = timezone.localdate()
        credito = self._crear_credito()
        self._crear_cuota(credito, numero=1, vencimiento=hoy + timedelta(days=20))
        request = self.factory.get('/gestion/obligaciones-pendientes/')
        with CaptureQueriesContext(connection) as consultas_base:
            get_admin_obligaciones_context(request)

        for indice in range(8):
            credito = self._crear_credito()
            self._crear_cuota(credito, numero=1, vencimiento=hoy + timedelta(days=indice + 1))
        with CaptureQueriesContext(connection) as consultas_ampliadas:
            get_admin_obligaciones_context(request)

        self.assertLessEqual(len(consultas_ampliadas), len(consultas_base) + 1)

import json
from datetime import datetime, timedelta
from decimal import Decimal

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
from gestion_creditos.services.dashboard_metrics import get_admin_dashboard_context


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

        context = get_admin_dashboard_context(self.staff)

        por_credito = {
            item['credito_id']: item for item in context['obligaciones_pendientes']
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

        context = get_admin_dashboard_context(self.staff, request)

        self.assertEqual(context['obligaciones_total'], 1)
        self.assertEqual(context['obligaciones_pendientes'][0]['credito_id'], credito.pk)

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
        self.assertContains(response, 'Obligaciones pendientes')
        self.assertContains(response, 'Recaudo mensual')
        self.assertNotContains(response, 'Evolución de Cartera (Saldo Mensual)')

from datetime import date
from decimal import Decimal
import inspect
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.models import (
    AprobacionPagadorLibranza,
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    HistorialPago,
)
from gestion_creditos.services import aprobacion_pagador_libranza
from usuarios.models import PerfilPagador


User = get_user_model()


class PagadorDashboardTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagador UX',
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
            convenio_activo=True,
        )
        self.user = User.objects.create_user(
            username='pagador-ux',
            email='pagador-ux@aprobado.test',
            password='123456',
            first_name='Pagador',
            last_name='UX',
        )
        PerfilPagador.objects.create(usuario=self.user, empresa=self.empresa, es_pagador=True)
        self.client.login(username='pagador-ux', password='123456')

    def _crear_credito_libranza(self, numero, estado=Credito.EstadoCredito.ACTIVO, cuota='100.00'):
        empleado = User.objects.create_user(
            username=f'user-{numero.lower()}',
            email=f'{numero.lower()}@aprobado.test',
            password='123456',
        )
        credito = Credito.objects.create(
            usuario=empleado,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            numero_credito=numero,
            monto_solicitado=Decimal('1000.00'),
            monto_aprobado=Decimal('1000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal(cuota),
            saldo_pendiente=Decimal('200.00') if estado != Credito.EstadoCredito.PAGADO else Decimal('0.00'),
            capital_pendiente=Decimal('200.00') if estado != Credito.EstadoCredito.PAGADO else Decimal('0.00'),
            total_a_pagar=Decimal('200.00'),
            comision=Decimal('0.00'),
            iva_comision=Decimal('0.00'),
            fecha_proximo_pago=date(2026, 4, 30),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=self.empresa,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico=f'{numero.lower()}@empresa.test',
            cedula=f'{numero[-3:]}123',
            nombres='Empleado',
            apellidos=numero[-2:],
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 4, 30),
            capital_a_pagar=Decimal('80.00'),
            interes_a_pagar=Decimal('20.00'),
            valor_cuota=Decimal(cuota),
            saldo_capital_pendiente=Decimal('100.00'),
            pagada=estado == Credito.EstadoCredito.PAGADO,
            monto_pagado=Decimal(cuota) if estado == Credito.EstadoCredito.PAGADO else Decimal('0.00'),
        )
        return credito

    def _comprobante_prueba(self, nombre='comprobante.pdf'):
        return SimpleUploadedFile(nombre, b'%PDF-1.4 comprobante de prueba', content_type='application/pdf')

    def test_dashboard_principal_muestra_pago_directo_en_la_tabla(self):
        self._crear_credito_libranza('CR-PAG-001', estado=Credito.EstadoCredito.ACTIVO)
        self._crear_credito_libranza('CR-PAG-002', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="per_page"', html=False)
        self.assertContains(response, 'Aplicar pagos seleccionados')
        self.assertContains(response, 'Respaldo operativo por Excel')
        self.assertNotContains(response, 'Completar datos de usuarios existentes')

    def test_dashboard_preserva_paginacion_y_filtros(self):
        for index in range(12):
            self._crear_credito_libranza(f'CR-PAG-{index:03d}', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.get(reverse('pagador:dashboard'), {'per_page': 10, 'page': 2, 'search': 'Empleado'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Página 2 de 2')
        self.assertContains(response, 'per_page=10')
        self.assertContains(response, 'search=Empleado')

    def test_pago_directo_redirige_al_siguiente_contexto(self):
        credito = self._crear_credito_libranza('CR-PAG-900', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'comprobante': self._comprobante_prueba(),
                'nota': 'Pago directo desde tabla',
                'next': '/pagador/adelantos/?page=2&per_page=10',
                'origen': 'adelantos',
            },
        )

        self.assertRedirects(response, '/pagador/adelantos/?page=2&per_page=10', fetch_redirect_response=False)

    def test_pago_directo_requiere_comprobante(self):
        credito = self._crear_credito_libranza('CR-PAG-SOP', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'nota': 'Pago sin soporte',
            },
        )

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertFalse(HistorialPago.objects.filter(credito=credito).exists())

    def test_pago_directo_no_muestra_transferencia_directa(self):
        self._crear_credito_libranza('CR-PAG-MET', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.get(reverse('pagador:dashboard'))
        detalle = self.client.get(reverse('pagador:credito_detalle', args=[Credito.objects.get(numero_credito='CR-PAG-MET').id]))

        self.assertNotContains(response, 'Transferencia directa')
        self.assertNotContains(detalle, 'Transferencia directa')
        self.assertContains(response, 'Registro offline manual')

    def test_pagador_puede_ver_comprobante_de_su_empresa(self):
        credito = self._crear_credito_libranza('CR-PAG-COMP', estado=Credito.EstadoCredito.ACTIVO)
        self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'comprobante': self._comprobante_prueba('soporte-propio.pdf'),
                'nota': 'Pago con soporte',
            },
        )
        pago = HistorialPago.objects.get(credito=credito)

        response = self.client.get(reverse('pagador:comprobante_pago', args=[pago.id]))

        self.assertEqual(response.status_code, 200)

    def test_admin_staff_ve_soporte_de_pago(self):
        credito = self._crear_credito_libranza('CR-PAG-ADM', estado=Credito.EstadoCredito.ACTIVO)
        self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'comprobante': self._comprobante_prueba('soporte-admin.pdf'),
                'nota': 'Pago con soporte admin',
            },
        )
        admin = User.objects.create_superuser(
            username='admin-soporte',
            email='admin-soporte@aprobado.test',
            password='123456',
        )
        self.client.force_login(admin)

        response = self.client.get(reverse('admin:gestion_creditos_historialpago_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ver soporte')

    def test_pagador_no_puede_ver_comprobante_de_otra_empresa(self):
        credito = self._crear_credito_libranza('CR-PAG-OTRA', estado=Credito.EstadoCredito.ACTIVO)
        self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {
                'obligaciones': [str(credito.id)],
                f'monto_{credito.id}': '100.00',
                'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
                'comprobante': self._comprobante_prueba('soporte-ajeno.pdf'),
                'nota': 'Pago con soporte',
            },
        )
        pago = HistorialPago.objects.get(credito=credito)
        otra_empresa = Empresa.objects.create(nombre='Otra empresa', tipo_empresa=Empresa.TipoEmpresa.MIXTA, convenio_activo=True)
        otro_pagador = self._crear_pagador('pagador-ajeno', empresa=otra_empresa)
        self.client.force_login(otro_pagador)

        response = self.client.get(reverse('pagador:comprobante_pago', args=[pago.id]))

        self.assertEqual(response.status_code, 404)

    def test_pago_directo_bloquea_duplicado_mismo_dia(self):
        credito = self._crear_credito_libranza('CR-PAG-DUP', estado=Credito.EstadoCredito.ACTIVO)
        payload = {
            'obligaciones': [str(credito.id)],
            f'monto_{credito.id}': '100.00',
            'metodo_pago': HistorialPago.MetodoPago.OFFLINE_MANUAL,
            'nota': 'Pago duplicado',
        }

        primera = self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {**payload, 'comprobante': self._comprobante_prueba('soporte-1.pdf')},
        )
        segunda = self.client.post(
            reverse('pagador:pagar_obligaciones'),
            {**payload, 'comprobante': self._comprobante_prueba('soporte-2.pdf')},
        )

        self.assertRedirects(primera, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertRedirects(segunda, reverse('pagador:dashboard'), fetch_redirect_response=False)
        self.assertEqual(HistorialPago.objects.filter(credito=credito).count(), 1)

    def test_detalle_credito_carga_sin_nameerror(self):
        credito = self._crear_credito_libranza('CR-PAG-DET', estado=Credito.EstadoCredito.ACTIVO)

        response = self.client.get(reverse('pagador:credito_detalle', args=[credito.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Detalle del Crédito')
        self.assertContains(response, 'Registrar pago offline')

    def test_dashboard_ignora_residuales_minimos_y_muestra_siguiente_cuota_real(self):
        credito = self._crear_credito_libranza('CR-PAG-ROUND', estado=Credito.EstadoCredito.ACTIVO, cuota='100.00')
        cuota_1 = credito.tabla_amortizacion.get(numero_cuota=1)
        cuota_1.monto_pagado = Decimal('99.25')
        cuota_1.save(update_fields=['monto_pagado'])
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 5, 30),
            capital_a_pagar=Decimal('80.00'),
            interes_a_pagar=Decimal('20.00'),
            valor_cuota=Decimal('100.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
        )
        credito.saldo_pendiente = Decimal('100.75')
        credito.save(update_fields=['saldo_pendiente'])

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cuota 2')
        self.assertContains(response, 'value="100.00"', html=False)

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.gestionar_cambio_estado_credito')
    def test_decision_pagador_aprueba_solicitud_sin_error_de_bloqueo(self, cambio_estado_mock, preparar_mock):
        credito = self._crear_credito_libranza('CR-PAG-DEC', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.post(
            reverse('pagador:decidir_solicitud', args=[credito.id]),
            {'action': 'approve', 'motivo': 'Aprobado en prueba'},
        )

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        cambio_estado_mock.assert_called_once()
        preparar_mock.assert_called_once()

    def test_servicio_bloquea_solo_credito_sin_select_related_nullable(self):
        fuente = inspect.getsource(aprobacion_pagador_libranza.decidir_solicitud_libranza_por_pagador)

        self.assertIn("select_for_update(of=('self',))", fuente)
        self.assertNotIn('select_related', fuente)

    def _crear_pagador(self, username, empresa=None, nivel=None):
        empresa = empresa or self.empresa
        user = User.objects.create_user(
            username=username,
            email=f'{username}@aprobado.test',
            password='123456',
        )
        PerfilPagador.objects.create(
            usuario=user,
            empresa=empresa,
            es_pagador=True,
            nivel_aprobacion_libranza=nivel or PerfilPagador.NivelAprobacionLibranza.AMBOS,
        )
        return user

    def _post_decision(self, credito, usuario, action='approve', motivo='Revision pagador'):
        self.client.force_login(usuario)
        return self.client.post(
            reverse('pagador:decidir_solicitud', args=[credito.id]),
            {'action': action, 'motivo': motivo},
        )

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_empresa_normal_conserva_aprobacion_unica(self, preparar_mock):
        credito = self._crear_credito_libranza('CR-PAG-NORMAL', estado=Credito.EstadoCredito.EN_REVISION)

        response = self._post_decision(credito, self.user, action='approve')

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.APROBADO_PAGADOR)
        preparar_mock.assert_called_once()
        self.assertEqual(AprobacionPagadorLibranza.objects.filter(credito=credito).count(), 1)

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_doble_aprobacion_nivel_1_deja_pendiente_final_sin_firma(self, preparar_mock):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        nivel_1 = self._crear_pagador(
            'pagador-nivel-1',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        credito = self._crear_credito_libranza('CR-PAG-N1', estado=Credito.EstadoCredito.EN_REVISION)

        response = self._post_decision(credito, nivel_1, action='approve')

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL)
        preparar_mock.assert_not_called()
        aprobacion = AprobacionPagadorLibranza.objects.get(credito=credito)
        self.assertEqual(aprobacion.nivel, AprobacionPagadorLibranza.Nivel.NIVEL_1)
        self.assertEqual(aprobacion.decision, AprobacionPagadorLibranza.Decision.APROBADO)

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_doble_aprobacion_final_continua_flujo_existente(self, preparar_mock):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        nivel_1 = self._crear_pagador(
            'pagador-nivel-1-final',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        final = self._crear_pagador(
            'pagador-final',
            nivel=PerfilPagador.NivelAprobacionLibranza.FINAL,
        )
        credito = self._crear_credito_libranza('CR-PAG-FIN', estado=Credito.EstadoCredito.EN_REVISION)

        self._post_decision(credito, nivel_1, action='approve')
        response = self._post_decision(credito, final, action='approve')

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.APROBADO_PAGADOR)
        preparar_mock.assert_called_once()
        self.assertEqual(AprobacionPagadorLibranza.objects.filter(credito=credito).count(), 2)
        self.assertTrue(
            AprobacionPagadorLibranza.objects.filter(
                credito=credito,
                nivel=AprobacionPagadorLibranza.Nivel.FINAL,
                decision=AprobacionPagadorLibranza.Decision.APROBADO,
            ).exists()
        )

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_mismo_usuario_bloqueado_si_empresa_exige_aprobadores_distintos(self, preparar_mock):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.requiere_aprobadores_distintos_libranza = True
        self.empresa.save(update_fields=[
            'requiere_doble_aprobacion_libranza',
            'requiere_aprobadores_distintos_libranza',
        ])
        credito = self._crear_credito_libranza('CR-PAG-DIST', estado=Credito.EstadoCredito.EN_REVISION)

        self._post_decision(credito, self.user, action='approve')
        self._post_decision(credito, self.user, action='approve')

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL)
        preparar_mock.assert_not_called()
        self.assertFalse(
            AprobacionPagadorLibranza.objects.filter(
                credito=credito,
                nivel=AprobacionPagadorLibranza.Nivel.FINAL,
            ).exists()
        )

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_final_no_puede_decidir_en_revision_en_doble_aprobacion(self, preparar_mock):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        final = self._crear_pagador(
            'pagador-final-sin-n1',
            nivel=PerfilPagador.NivelAprobacionLibranza.FINAL,
        )
        credito = self._crear_credito_libranza('CR-PAG-WRONG', estado=Credito.EstadoCredito.EN_REVISION)

        self._post_decision(credito, final, action='approve')

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        preparar_mock.assert_not_called()
        self.assertEqual(AprobacionPagadorLibranza.objects.filter(credito=credito).count(), 0)

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_nivel_1_no_puede_decidir_pendiente_aprobacion_final(self, preparar_mock):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        nivel_1_inicial = self._crear_pagador(
            'pagador-nivel-1-inicial',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        nivel_1_otro = self._crear_pagador(
            'pagador-nivel-1-otro',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        credito = self._crear_credito_libranza('CR-PAG-N1-BLOCK', estado=Credito.EstadoCredito.EN_REVISION)

        self._post_decision(credito, nivel_1_inicial, action='approve')
        self._post_decision(credito, nivel_1_otro, action='approve')

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_APROBACION_FINAL)
        preparar_mock.assert_not_called()
        self.assertFalse(
            AprobacionPagadorLibranza.objects.filter(
                credito=credito,
                nivel=AprobacionPagadorLibranza.Nivel.FINAL,
            ).exists()
        )

    def test_final_no_ve_accion_dashboard_ni_detalle_en_revision(self):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        final = self._crear_pagador(
            'pagador-final-ui',
            nivel=PerfilPagador.NivelAprobacionLibranza.FINAL,
        )
        credito = self._crear_credito_libranza('CR-PAG-FINAL-UI', estado=Credito.EstadoCredito.EN_REVISION)
        self.client.force_login(final)

        dashboard = self.client.get(reverse('pagador:dashboard'))
        detalle = self.client.get(reverse('pagador:credito_detalle', args=[credito.id]))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(dashboard, f'decisionModal-{credito.id}')
        self.assertNotContains(detalle, reverse('pagador:decidir_solicitud', args=[credito.id]))

    def test_nivel_1_no_ve_accion_dashboard_ni_detalle_en_pendiente_final(self):
        self.empresa.requiere_doble_aprobacion_libranza = True
        self.empresa.save(update_fields=['requiere_doble_aprobacion_libranza'])
        nivel_1_inicial = self._crear_pagador(
            'pagador-nivel-1-ui-inicial',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        nivel_1_otro = self._crear_pagador(
            'pagador-nivel-1-ui-otro',
            nivel=PerfilPagador.NivelAprobacionLibranza.NIVEL_1,
        )
        credito = self._crear_credito_libranza('CR-PAG-N1-UI', estado=Credito.EstadoCredito.EN_REVISION)
        self._post_decision(credito, nivel_1_inicial, action='approve')
        self.client.force_login(nivel_1_otro)

        dashboard = self.client.get(reverse('pagador:dashboard'))
        detalle = self.client.get(reverse('pagador:credito_detalle', args=[credito.id]))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(dashboard, f'decisionModal-{credito.id}')
        self.assertNotContains(detalle, reverse('pagador:decidir_solicitud', args=[credito.id]))

    @patch('gestion_creditos.services.aprobacion_pagador_libranza.credit_services.preparar_documento_para_firma')
    def test_usuario_de_otra_empresa_no_aprueba(self, preparar_mock):
        otra_empresa = Empresa.objects.create(
            nombre='Empresa externa pagador',
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
            convenio_activo=True,
        )
        externo = self._crear_pagador('pagador-externo', empresa=otra_empresa)
        credito = self._crear_credito_libranza('CR-PAG-EXT', estado=Credito.EstadoCredito.EN_REVISION)

        self._post_decision(credito, externo, action='approve')

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.EN_REVISION)
        preparar_mock.assert_not_called()
        self.assertEqual(AprobacionPagadorLibranza.objects.filter(credito=credito).count(), 0)

from datetime import date
from decimal import Decimal
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    HistorialEstado,
    HistorialPago,
    Pagare,
    VinculoLaboralEmpresa,
)
from usuarios.models import PerfilPagador
from contractors.models import (
    ConfiguracionPortalContratistas,
    ContractorApplication,
    InformacionLaboralSolicitudContratista,
)


User = get_user_model()


class PagadorDashboardTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

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

    def _crear_credito_libranza(
        self,
        numero,
        estado=Credito.EstadoCredito.ACTIVO,
        cuota='100.00',
        empresa=None,
        cedula=None,
        nombres='Empleado',
        apellidos=None,
    ):
        empresa = empresa or self.empresa
        apellidos = apellidos if apellidos is not None else numero[-2:]
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
            empresa=empresa,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico=f'{numero.lower()}@empresa.test',
            cedula=cedula or f'{numero[-3:]}123',
            nombres=nombres,
            apellidos=apellidos,
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

    def _crear_vinculo(self, *, nombre, documento, salario='2000000.00', validado=True):
        usuario = User.objects.create_user(
            username=f'emp-{documento}',
            email=f'{documento}@empresa.test',
            password='123456',
        )
        return VinculoLaboralEmpresa.objects.create(
            usuario=usuario,
            empresa=self.empresa,
            documento_empleado=documento,
            nombre_empleado=nombre,
            correo_empleado=usuario.email,
            telefono_empleado='3001234567',
            estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            fecha_alta_aprobado=date(2026, 1, 1),
            salario_base_mensual=Decimal(salario) if salario is not None else None,
            auxilio_transporte_mensual=Decimal('0.00'),
            descuentos_fijos_mensuales=Decimal('0.00'),
            validado_por_pagador=validado,
        )

    def _crear_solicitud_prestador(self, *, documento='880001', empresa=None, nombre='Prestador', apellido='Servicios'):
        empresa = empresa or self.empresa
        portal, _ = ConfiguracionPortalContratistas.objects.get_or_create(
            slug='portal-pagador-test',
            defaults={
                'nombre_visible': 'Portal Prestadores Test',
                'host': 'contratistas.localhost',
                'activo': True,
                'monto_minimo': Decimal('1000000.00'),
                'monto_maximo': Decimal('10000000.00'),
                'plazo_minimo_meses': 1,
                'plazo_maximo_meses': 24,
                'tasa_mensual': Decimal('2.0000'),
            },
        )
        usuario = User.objects.create_user(
            username=f'prestador-{documento}',
            email=f'prestador-{documento}@aprobado.test',
            password='123456',
        )
        solicitud = ContractorApplication.objects.create(
            configuracion_portal=portal,
            usuario=usuario,
            status=ContractorApplication.Estado.RECIBIDA,
            document_type='CC',
            document_number=documento,
            first_name=nombre,
            last_name=apellido,
            phone='3001234567',
            email=usuario.email,
            address='Calle 123',
            accepted_terms=True,
            source_subdomain='contratistas',
        )
        InformacionLaboralSolicitudContratista.objects.create(
            solicitud=solicitud,
            empresa=empresa,
            cargo='Consultor',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=date(2026, 1, 1),
            fecha_fin_contrato=date(2026, 12, 31),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('2000000.00'),
            valor_pendiente_cobrar=Decimal('10000000.00'),
            empresa_contratante_nombre=empresa.nombre,
            empresa_contratante_nit=getattr(empresa, 'nit', ''),
        )
        return solicitud

    def test_dashboard_principal_muestra_pago_directo_en_la_tabla(self):
        self._crear_credito_libranza('CR-PAG-001', estado=Credito.EstadoCredito.ACTIVO)
        self._crear_credito_libranza('CR-PAG-002', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="per_page"', html=False)
        self.assertContains(response, 'Aplicar pagos seleccionados')
        self.assertContains(response, 'Respaldo operativo por Excel')
        self.assertNotContains(response, 'Completar datos de usuarios existentes')
        self.assertNotContains(response, 'Gestión directa de empleados')
        self.assertNotContains(response, 'Documentación del trabajador')

    def test_dashboard_documentacion_trabajador_solo_muestra_documentos_permitidos_de_su_empresa(self):
        credito = self._crear_credito_libranza('CR-PAG-DOC', estado=Credito.EstadoCredito.ACTIVO)
        detalle = credito.detalle_libranza
        detalle.cedula_frontal = SimpleUploadedFile('cedula-frontal.pdf', b'cedula', content_type='application/pdf')
        detalle.cedula_trasera = SimpleUploadedFile('cedula-trasera.pdf', b'cedula', content_type='application/pdf')
        detalle.certificado_laboral = SimpleUploadedFile('contrato.pdf', b'contrato', content_type='application/pdf')
        detalle.certificado_bancario = SimpleUploadedFile('certificado-bancario.pdf', b'banco', content_type='application/pdf')
        detalle.certificado_bancario_estado_extraccion = 'completo'
        detalle.save()

        otra_empresa = Empresa.objects.create(nombre='Empresa Ajena', tipo_empresa=Empresa.TipoEmpresa.CONVENIO)
        credito_otro = self._crear_credito_libranza('CR-PAG-OTR', estado=Credito.EstadoCredito.ACTIVO, empresa=otra_empresa)
        detalle_otro = credito_otro.detalle_libranza
        detalle_otro.nombres = 'Empleado'
        detalle_otro.apellidos = 'Ajeno'
        detalle_otro.cedula_frontal = SimpleUploadedFile('cedula-ajena.pdf', b'ajena', content_type='application/pdf')
        detalle_otro.save()

        Pagare.objects.create(
            credito=credito,
            archivo_pdf=SimpleUploadedFile('pagare-restringido.pdf', b'pagare', content_type='application/pdf'),
        )
        HistorialPago.objects.create(
            credito=credito,
            monto=Decimal('10.00'),
            referencia_pago='DOC-COMP-001',
            estado=HistorialPago.EstadoPago.EXITOSO,
            comprobante=SimpleUploadedFile('comprobante-pago.pdf', b'comprobante', content_type='application/pdf'),
        )
        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            estado_nuevo=Credito.EstadoCredito.ACTIVO,
            motivo='Desembolso',
            comprobante_pago=SimpleUploadedFile('transferencia-restringida.pdf', b'transferencia', content_type='application/pdf'),
        )

        dashboard = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, 'Documentación del trabajador')

        detalle_response = self.client.get(reverse('pagador:credito_detalle', args=[credito.id]))
        self.assertEqual(detalle_response.status_code, 200)
        self.assertContains(detalle_response, 'Ver documentación')

        response = self.client.get(reverse('pagador:credito_documentacion', args=[credito.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Documentación del crédito')
        self.assertContains(response, 'Cedula frontal')
        self.assertContains(response, 'Cedula reverso')
        self.assertContains(response, 'Contrato / soporte laboral')
        self.assertContains(response, 'Certificado bancario')
        self.assertContains(response, 'Validado')
        self.assertNotContains(response, 'Empleado Ajeno')
        self.assertNotContains(response, 'cedula-ajena.pdf')
        self.assertNotContains(response, 'pagare-restringido.pdf')
        self.assertNotContains(response, 'comprobante-pago.pdf')
        self.assertNotContains(response, 'transferencia-restringida.pdf')
        self.assertNotContains(response, 'Pagaré')

        preview = self.client.get(reverse('pagador:documento_preview'), {'path': detalle.cedula_frontal.name})
        self.assertEqual(preview.status_code, 200)
        preview_ajeno = self.client.get(reverse('pagador:documento_preview'), {'path': detalle_otro.cedula_frontal.name})
        self.assertEqual(preview_ajeno.status_code, 404)

    def test_dashboard_total_pagado_usa_subquery_correlacionada_por_credito(self):
        credito_uno = self._crear_credito_libranza('CR-PAG-SUB-1', estado=Credito.EstadoCredito.ACTIVO)
        credito_dos = self._crear_credito_libranza('CR-PAG-SUB-2', estado=Credito.EstadoCredito.ACTIVO)
        HistorialPago.objects.create(
            credito=credito_uno,
            monto=Decimal('10.00'),
            referencia_pago='SUB-001',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )
        HistorialPago.objects.create(
            credito=credito_uno,
            monto=Decimal('15.00'),
            referencia_pago='SUB-002',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )
        HistorialPago.objects.create(
            credito=credito_dos,
            monto=Decimal('40.00'),
            referencia_pago='SUB-003',
            estado=HistorialPago.EstadoPago.EXITOSO,
        )

        response = self.client.get(reverse('pagador:dashboard'), {'sort_by': 'fecha_solicitud'})

        self.assertEqual(response.status_code, 200)
        creditos = {credito.id: credito for credito in response.context['creditos'].object_list}
        self.assertEqual(creditos[credito_uno.id].total_pagado, Decimal('25'))
        self.assertEqual(creditos[credito_dos.id].total_pagado, Decimal('40'))

    def test_dashboard_gestion_empleados_renderiza_estados_y_filtros(self):
        self._crear_vinculo(nombre='EMPLEADO COMPLETO', documento='900001', salario='2000000.00', validado=True)
        self._crear_vinculo(nombre='EMPLEADO SIN INFO', documento='900002', salario=None, validado=True)
        self._crear_vinculo(nombre='EMPLEADO SIN VALIDAR', documento='900003', salario='1800000.00', validado=False)

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Colaboradores de la empresa')
        self.assertContains(response, 'Completo')
        self.assertContains(response, 'Pendiente información')
        self.assertContains(response, 'Pendiente validación')
        self.assertContains(response, 'Carga masiva opcional')
        self.assertContains(response, 'Descargar plantilla oficial')

        filtered = self.client.get(reverse('pagador:carga_empleados'), {'empleado_estado': 'pendiente_info'})
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'EMPLEADO SIN INFO')
        self.assertNotContains(filtered, 'EMPLEADO COMPLETO')
        self.assertNotContains(filtered, 'EMPLEADO SIN VALIDAR')

    def test_gestion_empleados_muestra_colaborador_solo_con_credito_libranza(self):
        self._crear_credito_libranza(
            'CR-PAG-LIVE',
            estado=Credito.EstadoCredito.EN_REVISION,
            cedula='990001',
            nombres='Solo',
            apellidos='Solicitud',
        )

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SOLO SOLICITUD')
        self.assertContains(response, 'Pendiente vínculo laboral')
        self.assertContains(response, 'Pendiente completar v')
        self.assertEqual(response.context['empleados_summary']['total'], 1)
        self.assertEqual(response.context['empleados_summary']['pendientes_vinculo'], 1)
        self.assertIn('solicitud_libranza', response.context['empleados_gestion'][0]['origenes'])

    def test_gestion_empleados_no_duplica_vinculo_y_credito_mismo_documento(self):
        self._crear_vinculo(nombre='EMPLEADO UNIFICADO', documento='990002', salario='2000000.00', validado=True)
        self._crear_credito_libranza(
            'CR-PAG-DUP',
            estado=Credito.EstadoCredito.ACTIVO,
            cedula='990002',
            nombres='Empleado',
            apellidos='Unificado',
        )

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EMPLEADO UNIFICADO')
        self.assertEqual(response.context['empleados_summary']['total'], 1)
        colaborador = response.context['empleados_gestion'][0]
        self.assertIn('vinculo_laboral', colaborador['origenes'])
        self.assertIn('solicitud_libranza', colaborador['origenes'])
        self.assertIn('credito_activo', colaborador['origenes'])

    def test_gestion_empleados_no_muestra_colaborador_de_otra_empresa(self):
        otra_empresa = Empresa.objects.create(nombre='Empresa Fuera Vista', tipo_empresa=Empresa.TipoEmpresa.CONVENIO)
        self._crear_credito_libranza(
            'CR-PAG-OUT',
            estado=Credito.EstadoCredito.ACTIVO,
            empresa=otra_empresa,
            cedula='990003',
            nombres='Empleado',
            apellidos='Ajeno',
        )

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'EMPLEADO AJENO')
        self.assertEqual(response.context['empleados_summary']['total'], 0)

    def test_gestion_empleados_total_supera_vinculos_cuando_hay_solicitudes(self):
        self._crear_vinculo(nombre='EMPLEADO CON VINCULO', documento='990004', salario='2000000.00', validado=True)
        self._crear_credito_libranza(
            'CR-PAG-LIVE-1',
            estado=Credito.EstadoCredito.EN_REVISION,
            cedula='990005',
            nombres='Solicitud',
            apellidos='Uno',
        )
        self._crear_credito_libranza(
            'CR-PAG-LIVE-2',
            estado=Credito.EstadoCredito.ACTIVO,
            cedula='990006',
            nombres='Solicitud',
            apellidos='Dos',
        )

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['empleados_summary']['total'], 3)
        self.assertEqual(response.context['empleados_summary']['con_credito_activo'], 1)
        self.assertContains(response, 'SOLICITUD UNO')
        self.assertContains(response, 'SOLICITUD DOS')

    def test_gestion_empleados_muestra_solicitud_de_prestador(self):
        self._crear_solicitud_prestador(documento='880001', nombre='Prestador', apellido='Visible')

        response = self.client.get(reverse('pagador:carga_empleados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Prestador Visible')
        self.assertContains(response, 'Pendiente v')
        self.assertEqual(response.context['empleados_summary']['en_solicitud'], 1)
        self.assertEqual(response.context['empleados_summary']['pendientes_vinculo'], 1)
        self.assertIn('solicitud_prestador', response.context['empleados_gestion'][0]['origenes'])

    def test_gestion_empleados_filtros_credito_activo_y_pendiente_vinculo(self):
        self._crear_vinculo(nombre='EMPLEADO ACTIVO', documento='770001', salario='2000000.00', validado=True)
        self._crear_credito_libranza('CR-PAG-FIL', estado=Credito.EstadoCredito.ACTIVO, cedula='770001', nombres='Empleado', apellidos='Activo')
        self._crear_solicitud_prestador(documento='770002', nombre='Prestador', apellido='Pendiente')

        activos = self.client.get(reverse('pagador:carga_empleados'), {'con_credito_activo': '1'})
        self.assertEqual(activos.status_code, 200)
        self.assertContains(activos, 'EMPLEADO ACTIVO')
        self.assertNotContains(activos, 'Prestador Pendiente')

        pendientes = self.client.get(reverse('pagador:carga_empleados'), {'pendiente_vinculo': '1'})
        self.assertEqual(pendientes.status_code, 200)
        self.assertContains(pendientes, 'Prestador Pendiente')
        self.assertNotContains(pendientes, 'EMPLEADO ACTIVO')

    def test_detalle_empleado_muestra_datos_y_documentacion_permitida(self):
        credito = self._crear_credito_libranza(
            'CR-PAG-EMPDET',
            estado=Credito.EstadoCredito.ACTIVO,
            cedula='880002',
            nombres='Empleado',
            apellidos='Detalle',
        )
        detalle = credito.detalle_libranza
        detalle.cedula_frontal = SimpleUploadedFile('cedula-detalle.pdf', b'cedula', content_type='application/pdf')
        detalle.certificado_laboral = SimpleUploadedFile('contrato-detalle.pdf', b'contrato', content_type='application/pdf')
        detalle.certificado_bancario = SimpleUploadedFile('banco-detalle.pdf', b'banco', content_type='application/pdf')
        detalle.save()
        Pagare.objects.create(
            credito=credito,
            archivo_pdf=SimpleUploadedFile('pagare-no-visible.pdf', b'pagare', content_type='application/pdf'),
        )

        response = self.client.get(reverse('pagador:empleado_detalle', args=['880002']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EMPLEADO DETALLE')
        self.assertContains(response, 'Documentaci')
        self.assertContains(response, 'Cedula frontal')
        self.assertContains(response, 'Contrato / soporte laboral')
        self.assertContains(response, 'Certificado bancario')
        self.assertNotContains(response, 'pagare-no-visible.pdf')
        self.assertNotContains(response, 'DataCr')
        self.assertNotContains(response, 'score')

    def test_detalle_empleado_no_expone_documento_de_otra_empresa(self):
        otra_empresa = Empresa.objects.create(nombre='Empresa Detalle Ajena', tipo_empresa=Empresa.TipoEmpresa.CONVENIO)
        self._crear_credito_libranza(
            'CR-PAG-DET-OUT',
            estado=Credito.EstadoCredito.ACTIVO,
            empresa=otra_empresa,
            cedula='880003',
            nombres='Empleado',
            apellidos='Ajeno',
        )

        response = self.client.get(reverse('pagador:empleado_detalle', args=['880003']))

        self.assertEqual(response.status_code, 404)

    def test_pagador_actualiza_empleado_de_su_empresa_y_no_de_otra(self):
        vinculo = self._crear_vinculo(nombre='EMPLEADO EDITABLE', documento='910001', salario=None, validado=False)
        otra_empresa = Empresa.objects.create(nombre='Empresa Empleado Ajeno', tipo_empresa=Empresa.TipoEmpresa.CONVENIO)
        otro_usuario = User.objects.create_user(username='empleado-ajeno', email='ajeno@empresa.test', password='123456')
        vinculo_ajeno = VinculoLaboralEmpresa.objects.create(
            usuario=otro_usuario,
            empresa=otra_empresa,
            documento_empleado='910002',
            nombre_empleado='EMPLEADO AJENO',
            correo_empleado='ajeno@empresa.test',
            estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            fecha_alta_aprobado=date(2026, 1, 1),
        )

        response = self.client.post(
            reverse('pagador:actualizar_empleado', args=[vinculo.id]),
            {
                'nombre_empleado': 'Empleado Editable Actualizado',
                'documento_empleado': '910001',
                'correo_empleado': 'editable@empresa.test',
                'telefono_empleado': '3000000000',
                'fecha_alta_aprobado': '2026-01-01',
                'salario_base_mensual': '2500000',
                'auxilio_transporte_mensual': '162000',
                'descuentos_fijos_mensuales': '200000',
                'estado_vinculo': VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
                'validado_por_pagador': 'on',
            },
        )

        self.assertRedirects(response, reverse('pagador:carga_empleados'), fetch_redirect_response=False)
        vinculo.refresh_from_db()
        self.assertEqual(vinculo.nombre_empleado, 'EMPLEADO EDITABLE ACTUALIZADO')
        self.assertEqual(vinculo.salario_base_mensual, Decimal('2500000'))
        self.assertTrue(vinculo.validado_por_pagador)

        response_ajeno = self.client.post(
            reverse('pagador:actualizar_empleado', args=[vinculo_ajeno.id]),
            {
                'nombre_empleado': 'NO DEBE CAMBIAR',
                'documento_empleado': '910002',
                'fecha_alta_aprobado': '2026-01-01',
                'estado_vinculo': VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            },
        )
        self.assertEqual(response_ajeno.status_code, 404)
        vinculo_ajeno.refresh_from_db()
        self.assertEqual(vinculo_ajeno.nombre_empleado, 'EMPLEADO AJENO')

    def test_dashboard_principal_no_muestra_bloque_de_pagadores_activos(self):
        segundo = User.objects.create_user(
            username='pagador-ux-2',
            email='pagador2@aprobado.test',
            password='123456',
        )
        PerfilPagador.objects.create(usuario=segundo, empresa=self.empresa, es_pagador=True)

        response = self.client.get(reverse('pagador:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'pagador-ux@aprobado.test')
        self.assertNotContains(response, 'pagador2@aprobado.test')

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
                'metodo_pago': HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
                'nota': 'Pago directo desde tabla',
                'next': '/pagador/adelantos/?page=2&per_page=10',
                'origen': 'adelantos',
            },
        )

        self.assertRedirects(response, '/pagador/adelantos/?page=2&per_page=10', fetch_redirect_response=False)

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

    @patch('gestion_creditos.views.pagador.credit_services.preparar_documento_para_firma')
    @patch('gestion_creditos.views.pagador.credit_services.gestionar_cambio_estado_credito')
    def test_decision_pagador_aprueba_solicitud_sin_error_de_bloqueo(self, cambio_estado_mock, preparar_mock):
        credito = self._crear_credito_libranza('CR-PAG-DEC', estado=Credito.EstadoCredito.EN_REVISION)

        response = self.client.post(
            reverse('pagador:decidir_solicitud', args=[credito.id]),
            {'action': 'approve', 'motivo': 'Aprobado en prueba'},
        )

        self.assertRedirects(response, reverse('pagador:dashboard'), fetch_redirect_response=False)
        cambio_estado_mock.assert_called_once()
        preparar_mock.assert_called_once()

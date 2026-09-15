import logging
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import TestCase as CleanupCase
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.signing import TimestampSigner
from django.db import connection
from django.http import FileResponse
from django.test import TestCase, override_settings
from django.template import Context, Template
from django.urls import reverse

from gestion_creditos.document_logging import OcultarTokenPagare
from gestion_creditos.models import (
    Credito, CreditoLibranza, Empresa, Pagare, CuentaAhorro, MovimientoAhorro,
    AsesorComercial, PagoComisionEjecutivo, MarketplaceItem, LotePagoEmpresa,
    CreditoEmprendimiento, ImagenNegocio, HistorialPago, HistorialEstado,
)
from gestion_creditos.services.documentos_privados import url_documento
from gestion_creditos.tests.response_helpers import finalizar_respuesta_cliente
from usuarios.models import PerfilPagador


class DocumentosPrivadosTest(TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        override = override_settings(MEDIA_ROOT=str(Path(carpeta.name) / 'media'),
            PRIVATE_DOCUMENTS_ROOT=str(Path(carpeta.name) / 'private'), PROTECTED_LEGACY_X_ACCEL=False)
        override.enable()
        self.addCleanup(override.disable)
        User = get_user_model()
        self.owner = User.objects.create_user('document-owner')
        self.otro = User.objects.create_user('document-otro')
        self.staff = User.objects.create_user('document-staff', is_staff=True)
        self.empresa = Empresa.objects.create(nombre='Documentos', convenio_activo=True)
        self.ajena = Empresa.objects.create(nombre='Otra empresa', convenio_activo=True)
        self.credito = Credito.objects.create(usuario=self.owner, linea='LIBRANZA',
            monto_solicitado=1000000, plazo_solicitado=3)
        self.nombre = default_storage.save('credito_libranza/cedulas/historico.pdf', ContentFile(b'%PDF-1.4\nprueba'))
        self.detalle = CreditoLibranza.objects.create(credito=self.credito, empresa=self.empresa,
            cedula='1234567', cedula_frontal=self.nombre, certificado_bancario=self.nombre)
        self.url = url_documento(self.detalle.cedula_frontal)

    def permiso(self, usuario, codename):
        usuario.user_permissions.add(Permission.objects.get(content_type__app_label='gestion_creditos', codename=codename))

    def get(self, usuario, url=None, status=200):
        self.client.logout()
        if usuario:
            self.client.force_login(usuario)
        response = self.client.get(url or self.url)
        self.addCleanup(finalizar_respuesta_cliente, response)
        self.assertEqual(response.status_code, status)
        self.assertIn('no-store', response.get('Cache-Control', ''))
        return response

    def test_cierre_streaming_y_cleanup_conservan_conexion_del_testcase(self):
        conexion_original = connection.connection
        for consumir in (False, True):
            with patch('gestion_creditos.services.documentos_privados.FileResponse', wraps=FileResponse) as respuesta_archivo:
                response = self.get(self.owner)
                archivo = respuesta_archivo.call_args.args[0]
            with patch.object(connection, 'close', wraps=connection.close) as cierre:
                if consumir:
                    self.assertTrue(b''.join(response.streaming_content))
                finalizar_respuesta_cliente(response)
                finalizar_respuesta_cliente(response)
                cierre.assert_not_called()
            self.assertTrue(response.closed)
            self.assertTrue(archivo.closed)
            self.assertIs(connection.connection, conexion_original)
            self.assertFalse(connection.closed_in_transaction)
            self.assertTrue(connection.is_usable())
            self.assertTrue(Credito.objects.filter(pk=self.credito.pk).exists())
            self.client.logout()
            self.client.force_login(self.owner)

        # Ejecutar un cleanup real sin adelantar la limpieza del storage/fixtures.
        with patch('gestion_creditos.services.documentos_privados.FileResponse', wraps=FileResponse) as respuesta_archivo:
            response = self.client.get(self.url)
            archivo = respuesta_archivo.call_args.args[0]
        cleanup_case = CleanupCase()
        cleanup_case.addCleanup(finalizar_respuesta_cliente, response)
        with patch.object(connection, 'close', wraps=connection.close) as cierre:
            self.assertTrue(cleanup_case.doCleanups())
            cierre.assert_not_called()
        self.assertTrue(archivo.closed)
        self.assertIs(connection.connection, conexion_original)
        self.assertTrue(connection.is_usable())
        self.client.logout()
        self.client.force_login(self.owner)

    def test_propietario_anonimo_y_ajeno(self):
        self.get(None, status=404)
        self.get(self.otro, status=404)
        response = self.get(self.owner)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4\nprueba')
        self.assertNotIn('historico', response['Content-Disposition'])
        self.assertNotIn('/media/', self.url)

    def test_staff_necesita_permiso_explicito(self):
        self.get(self.staff, status=404)
        self.permiso(self.staff, 'view_credito')
        self.get(self.staff, status=404)
        self.permiso(self.staff, 'view_identity_documents')
        self.get(self.staff)

    def test_pagador_empresa_inactivo_y_permiso_accidental(self):
        perfil = PerfilPagador.objects.create(usuario=self.staff, empresa=self.empresa)
        self.permiso(self.staff, 'view_credito')
        self.get(self.staff)
        perfil.empresa = self.ajena
        perfil.save()
        self.get(self.staff, status=404)
        perfil.empresa = self.empresa
        perfil.es_pagador = False
        perfil.save()
        self.get(self.staff, status=404)

    def test_usuario_inactivo_no_descarga(self):
        self.owner.is_active = False
        self.owner.save()
        self.get(self.owner, status=404)

    def test_preview_por_path_traversal_y_campo_no_permitido(self):
        self.permiso(self.staff, 'view_credito')
        for path in (self.nombre, '../../.env'):
            self.get(self.staff, reverse('gestion:documento_preview') + '?path=' + path, 404)
        self.get(self.owner, self.url.replace('cedula_frontal', 'cedula'), 404)
        self.get(self.owner, self.url + '?path=../../.env')

    @override_settings(PROTECTED_LEGACY_X_ACCEL=True)
    def test_x_accel_solo_despues_de_autorizar(self):
        denied = self.get(self.otro, status=404)
        self.assertNotIn('X-Accel-Redirect', denied)
        response = self.get(self.owner)
        self.assertEqual(response['X-Accel-Redirect'], '/_protected_legacy/' + self.nombre)
        self.assertFalse(response.streaming)
        self.assertEqual(response.content, b'')

    def test_referencia_manipulada_no_sale_de_raices(self):
        self.detalle.cedula_frontal = '../fuera.pdf'
        self.detalle.save(update_fields=['cedula_frontal'])
        self.get(self.owner, status=404)

    @override_settings(PROTECTED_LEGACY_X_ACCEL=True)
    def test_nueva_captura_sigue_privada_y_no_se_mueve_historico(self):
        self.detalle.cedula_trasera.save('cedula.png', ContentFile(b'imagen'), save=True)
        archivo = self.detalle.cedula_trasera
        self.assertTrue(archivo.name.startswith('identidad/'))
        response = self.get(self.owner, url_documento(archivo))
        self.assertNotIn('X-Accel-Redirect', response)
        self.assertTrue(default_storage.exists(self.nombre))

    def test_lote_no_permite_cliente_individual(self):
        lote = LotePagoEmpresa.objects.create(empresa=self.empresa, archivo=self.nombre)
        url = url_documento(lote.archivo)
        self.get(self.owner, url, 404)
        PerfilPagador.objects.create(usuario=self.otro, empresa=self.empresa)
        self.get(self.otro, url)
        self.permiso(self.staff, 'view_lotepagoempresa')
        self.get(self.staff, url)

    def test_billetera_owner_y_permiso(self):
        cuenta = CuentaAhorro.objects.create(usuario=self.owner, tipo_usuario='NATURAL')
        movimiento = MovimientoAhorro.objects.create(cuenta=cuenta, tipo='DEPOSITO_OFFLINE',
            monto=100, referencia='ID00-1', comprobante=self.nombre)
        url = url_documento(movimiento.comprobante)
        self.get(self.owner, url)
        self.get(self.otro, url, 404)
        self.get(self.staff, url, 404)
        self.permiso(self.staff, 'view_movimientoahorro')
        self.get(self.staff, url)

    def test_comision_ejecutivo_y_staff_con_permiso(self):
        asesor = AsesorComercial.objects.create(usuario=self.owner, nombre='Ejecutivo', cedula='555')
        pago = PagoComisionEjecutivo.objects.create(asesor=asesor, monto=100, comprobante=self.nombre)
        url = url_documento(pago.comprobante)
        self.get(self.owner, url)
        self.get(self.otro, url, 404)
        self.permiso(self.staff, 'view_pagocomisionejecutivo')
        self.get(self.staff, url)

    def test_pagare_token_valido_invalido_expirado_y_staff(self):
        pagare = Pagare.objects.create(credito=self.credito, numero_pagare='PAG-ID00', archivo_pdf=self.nombre)
        url = url_documento(pagare.archivo_pdf)
        self.get(self.staff, url, 404)
        self.permiso(self.staff, 'view_pagare')
        self.get(self.staff, url)
        token = TimestampSigner().sign(f'{pagare.pk}:3600')
        token_url = reverse('descargar_pagare_publico', args=[token])
        self.get(None, token_url)
        self.get(None, reverse('descargar_pagare_publico', args=['incorrecto']), 403)
        with patch('django.core.signing.time.time', return_value=1):
            expired = TimestampSigner().sign(f'{pagare.pk}:1')
        self.get(None, reverse('descargar_pagare_publico', args=[expired]), 410)

    def test_token_no_se_registra_en_logs(self):
        token = TimestampSigner().sign('9999999:3600')
        with self.assertLogs('zapsign', level='WARNING') as logs:
            self.get(None, reverse('descargar_pagare_publico', args=[token]), 404)
        self.assertNotIn(token, str(logs.output))
        record = logging.LogRecord('django.request', 30, '', 0, 'Not Found: %s',
            ('/api/pagares/download/' + token + '/',), None)
        OcultarTokenPagare().filter(record)
        self.assertNotIn(token, record.getMessage())

    def test_marketplace_imagen_logo_no_cambian_video_no_publico(self):
        self.empresa.logo = 'marketplace/logos/publico.png'
        self.assertEqual(self.empresa.logo.url, '/media/marketplace/logos/publico.png')
        item = MarketplaceItem.objects.create(empresa=self.empresa, titulo='Publico',
            imagen='marketplace/items/publico.png', video=self.nombre)
        self.assertEqual(item.imagen.url, '/media/marketplace/items/publico.png')
        self.get(None, url_documento(item.video), 404)
        self.get(self.owner, url_documento(item.video), 404)
        self.permiso(self.staff, 'view_marketplaceitem')
        self.get(self.staff, url_documento(item.video))

    def test_admin_widget_no_expone_media_ni_nombre(self):
        from gestion_creditos.document_widgets import DocumentoPrivadoInput
        html = DocumentoPrivadoInput().render('cedula_frontal', self.detalle.cedula_frontal)
        self.assertIn(self.url, html)
        self.assertNotIn('/media/', html)
        self.assertNotIn('historico.pdf', html)

    def test_tag_genera_link_objeto_no_storage(self):
        html = Template('{% load documentos_privados %}{% documento_url archivo %}').render(
            Context({'archivo': self.detalle.cedula_frontal}))
        self.assertEqual(html, self.url)

    def test_historial_pago_y_pagare_firmado_por_objeto(self):
        pago = HistorialPago.objects.create(credito=self.credito, monto=100, comprobante=self.nombre)
        estado = HistorialEstado.objects.create(credito=self.credito, estado_anterior='FIRMADO',
            estado_nuevo='PENDIENTE_TRANSFERENCIA', comprobante_pago=self.nombre)
        pagare = Pagare.objects.create(credito=self.credito, numero_pagare='PAG-ID00',
            archivo_pdf=self.nombre, archivo_pdf_firmado=self.nombre)
        for archivo in (pago.comprobante, estado.comprobante_pago, pagare.archivo_pdf_firmado):
            self.get(self.owner, url_documento(archivo))
            self.get(self.otro, url_documento(archivo), 404)

    def test_imagenes_negocio_privadas_y_admin_readonly(self):
        credito = Credito.objects.create(usuario=self.owner, linea='EMPRENDIMIENTO',
            monto_solicitado=1000000, plazo_solicitado=3)
        detalle = CreditoEmprendimiento.objects.create(credito=credito, foto_negocio=self.nombre,
            fecha_nac='1990-01-01', numero_personas_cargo=0, dias_trabajados_sem=5,
            ingresos_prom_mes=2000000, cli_aten_day=10)
        imagen = ImagenNegocio.objects.create(credito_emprendimiento=detalle, imagen=self.nombre)
        self.get(self.owner, url_documento(imagen.imagen))
        self.get(self.otro, url_documento(imagen.imagen), 404)
        self.staff.is_superuser = True
        self.staff.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin:gestion_creditos_credito_change', args=[credito.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, url_documento(detalle.foto_negocio))
        self.assertNotContains(response, '/media/credito_libranza/')

    def test_documento_prestador_owner_interno_y_no_pagador(self):
        from contractors.models import ContractorApplication, ContractorApplicationDocument
        solicitud = ContractorApplication.objects.create(usuario=self.owner, empresa=self.empresa,
            tipo_documento='CC', numero_documento='12345678', nombres='Prueba', apellidos='Privada',
            tipo_contrato='PRESTACION_SERVICIOS', estado='DOCUMENTOS_PENDIENTES')
        documento = ContractorApplicationDocument.objects.create(solicitud=solicitud,
            tipo_documento='CONTRATO', archivo=self.nombre, uploaded_by=self.owner)
        url = url_documento(documento.archivo)
        self.get(self.owner, url)
        self.get(self.otro, url, 404)
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label='contractors', codename='can_view_contractor_review_queue'))
        self.get(self.staff, url)
        PerfilPagador.objects.create(usuario=self.staff, empresa=self.empresa)
        self.get(self.staff, url, 404)

    def test_todos_los_campos_libranza_permitidos_sin_path(self):
        for campo in ('cedula_trasera', 'certificado_laboral', 'contrato_prestacion_servicios',
                      'desprendible_nomina', 'certificado_bancario'):
            setattr(self.detalle, campo, self.nombre)
        self.detalle.save()
        for campo in ('cedula_frontal', 'cedula_trasera', 'certificado_laboral',
                      'contrato_prestacion_servicios', 'desprendible_nomina', 'certificado_bancario'):
            self.get(self.owner, url_documento(getattr(self.detalle, campo)))

    def test_admin_pagare_renderiza_editable_y_solo_lectura(self):
        pagare = Pagare.objects.create(credito=self.credito, numero_pagare='PAG-ID00', archivo_pdf=self.nombre,
            zapsign_signed_file_url='https://example.test/documento-privado-firmado.pdf')
        self.permiso(self.staff, 'view_pagare')
        self.client.force_login(self.staff)
        for change in (False, True):
            if change:
                self.permiso(self.staff, 'change_pagare')
            response = self.client.get(reverse('admin:gestion_creditos_pagare_change', args=[pagare.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, url_documento(pagare.archivo_pdf))
            self.assertNotContains(response, '/media/credito_libranza/')
            self.assertNotContains(response, pagare.zapsign_signed_file_url)

    def test_excel_pagador_enlaces_protegidos_y_solo_su_empresa(self):
        from openpyxl import load_workbook
        self.credito.estado = Credito.EstadoCredito.EN_REVISION
        self.credito.save(update_fields=['estado'])
        PerfilPagador.objects.create(usuario=self.otro, empresa=self.empresa)
        self.client.force_login(self.otro)
        response = self.client.get(reverse('pagador:descargar_reporte'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        workbook = load_workbook(BytesIO(response.content))
        self.addCleanup(workbook.close)
        celdas = [str(cell.value) for row in workbook.active for cell in row if cell.value is not None]
        self.assertTrue(any(self.url in valor for valor in celdas))
        self.assertTrue(any(url_documento(self.detalle.certificado_bancario) in valor for valor in celdas))
        self.assertFalse(any('/media/' in valor for valor in celdas))

    def test_billetera_dashboard_link_protegido(self):
        cuenta = CuentaAhorro.objects.create(usuario=self.owner, tipo_usuario='NATURAL')
        movimiento = MovimientoAhorro.objects.create(cuenta=cuenta, tipo='DEPOSITO_OFFLINE',
            monto=100, referencia='ID00-1', comprobante=self.nombre)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('billetera:gestion_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, url_documento(movimiento.comprobante))
        self.assertNotContains(response, '/media/credito_libranza/')

    def test_documentacion_admin_preview_objeto_sin_path(self):
        self.permiso(self.staff, 'view_credito')
        self.client.force_login(self.staff)
        response = self.client.get(reverse('gestion:credito_documentacion', args=[self.credito.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)
        self.assertNotContains(response, '?path=')
        self.assertNotContains(response, '/media/credito_libranza/')

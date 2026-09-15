import tempfile
from datetime import timedelta
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contractors.models import ContractorApplication, ContractorApplicationDocument
from gestion_creditos.models import (
    SesionCapturaDocumental as Sesion, CapturaDocumentoIdentidad as Captura,
    EventoCapturaDocumental as Evento, Credito, CreditoLibranza, Empresa,
)
from gestion_creditos.services import captura_documental as servicio
from gestion_creditos.storage import documentos_solicitud_storage
from gestion_creditos.tests.captura_fixtures import imagen_documental, sesion_finalizada
from usuarios.models import PerfilPagador


class CapturaFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        settings = override_settings(MEDIA_ROOT=str(Path(self.temporary.name) / 'media'),
                                     PRIVATE_DOCUMENTS_ROOT=str(Path(self.temporary.name) / 'privado'))
        settings.enable()
        self.addCleanup(settings.disable)
        self.usuario = get_user_model().objects.create_user('captura-owner')
        self.otro = get_user_model().objects.create_user('captura-otro')
        self.staff = get_user_model().objects.create_user('captura-staff', is_staff=True)
        self.empresa = Empresa.objects.create(nombre='Empresa captura', convenio_activo=True)
        self.sesion, self.token = servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')
        self.params = {'sesion_id': self.sesion.pk, 'actor': self.usuario, 'producto': 'LIBRANZA'}

    def canjear(self):
        return servicio.canjear_sesion(**self.params, token=self.token, vinculo='browser')

    def cargar(self, lado='FRONTAL', color='red'):
        return servicio.recibir_captura(**self.params, lado=lado, archivo=imagen_documental(color), vinculo='browser')

    def completar(self):
        self.canjear()
        self.cargar()
        self.cargar('TRASERA', 'blue')
        return servicio.finalizar_sesion(**self.params, vinculo='browser')

    def solicitud(self, usuario=None, pk=None):
        return ContractorApplication.objects.create(pk=pk, usuario=usuario or self.usuario, empresa=self.empresa,
            tipo_documento='CC', numero_documento='12345678', nombres='Prueba', apellidos='Captura',
            tipo_contrato='PRESTACION_SERVICIOS', estado='DOCUMENTOS_PENDIENTES')


class CapturaDocumentalTest(CapturaFixture, TestCase):
    def test_borrador_no_crea_credito_y_token_solo_hash(self):
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())
        self.assertEqual(len(self.sesion.token_hash), 64)
        self.assertNotEqual(self.token, self.sesion.token_hash)
        self.assertNotIn(self.token, str(Sesion.objects.values().get()))
        self.assertEqual(Evento.objects.get().evento, 'SESION_CREADA')

    def test_token_incorrecto_y_reutilizado(self):
        with self.assertRaises(ValidationError):
            servicio.canjear_sesion(**self.params, token='incorrecto', vinculo='browser')
        self.canjear()
        with self.assertRaises(ValidationError):
            self.canjear()
        self.assertEqual(Evento.objects.filter(evento='CANJE').count(), 1)

    def test_propietario_producto_contexto_y_pagador(self):
        for cambios in ({'actor': self.otro}, {'producto': 'PRESTADORES'}, {'solicitud_id': self.solicitud(self.otro).pk}):
            with self.subTest(cambios=cambios), self.assertRaises((PermissionDenied, ValidationError)):
                servicio.canjear_sesion(**{**self.params, **cambios}, token=self.token, vinculo='browser')
        PerfilPagador.objects.create(usuario=self.usuario, empresa=self.empresa)
        with self.assertRaises(PermissionDenied):
            servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')

    def test_expiracion_persistida_y_sesion_revocada(self):
        Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            self.canjear()
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.estado, 'EXPIRADA')
        self.assertTrue(Evento.objects.filter(evento='EXPIRACION').exists())
        otra, token = servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')
        params = {**self.params, 'sesion_id': otra.pk}
        servicio.revocar_sesion(**params)
        with self.assertRaises(ValidationError):
            servicio.canjear_sesion(**params, token=token, vinculo='browser')

    def test_regeneracion_invalida_token_y_browser_anterior(self):
        self.canjear()
        captura = self.cargar()
        _, token = servicio.regenerar_enlace(**self.params)
        with self.assertRaises(ValidationError):
            self.canjear()
        with self.assertRaises(PermissionDenied):
            self.cargar()
        servicio.canjear_sesion(**self.params, token=token, vinculo='browser-nuevo')
        with self.assertRaises(PermissionDenied):
            self.cargar()
        captura.refresh_from_db()
        self.assertFalse(captura.activo)

    def test_lados_separados_y_finalizacion_idempotente(self):
        self.canjear()
        self.cargar()
        with self.assertRaises(ValidationError):
            servicio.finalizar_sesion(**self.params, vinculo='browser')
        self.cargar('TRASERA', 'blue')
        for _ in range(2):
            servicio.finalizar_sesion(**self.params, vinculo='browser')
        self.assertEqual(Evento.objects.filter(evento='FINALIZACION').count(), 1)
        self.assertEqual(Captura.objects.count(), 2)
        self.assertEqual(set(Captura.objects.values_list('identidad', flat=True)), {'IDENTIDAD_NO_VERIFICADA'})

    def test_no_mezcla_capturas_otra_sesion(self):
        self.canjear()
        self.cargar()
        sesion_finalizada(self.usuario)
        with self.assertRaises(ValidationError):
            servicio.finalizar_sesion(**self.params, vinculo='browser')

    def test_reemplazo_versionado_idempotente_y_rollback(self):
        self.canjear()
        anterior = self.cargar()
        self.assertEqual(self.cargar().pk, anterior.pk)
        with patch('gestion_creditos.services.captura_documental._evento', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.cargar(color='green')
        anterior.refresh_from_db()
        self.assertTrue(anterior.activo)
        self.assertEqual(Captura.objects.count(), 1)
        nueva = self.cargar(color='blue')
        self.assertNotEqual(nueva.pk, anterior.pk)
        self.assertEqual(Captura.objects.filter(activo=True).count(), 1)
        self.assertTrue(anterior.archivo.storage.exists(anterior.archivo.name))

    def test_invalidos_y_mime_real(self):
        self.canjear()
        for archivo in (SimpleUploadedFile('cedula.jpg', b'invalido', content_type='image/jpeg'),
                        SimpleUploadedFile('cedula.pdf', b'%PDF-1.4')):
            with self.subTest(nombre=archivo.name), self.assertRaises(ValidationError):
                servicio.recibir_captura(**self.params, vinculo='browser', lado='FRONTAL', archivo=archivo)
        captura = self.cargar()
        self.assertEqual(captura.metadata['formato'], 'PNG')
        self.assertEqual(captura.estado_tecnico, 'VALIDO_TECNICAMENTE')

    def test_archivo_privado_nombre_aleatorio_y_descarga_autorizada(self):
        self.canjear()
        captura = self.cargar()
        with self.assertRaises(ValueError):
            _ = captura.archivo.url
        self.assertNotIn('documento', captura.archivo.name)
        self.assertTrue(Path(captura.archivo.path).is_relative_to(Path(self.temporary.name) / 'privado'))
        url = reverse('captura:descargar', args=[captura.pk])
        for usuario, esperado in ((self.usuario, 200), (self.otro, 404), (self.staff, 404)):
            self.client.force_login(usuario)
            response = self.client.get(url)
            self.assertEqual(response.status_code, esperado)
            response.close()
        self.staff.user_permissions.add(Permission.objects.get(codename='view_identity_documents'))
        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('cedula-frontal.jpg', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        response.close()

    def test_historicos_se_leen_sin_mover_y_nuevos_privados(self):
        nombre = default_storage.save('credito_libranza/cedulas/legacy.jpg', imagen_documental())
        with documentos_solicitud_storage.open(nombre) as archivo:
            self.assertGreater(len(archivo.read()), 0)
        nuevo = documentos_solicitud_storage.save('credito_libranza/cedulas/123456789-persona.png', imagen_documental())
        self.assertTrue(nuevo.startswith('identidad/'))
        self.assertTrue(default_storage.exists(nombre))
        self.assertFalse(default_storage.exists(nuevo))
        with self.assertRaises(ValueError):
            documentos_solicitud_storage.url(nuevo)
        credito = Credito.objects.create(usuario=self.usuario, linea='LIBRANZA', monto_solicitado=1000000, plazo_solicitado=3)
        CreditoLibranza.objects.create(credito=credito, empresa=self.empresa, cedula='9999999', cedula_frontal=nombre, cedula_trasera=nuevo)
        self.client.force_login(self.usuario)
        for lado in ('FRONTAL', 'TRASERA'):
            response = self.client.get(reverse('captura:cedula_credito', args=[credito.pk, lado]))
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_get_no_consume_y_csrf_para_canje(self):
        self.client.force_login(self.usuario)
        url = reverse('captura:continuar', args=['LIBRANZA', self.sesion.pk])
        for _ in range(2):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.estado, 'ABIERTA')
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        action = reverse('captura:operar', args=['LIBRANZA', self.sesion.pk, 'canjear'])
        self.assertEqual(cliente.post(action, {'token': self.token}).status_code, 403)
        response = cliente.get(url)
        csrf = cliente.cookies['csrftoken'].value
        self.assertEqual(cliente.post(action, {'token': self.token, 'csrfmiddlewaretoken': csrf}).status_code, 200)
        self.assertEqual(cliente.post(action, {'token': self.token, 'csrfmiddlewaretoken': csrf}).status_code, 400)
        self.assertNotIn(self.token, str(list(Evento.objects.values())))

    def test_no_carga_desde_browser_sin_canje(self):
        self.canjear()
        self.client.force_login(self.usuario)
        url = reverse('captura:operar', args=['LIBRANZA', self.sesion.pk, 'FRONTAL'])
        response = self.client.post(url, {'archivo': imagen_documental()})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Captura.objects.exists())

    @patch('gestion_creditos.views.solicitudes.procesar_certificado_bancario')
    def test_libranza_integra_finalizados_y_no_admite_reuso(self, procesar):
        self.completar()
        self.client.force_login(self.usuario)
        def datos():
            return {'sesion_documental_id': str(self.sesion.pk), 'valor_credito': '1000000',
                'plazo': '3', 'ingresos_mensuales': '3000000', 'nombres': 'Carlos', 'apellidos': 'Ramirez',
                'cedula': '123456789', 'direccion': 'Calle Principal 123', 'telefono': '3001234567',
                'correo_electronico': 'cliente@example.test', 'empresa': self.empresa.pk,
                'certificado_bancario': SimpleUploadedFile('certificado.pdf', b'%PDF-prueba', content_type='application/pdf')}
        response = self.client.post(reverse('libranza:solicitar'), datos())
        self.assertEqual(response.status_code, 302)
        credito = Credito.objects.get()
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.credito_id, credito.pk)
        self.assertEqual(self.sesion.estado, 'UTILIZADA')
        self.assertTrue(credito.detalle_libranza.cedula_frontal.name.startswith('identidad/'))
        self.client.post(reverse('libranza:solicitar'), datos())
        self.assertEqual(Credito.objects.count(), 1)

    def test_libranza_rechaza_upload_libre(self):
        self.client.force_login(self.usuario)
        response = self.client.post(reverse('libranza:solicitar'), {'cedula_frontal': imagen_documental()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Credito.objects.exists())

    def test_prestadores_contexto_y_carga_no_identitaria(self):
        solicitud = self.solicitud()
        self.client.force_login(self.usuario)
        url = reverse('contractors:documentos', args=[solicitud.pk], urlconf='aprobado_web.urls_contractors')
        for tipo in ('CEDULA_FRONTAL', 'CEDULA_TRASERA'):
            response = self.client.post(url, {'tipo_documento': tipo, 'archivo': imagen_documental()}, HTTP_HOST='contratistas.localhost')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(solicitud.documentos.exists())
        response = self.client.post(url, {'tipo_documento': 'CONTRATO', 'archivo': SimpleUploadedFile('contrato.pdf', b'%PDF-1.4')}, HTTP_HOST='contratistas.localhost')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.documentos.get().tipo_documento, 'CONTRATO')
        sesion = sesion_finalizada(self.usuario, 'PRESTADORES', solicitud)
        with transaction.atomic():
            _, capturas = servicio.obtener_documentos_finalizados(sesion_id=sesion.pk, actor=self.usuario,
                producto='PRESTADORES', solicitud_id=solicitud.pk)
            self.assertEqual(set(capturas), {'FRONTAL', 'TRASERA'})
            servicio.consumir_documentos(sesion=sesion, actor=self.usuario, solicitud=solicitud)
        self.assertFalse(Credito.objects.exists())

    @override_settings(USE_THOUSAND_SEPARATOR=True, LANGUAGE_CODE='es-co')
    def test_contexto_documental_prestador_no_localiza_identificadores(self):
        solicitud = self.solicitud(pk=1234)
        self.client.force_login(self.usuario)
        response = self.client.get('/solicitar/', {'solicitud_id': solicitud.pk}, HTTP_HOST='contratistas.localhost')
        self.assertContains(response, 'name="solicitud_id" value="1234"')
        self.assertContains(response, 'data-contexto="1234"')
        self.assertNotContains(response, 'data-contexto="1.234"')

    def test_purga_borradores_no_borra_vinculados_ni_historicos(self):
        self.completar()
        otra = sesion_finalizada(self.usuario)
        Sesion.objects.filter(pk=otra.pk).update(estado='UTILIZADA')
        Sesion.objects.all().update(expira_en=timezone.now() - timedelta(days=1))
        self.assertEqual(servicio.purgar_sesiones_expiradas(), 2)
        self.assertEqual(servicio.purgar_sesiones_expiradas(), 0)
        self.assertEqual(otra.capturas.filter(purgado_en__isnull=True).count(), 2)

    def test_subsanacion_identidad_exige_sesion_y_vincula_ambas_caras(self):
        from contractors.models import RevisionManualPrestador, RequerimientoSubsanacionPrestador
        solicitud = self.solicitud()
        revision = RevisionManualPrestador.objects.create(solicitud=solicitud, motivo='OTRA_REVISION_CONTROLADA')
        requerimiento = RequerimientoSubsanacionPrestador.objects.create(
            solicitud=solicitud, revision=revision, tipo='DOCUMENTO_IDENTIDAD', mensaje_publico='Actualizar cedula')
        url = reverse('contractors:atender_subsanacion', args=[solicitud.pk, requerimiento.pk],
                      urlconf='aprobado_web.urls_contractors')
        self.client.force_login(self.usuario)
        response = self.client.post(url, {'archivo': imagen_documental(), 'tipo_documento_carga': 'CEDULA_FRONTAL'},
                                    HTTP_HOST='contratistas.localhost')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-capture-handoff')
        self.assertFalse(solicitud.documentos.exists())
        sesion = sesion_finalizada(self.usuario, 'PRESTADORES', solicitud)
        response = self.client.post(url, {'sesion_documental_id': str(sesion.pk)}, HTTP_HOST='contratistas.localhost')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.documentos.count(), 2)
        self.assertTrue(all(d.archivo.name.startswith('identidad/') for d in solicitud.documentos.all()))
        sesion.refresh_from_db()
        requerimiento.refresh_from_db()
        self.assertEqual(sesion.estado, 'UTILIZADA')
        self.assertEqual(requerimiento.estado, 'ATENDIDO')


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real en VPS.')
class CapturaConcurrenciaPostgresTest(CapturaFixture, TransactionTestCase):
    def competir(self, funcion):
        barrera, resultados = Barrier(2), Queue()
        def worker(n):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                barrera.wait(10)
                funcion(n)
                resultados.put('OK')
            except ValidationError:
                resultados.put('RECHAZADO')
            except Exception as exc:
                resultados.put(type(exc).__name__)
            finally:
                connections.close_all()
        hilos = [Thread(target=worker, args=(n,)) for n in (0, 1)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(30)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        return list(resultados.queue)

    def test_canje_unico(self):
        self.assertCountEqual(self.competir(lambda n: self.canjear()), ['OK', 'RECHAZADO'])
        self.assertEqual(Evento.objects.filter(evento='CANJE').count(), 1)

    def test_doble_finalizacion(self):
        self.canjear()
        self.cargar()
        self.cargar('TRASERA', 'blue')
        self.assertEqual(self.competir(lambda n: servicio.finalizar_sesion(**self.params, vinculo='browser')), ['OK', 'OK'])
        self.assertEqual(Evento.objects.filter(evento='FINALIZACION').count(), 1)

    def test_reemplazo_serializado(self):
        self.canjear()
        self.assertEqual(self.competir(lambda n: self.cargar(color=('red', 'blue')[n])), ['OK', 'OK'])
        self.assertEqual(Captura.objects.filter(activo=True).count(), 1)
        self.assertEqual(Captura.objects.count(), 2)

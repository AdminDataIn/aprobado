from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.forms import MarketplaceItemForm
from gestion_creditos.models import Empresa, MarketplaceItem, MarketplaceItemHistorialEstado, Notificacion
from gestion_creditos.admin import MarketplaceItemAdminForm
from gestion_creditos.services.marketplace_service import (
    cambiar_estado_publicacion,
    registrar_historial_publicacion,
)
from usuarios.models import PerfilEmpresaMarketing


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test'
)
class MarketplaceFlowServiceTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='Empresa Demo')
        self.admin_user = User.objects.create_user(username='adminmk', password='123', is_staff=True)

        self.mk_user_1 = User.objects.create_user(
            username='mkdemo1',
            password='123',
            email='mk1@empresa.com'
        )
        self.mk_user_2 = User.objects.create_user(
            username='mkdemo2',
            password='123',
            email='mk2@empresa.com'
        )
        PerfilEmpresaMarketing.objects.create(usuario=self.mk_user_1, empresa=self.empresa, activo=True)
        PerfilEmpresaMarketing.objects.create(usuario=self.mk_user_2, empresa=self.empresa, activo=True)

        self.item = MarketplaceItem.objects.create(
            empresa=self.empresa,
            titulo='Producto demo',
            descripcion='Descripcion demo',
            beneficio='Beneficio demo',
            tipo=MarketplaceItem.TipoItem.PRODUCTO,
            estado=MarketplaceItem.EstadoItem.PENDIENTE,
        )

    def test_aprobar_publicacion_crea_historial_y_notificaciones(self):
        cambiar_estado_publicacion(
            item=self.item,
            estado_nuevo=MarketplaceItem.EstadoItem.APROBADO,
            usuario=self.admin_user,
            origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
            comentario='Aprobado por cumplimiento.'
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.estado, MarketplaceItem.EstadoItem.APROBADO)
        self.assertIsNotNone(self.item.fecha_publicacion)

        self.assertEqual(
            MarketplaceItemHistorialEstado.objects.filter(item=self.item, estado_nuevo=MarketplaceItem.EstadoItem.APROBADO).count(),
            1
        )
        self.assertEqual(Notificacion.objects.filter(usuario=self.mk_user_1).count(), 1)
        self.assertEqual(Notificacion.objects.filter(usuario=self.mk_user_2).count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_rechazo_exige_motivo_cuando_es_requerido(self):
        with self.assertRaises(ValidationError):
            cambiar_estado_publicacion(
                item=self.item,
                estado_nuevo=MarketplaceItem.EstadoItem.RECHAZADO,
                usuario=self.admin_user,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                comentario='',
                require_comment=True
            )

    def test_transicion_invalida_lanza_error(self):
        self.item.estado = MarketplaceItem.EstadoItem.INACTIVO
        self.item.save(update_fields=['estado'])

        with self.assertRaises(ValidationError):
            cambiar_estado_publicacion(
                item=self.item,
                estado_nuevo=MarketplaceItem.EstadoItem.APROBADO,
                usuario=self.admin_user,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                comentario='Intento invalido'
            )

    def test_registrar_historial_omite_duplicado_mismo_estado(self):
        result = registrar_historial_publicacion(
            item=self.item,
            estado_anterior=MarketplaceItem.EstadoItem.PENDIENTE,
            estado_nuevo=MarketplaceItem.EstadoItem.PENDIENTE,
            usuario=self.admin_user,
            origen=MarketplaceItemHistorialEstado.OrigenCambio.SISTEMA,
            comentario='Sin cambios'
        )
        self.assertIsNone(result)
        self.assertEqual(MarketplaceItemHistorialEstado.objects.filter(item=self.item).count(), 0)

    def test_admin_form_bloquea_rechazo_directo_sin_flujo(self):
        form = MarketplaceItemAdminForm(
            data={
                'empresa': self.empresa.id,
                'titulo': self.item.titulo,
                'descripcion': self.item.descripcion,
                'beneficio': self.item.beneficio,
                'tipo': self.item.tipo,
                'precio': self.item.precio,
                'whatsapp_contacto': self.item.whatsapp_contacto,
                'estado': MarketplaceItem.EstadoItem.RECHAZADO,
                'fecha_publicacion': '',
            },
            instance=self.item
        )
        self.assertFalse(form.is_valid())
        self.assertIn('usa el boton', str(form.errors))


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test'
)
class MarketplacePanelIntegrationTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='Empresa Panel')
        self.user = User.objects.create_user(username='mkpanel', password='123', email='mkpanel@empresa.com')
        PerfilEmpresaMarketing.objects.create(usuario=self.user, empresa=self.empresa, activo=True)
        self.item = MarketplaceItem.objects.create(
            empresa=self.empresa,
            titulo='Servicio inicial',
            descripcion='Primera version',
            beneficio='Beneficio inicial',
            tipo=MarketplaceItem.TipoItem.SERVICIO,
            estado=MarketplaceItem.EstadoItem.APROBADO,
        )

    def test_editar_publicacion_aprobada_la_envia_a_pendiente(self):
        self.client.login(username='mkpanel', password='123')
        response = self.client.post(
            reverse('marketplace:item_edit', args=[self.item.id]),
            data={
                'titulo': 'Servicio actualizado',
                'descripcion': 'Contenido nuevo',
                'beneficio': 'Nuevo beneficio',
                'tipo': MarketplaceItem.TipoItem.SERVICIO,
                'precio': '',
                'whatsapp_contacto': '573001234567',
            }
        )
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.estado, MarketplaceItem.EstadoItem.PENDIENTE)
        self.assertTrue(
            MarketplaceItemHistorialEstado.objects.filter(
                item=self.item,
                estado_nuevo=MarketplaceItem.EstadoItem.PENDIENTE
            ).exists()
        )


class MarketplaceItemFormVideoValidationTest(TestCase):
    def _base_data(self):
        return {
            'titulo': 'Oferta con video',
            'descripcion': 'Descripcion corta',
            'beneficio': 'Beneficio principal',
            'tipo': MarketplaceItem.TipoItem.SERVICIO,
            'precio': '',
            'whatsapp_contacto': '573001234567',
        }

    def test_form_acepta_video_mp4(self):
        video = SimpleUploadedFile(
            name='demo.mp4',
            content=b'fake-video',
            content_type='video/mp4'
        )
        form = MarketplaceItemForm(data=self._base_data(), files={'video': video})
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_form_rechaza_video_no_permitido(self):
        video = SimpleUploadedFile(
            name='demo.avi',
            content=b'fake-video',
            content_type='video/avi'
        )
        form = MarketplaceItemForm(data=self._base_data(), files={'video': video})
        self.assertFalse(form.is_valid())
        self.assertIn('Formato de video no permitido', str(form.errors))

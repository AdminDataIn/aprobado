from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.services import captura_documental as servicio
from gestion_creditos.tests.test_captura_documental import CapturaFixture
from gestion_creditos.tests.captura_fixtures import imagen_documental
from gestion_creditos.models import SesionCapturaDocumental as Sesion, CapturaDocumentoIdentidad as Captura
from gestion_creditos.models import EventoCapturaDocumental as Evento, Credito, CreditoLibranza
from gestion_creditos.views.captura_documental import CAPTURE_GRANT_COOKIE
from usuarios.models import PerfilPagador


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'])
class CaptureGrantTest(CapturaFixture, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.mobile = Client(enforce_csrf_checks=True)
        self.base = reverse('captura:movil', args=['LIBRANZA', self.sesion.pk])
        self.shell(self.mobile)

    def shell(self, client, base=None):
        return client.get(base or self.base, secure=True)

    def post(self, accion, data=None, client=None, base=None, **headers):
        client = client or self.mobile
        headers.setdefault('HTTP_ORIGIN', 'https://testserver')
        headers.setdefault('HTTP_X_CSRFTOKEN', client.cookies['csrftoken'].value)
        return client.post((base or self.base) + accion + '/', data or {}, secure=True, **headers)

    def canjear(self):
        return self.post('canjear', {'token': self.token})

    def test_pc_autenticado_movil_anonimo_flujo_completo_csrf_https(self):
        pc = Client(enforce_csrf_checks=True)
        pc.force_login(self.usuario)
        self.shell(pc)
        created = pc.post(reverse('captura:crear', args=['LIBRANZA']), secure=True,
                          HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=pc.cookies['csrftoken'].value)
        self.assertEqual(created.status_code, 201)
        link = urlsplit(created.json()['enlace'])
        self.base, self.token = link.path, link.fragment
        self.sesion = Sesion.objects.get(pk=created.json()['id'])
        self.assertIn('/movil/', self.base)
        self.assertEqual(self.shell(self.mobile).status_code, 200)
        response = self.canjear()
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[CAPTURE_GRANT_COOKIE]
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Strict')
        self.assertEqual(cookie['domain'], '')
        self.assertEqual(cookie['path'], self.base)
        self.assertGreater(int(cookie['max-age']), 0)
        self.assertLessEqual(int(cookie['max-age']), 600)
        self.sesion.refresh_from_db()
        self.assertNotEqual(self.sesion.capture_grant_hash, cookie.value)
        self.assertNotEqual(self.sesion.capture_grant_hash, servicio._hash(self.token))
        self.assertEqual(self.sesion.capture_grant_expira_en, self.sesion.expira_en)
        self.assertEqual(self.sesion.vinculo_hash, '')
        for lado in ('frontal', 'trasera'):
            self.assertEqual(self.post(lado, {'archivo': imagen_documental()}).status_code, 200)
        self.assertEqual(self.shell(self.mobile).status_code, 200)
        self.assertEqual(self.mobile.get(self.base + 'estado/', secure=True).json()['estado'], 'CANJEADA')
        finalized = self.post('finalizar')
        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(finalized.json()['estado'], 'FINALIZADA')
        self.assertNotIn('retorno', finalized.json())
        self.assertNotContains(self.shell(self.mobile), 'data-retorno-seguro')
        self.assertEqual(finalized.cookies[CAPTURE_GRANT_COOKIE]['max-age'], 0)
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.capture_grant_hash, '')
        self.assertIsNotNone(self.sesion.capture_grant_revocado_en)
        estado = pc.get(reverse('captura:estado', args=['LIBRANZA', self.sesion.pk]), secure=True)
        self.assertEqual(estado.json()['estado'], 'FINALIZADA')
        self.assertNotIn('sessionid', self.mobile.cookies)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())
        self.assertEqual(self.sesion.capturas.filter(activo=True).count(), 2)
        self.assertTrue(all(c.metadata['canal'] == 'CAPTURE_GRANT' for c in self.sesion.capturas.all()))
        self.assertTrue(all(e.actor_id is None for e in self.sesion.eventos.all() if 'DELEGAD' in e.evento))
        self.assertNotIn(cookie.value, str(list(Evento.objects.values())))
        self.assertNotIn(self.token, str(list(Evento.objects.values())))
        self.assertEqual(self.post('frontal', {'archivo': imagen_documental()}).status_code, 403)

    def test_finalizada_delegada_vence_grant_pero_no_evidencia(self):
        self.canjear()
        for lado in ('frontal', 'trasera'):
            self.assertEqual(self.post(lado, {'archivo': imagen_documental()}).status_code, 200)
        self.assertEqual(self.post('finalizar').json()['estado'], 'FINALIZADA')
        Sesion.objects.filter(pk=self.sesion.pk).update(expira_en=timezone.now() - timedelta(days=1))
        self.assertEqual(self.mobile.get(self.base + 'estado/', secure=True).status_code, 403)
        self.assertEqual(self.post('canjear', {'token': self.token}).status_code, 403)
        self.assertEqual(servicio.estado_publico(**self.params)['estado'], 'FINALIZADA')
        self.assertEqual(servicio.purgar_sesiones_expiradas(), 0)


    def test_grant_no_concede_endpoints_propietario(self):
        self.assertEqual(self.canjear().status_code, 200)
        for path in [reverse('libranza:solicitar'), reverse('captura:crear', args=['LIBRANZA']),
                     reverse('captura:estado', args=['LIBRANZA', self.sesion.pk]),
                     reverse('captura:operar', args=['LIBRANZA', self.sesion.pk, 'regenerar']),
                     reverse('captura:descargar', args=[uuid4()])]:
            with self.subTest(path=path):
                self.assertEqual(self.mobile.get(path, secure=True).status_code, 302)
        for accion in ('regenerar', 'revocar', 'originar'):
            self.assertEqual(self.post(accion).status_code, 404)
        self.assertNotIn('sessionid', self.mobile.cookies)

    def test_sesion_producto_y_contexto_no_se_pueden_cambiar(self):
        self.canjear()
        secret = self.mobile.cookies[CAPTURE_GRANT_COOKIE].value
        other, _ = servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')
        for changes in ({'sesion_id': other.pk}, {'producto': 'PRESTADORES'}, {'solicitud_id': 999}):
            with self.subTest(changes=changes), self.assertRaises(PermissionDenied):
                servicio.operar_capture_grant(**{'sesion_id': self.sesion.pk, 'producto': 'LIBRANZA',
                    'grant': secret, 'accion': 'estado', **changes})
        response = self.mobile.get(reverse('captura:movil', args=['LIBRANZA', other.pk]) + 'estado/', secure=True)
        self.assertEqual(response.status_code, 403)

    def test_grant_y_token_expirados(self):
        self.canjear()
        Sesion.objects.filter(pk=self.sesion.pk).update(capture_grant_expira_en=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.mobile.get(self.base + 'estado/', secure=True).status_code, 403)
        self.assertEqual(self.post('frontal', {'archivo': imagen_documental()}).status_code, 403)
        new, token = servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')
        Sesion.objects.filter(pk=new.pk).update(expira_en=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(PermissionDenied):
            servicio.canjear_capture_grant(sesion_id=new.pk, producto='LIBRANZA', token=token)

    def test_revocacion_y_regeneracion_invalidan_grant(self):
        for accion in ('regenerar_enlace', 'revocar_sesion'):
            with self.subTest(accion=accion):
                sesion, token = servicio.crear_sesion(actor=self.usuario, producto='LIBRANZA')
                _, grant = servicio.canjear_capture_grant(sesion_id=sesion.pk, producto='LIBRANZA', token=token)
                getattr(servicio, accion)(sesion_id=sesion.pk, actor=self.usuario, producto='LIBRANZA')
                sesion.refresh_from_db()
                self.assertEqual(sesion.capture_grant_hash, '')
                self.assertIsNotNone(sesion.capture_grant_revocado_en)
                self.assertNotEqual(sesion.token_hash, servicio._hash(token))
                with self.assertRaises(PermissionDenied):
                    servicio.operar_capture_grant(sesion_id=sesion.pk, producto='LIBRANZA', grant=grant, accion='finalizar')

    def test_token_reutilizado_invalido_uuid_y_shell_no_enumeran(self):
        self.assertEqual(self.canjear().status_code, 200)
        expected = self.canjear()
        self.assertEqual(expected.status_code, 403)
        absent = reverse('captura:movil', args=['LIBRANZA', uuid4()])
        response = self.post('canjear', {'token': 'invalido'}, base=absent)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), expected.json())
        self.assertEqual(self.shell(self.mobile, absent).status_code, 200)
        self.assertEqual(self.post('canjear', {'token': 'invalido'}).json(), expected.json())
        self.assertEqual(self.mobile.get('/captura-documental/LIBRANZA/no-uuid/movil/', secure=True).status_code, 404)
        self.assertFalse(Captura.objects.exists())

    def test_csrf_canje_y_upload_no_se_eximen(self):
        response = self.post('canjear', {'token': self.token}, HTTP_X_CSRFTOKEN='incorrecto')
        self.assertEqual(response.status_code, 403)
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.estado, 'ABIERTA')
        self.assertEqual(self.canjear().status_code, 200)
        self.assertEqual(self.post('frontal', {'archivo': imagen_documental()}, HTTP_X_CSRFTOKEN='incorrecto').status_code, 403)
        self.assertEqual(self.post('frontal', {'archivo': imagen_documental()}, HTTP_ORIGIN='https://evil.example').status_code, 403)
        self.assertFalse(Captura.objects.exists())
        response = self.mobile.post(self.base + 'frontal/', {'archivo': imagen_documental()}, secure=True,
            HTTP_X_CSRFTOKEN=self.mobile.cookies['csrftoken'].value, HTTP_REFERER='https://testserver' + self.base)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shell(self.mobile)['Referrer-Policy'], 'same-origin')

    def test_prestadores_reutiliza_grant_con_contexto(self):
        solicitud = self.solicitud()
        sesion, token = servicio.crear_sesion(actor=self.usuario, producto='PRESTADORES', solicitud_id=solicitud.pk)
        _, grant = servicio.canjear_capture_grant(sesion_id=sesion.pk, producto='PRESTADORES', token=token, solicitud_id=solicitud.pk)
        params = dict(sesion_id=sesion.pk, producto='PRESTADORES', grant=grant, solicitud_id=solicitud.pk)
        for lado in ('frontal', 'trasera'):
            self.assertEqual(servicio.operar_capture_grant(**params, accion=lado, archivo=imagen_documental())['estado'], 'CANJEADA')
        self.assertEqual(servicio.operar_capture_grant(**params, accion='finalizar')['estado'], 'FINALIZADA')
        self.assertFalse(Credito.objects.exists())

    def test_dueno_inactivo_no_puede_delegar(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=['is_active'])
        self.assertEqual(self.canjear().status_code, 403)

    def test_grant_revalida_rol_propietario_en_cada_operacion(self):
        self.assertEqual(self.canjear().status_code, 200)
        PerfilPagador.objects.create(usuario=self.usuario, empresa=self.empresa)
        self.assertEqual(self.post('frontal', {'archivo': imagen_documental()}).status_code, 403)
        self.assertFalse(Captura.objects.exists())

    def test_finalizacion_incompleta_y_rollback_canje(self):
        with patch.object(Evento.objects, 'create', side_effect=RuntimeError('fallo simulado')):
            with self.assertRaises(RuntimeError):
                servicio.canjear_capture_grant(sesion_id=self.sesion.pk, producto='LIBRANZA', token=self.token)
        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.estado, 'ABIERTA')
        self.assertEqual(self.sesion.capture_grant_hash, '')
        self.canjear()
        self.assertEqual(self.post('finalizar').status_code, 400)
        self.assertEqual(self.mobile.get(self.base + 'estado/', secure=True).status_code, 200)

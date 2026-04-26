from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from django.utils import timezone

from gestion_creditos.models import AsesorComercial, Empresa
from .executive_activation_service import crear_token_ejecutivo, enviar_invitacion_activacion_ejecutivo
from .models import ExecutiveAccessToken, InvestorAccessToken, PerfilPagador, PagadorAccessToken
from .investor_activation_service import enviar_invitacion_inversionista
from .pagador_activation_service import (
    crear_token_pagador,
    crear_token_activacion_pagador,
    enviar_invitacion_activacion_pagador,
    enviar_reset_password_pagador,
)


User = get_user_model()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test',
    PRIMARY_DOMAIN_HOST='aprobado.test',
)
class PagadorActivationFlowTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre='Empresa Test Pagador')
        self.user = User.objects.create_user(
            username='pagador_test',
            email='pagador@test.com',
            password='Temporal123*',
            is_active=True,
        )
        self.perfil = PerfilPagador.objects.create(usuario=self.user, empresa=self.empresa, es_pagador=True)

    def test_envio_invitacion_reutiliza_token_activo(self):
        token_1, public_token_1 = crear_token_activacion_pagador(self.perfil)
        token_2, public_token_2 = crear_token_activacion_pagador(self.perfil)

        token_1.refresh_from_db()
        token_2.refresh_from_db()

        self.assertEqual(token_1.pk, token_2.pk)
        self.assertEqual(public_token_1, public_token_2)
        self.assertIsNone(token_1.invalidated_at)

    def test_envio_email_activa_flujo_para_cuenta_nueva(self):
        self.user.last_login = None
        self.user.save(update_fields=['last_login'])

        enviar_invitacion_activacion_pagador(self.perfil)
        self.user.refresh_from_db()

        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Activa tu acceso aqui:', mail.outbox[0].body)

    def test_enlace_invalido_permite_reenvio(self):
        _, public_token = crear_token_activacion_pagador(self.perfil)
        PagadorAccessToken.objects.filter(usuario=self.user).update(invalidated_at=timezone.now())

        response = self.client.post(
            reverse('pagador:activar_cuenta', kwargs={'token': public_token}),
            data={'action': 'resend_activation'},
            follow=True,
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enviamos un nuevo enlace de activacion')
        self.assertEqual(PagadorAccessToken.objects.filter(usuario=self.user).count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNone(
            PagadorAccessToken.objects.filter(
                usuario=self.user,
                used_at__isnull=True,
                invalidated_at__isnull=True,
            ).first().invalidated_at
        )

    def test_activacion_define_contrasena_y_habilita_usuario(self):
        self.user.last_login = None
        self.user.save(update_fields=['last_login'])
        _, raw_token = crear_token_activacion_pagador(self.perfil)
        self.user.is_active = False
        self.user.set_unusable_password()
        self.user.save(update_fields=['is_active', 'password'])

        response = self.client.post(
            reverse('pagador:activar_cuenta', kwargs={'token': raw_token}),
            data={
                'new_password1': 'ActivaPagador2026!Segura',
                'new_password2': 'ActivaPagador2026!Segura',
            },
            follow=True,
            secure=True,
        )

        self.user.refresh_from_db()
        token = PagadorAccessToken.objects.get(usuario=self.user)

        self.assertEqual(response.status_code, 200)
        if not self.user.is_active:
            context_form = None
            if hasattr(response, 'context') and response.context:
                try:
                    context_form = response.context.get('form')
                except Exception:
                    context_form = None
            errores = context_form.errors.as_json() if context_form is not None else 'sin formulario en contexto'
            self.fail(f"Formulario de activacion no completo el flujo: errores={errores}")
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.check_password('ActivaPagador2026!Segura'))
        self.assertIsNotNone(token.used_at)

    def test_envio_reset_password_para_pagador_existente(self):
        enviar_reset_password_pagador(self.perfil)
        token = PagadorAccessToken.objects.get(usuario=self.user, tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Restablece tu acceso', mail.outbox[0].alternatives[0][0])
        self.assertIsNone(token.used_at)
        self.assertIsNone(token.invalidated_at)

    def test_reset_password_por_vista_actualiza_contrasena(self):
        reset_token, raw_reset = crear_token_pagador(
            self.perfil,
            tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD,
        )
        response = self.client.post(
            reverse('pagador:reset_password_confirm', kwargs={'token': raw_reset}),
            data={
                'new_password1': 'NuevaClavePagador2026!',
                'new_password2': 'NuevaClavePagador2026!',
            },
            follow=True,
            secure=True,
        )

        self.user.refresh_from_db()
        reset_token.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.user.check_password('NuevaClavePagador2026!'))
        self.assertIsNotNone(reset_token.used_at)

    def test_request_reset_por_usuario_envia_mensaje_neutro(self):
        response = self.client.post(
            reverse('pagador:password_reset_request'),
            data={'email': self.user.email},
            follow=True,
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Si encontramos una cuenta de pagador asociada, enviamos un enlace de restablecimiento al correo registrado.',
        )
        self.assertEqual(
            PagadorAccessToken.objects.filter(
                usuario=self.user,
                tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD,
            ).count(),
            1,
        )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test',
    PRIMARY_DOMAIN_HOST='aprobado.test',
)
class InvestorActivationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username='investor@test.com',
            email='investor@test.com',
            is_active=True,
        )
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])

    def test_invitation_deactivates_account_without_password_and_sends_link(self):
        enviar_invitacion_inversionista(self.user)
        self.user.refresh_from_db()
        token = InvestorAccessToken.objects.get(usuario=self.user)

        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/inversionista/activar/', mail.outbox[0].body)
        self.assertIsNone(token.invalidated_at)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test',
    PRIMARY_DOMAIN_HOST='aprobado.test',
)
class ExecutiveActivationFlowTests(TestCase):
    def setUp(self):
        self.asesor = AsesorComercial.objects.create(
            nombre='Ejecutivo Demo',
            cedula='11224455',
            email='ejecutivo@test.com',
            telefono='3001234567',
        )

    def test_envio_invitacion_crea_usuario_sin_password_y_manda_correo(self):
        enviar_invitacion_activacion_ejecutivo(self.asesor)
        self.asesor.refresh_from_db()
        token = ExecutiveAccessToken.objects.get(asesor=self.asesor)

        self.assertIsNotNone(self.asesor.usuario)
        self.assertFalse(self.asesor.usuario.is_active)
        self.assertFalse(self.asesor.usuario.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/asesores/activar/', mail.outbox[0].body)
        self.assertIsNone(token.invalidated_at)

    def test_reenvio_reutiliza_token_activo(self):
        enviar_invitacion_activacion_ejecutivo(self.asesor)
        enviar_invitacion_activacion_ejecutivo(self.asesor)

        self.assertEqual(ExecutiveAccessToken.objects.filter(asesor=self.asesor).count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_activacion_define_password_y_permite_ingreso(self):
        enviar_invitacion_activacion_ejecutivo(self.asesor)
        self.asesor.refresh_from_db()
        _, public_token = crear_token_ejecutivo(self.asesor)

        response = self.client.post(
            reverse('asesores:activar_cuenta', kwargs={'token': public_token}),
            data={
                'new_password1': 'EjecutivoPass2026!Segura',
                'new_password2': 'EjecutivoPass2026!Segura',
            },
            follow=True,
            secure=True,
        )

        self.asesor.refresh_from_db()
        token = ExecutiveAccessToken.objects.get(asesor=self.asesor)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.asesor.usuario.is_active)
        self.assertTrue(self.asesor.usuario.check_password('EjecutivoPass2026!Segura'))
        self.assertIsNotNone(token.used_at)
        self.assertTrue(self.client.login(username=self.asesor.usuario.username, password='EjecutivoPass2026!Segura'))
        dashboard = self.client.get(reverse('asesores:dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Panel del Ejecutivo')

    def test_enlace_invalido_permite_reenvio(self):
        enviar_invitacion_activacion_ejecutivo(self.asesor)
        _, public_token = crear_token_ejecutivo(self.asesor)
        ExecutiveAccessToken.objects.filter(asesor=self.asesor).update(invalidated_at=timezone.now())

        response = self.client.post(
            reverse('asesores:activar_cuenta', kwargs={'token': public_token}),
            data={'action': 'resend_activation'},
            follow=True,
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enviamos un nuevo enlace de activación')
        self.assertEqual(ExecutiveAccessToken.objects.filter(asesor=self.asesor).count(), 2)
        self.assertEqual(len(mail.outbox), 2)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@aprobado.test',
    PRIMARY_DOMAIN_HOST='aprobado.test',
)
class ClosedAuthUiTests(TestCase):
    def setUp(self):
        self.investor = User.objects.create_user(
            username='investor.ui@test.com',
            email='investor.ui@test.com',
            password='InvestorUi2026!',
            is_active=True,
        )

        self.pagador = User.objects.create_user(
            username='pagador.ui@test.com',
            email='pagador.ui@test.com',
            password='PagadorUi2026!',
            is_active=True,
        )

    def test_login_inversionista_no_expone_google(self):
        response = self.client.get(reverse('inversionista:login'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recuperar acceso')
        self.assertNotContains(response, 'Continuar con Google')
        self.assertNotContains(response, 'Crear cuenta')

    def test_login_pagador_no_expone_google(self):
        response = self.client.get(reverse('pagador:login'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recuperar acceso')
        self.assertNotContains(response, 'Continuar con Google')
        self.assertNotContains(response, 'Crear cuenta')

    def test_password_reset_inversionista_usa_ruta_propia(self):
        response = self.client.post(
            reverse('inversionista:password_reset'),
            data={'email': self.investor.email},
            follow=True,
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Si el correo está registrado, recibirás un enlace para restablecer tu contraseña en los próximos minutos.',
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/inversionista/reset/', mail.outbox[0].body)

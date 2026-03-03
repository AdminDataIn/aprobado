from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_creditos.models import Empresa
from .models import PerfilPagador, PagadorAccessToken
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

    def test_envio_invitacion_invalida_tokens_previos(self):
        token_1, _ = crear_token_activacion_pagador(self.perfil)
        token_2, _ = crear_token_activacion_pagador(self.perfil)

        token_1.refresh_from_db()
        token_2.refresh_from_db()

        self.assertIsNotNone(token_1.invalidated_at)
        self.assertIsNone(token_2.invalidated_at)

    def test_envio_email_activa_flujo_para_cuenta_nueva(self):
        self.user.last_login = None
        self.user.save(update_fields=['last_login'])

        enviar_invitacion_activacion_pagador(self.perfil)
        self.user.refresh_from_db()

        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Activa tu acceso aqui:', mail.outbox[0].body)

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
            data={'identificador': self.user.username},
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

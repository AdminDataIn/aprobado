from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


class AccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        if hasattr(request.user, 'perfil_pagador'):
            return '/gestion_creditos/pagador/dashboard/'
        return super().get_login_redirect_url(request)


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """
        Si ya existe un usuario local con el mismo email, conectamos Google a
        ese registro en vez de crear un usuario duplicado.
        """
        if sociallogin.is_existing:
            return

        email = (sociallogin.user.email or '').strip().lower()
        if not email:
            return

        User = get_user_model()
        existing_user = User.objects.filter(email__iexact=email).first()
        if existing_user:
            sociallogin.connect(request, existing_user)

    def save_user(self, request, sociallogin, form=None):
        """
        Guarda el usuario social y, si el flujo viene desde libranza,
        intenta asignarlo al grupo Empleados.
        """
        user = super().save_user(request, sociallogin, form)

        if not sociallogin.is_existing:
            next_url = sociallogin.state.get('next')
            if next_url in {'/gestion_creditos/solicitar/libranza/', '/libranza/solicitar/'}:
                try:
                    empleados_group = Group.objects.get(name='Empleados')
                    user.groups.add(empleados_group)
                except Group.DoesNotExist:
                    pass
        return user

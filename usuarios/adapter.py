
#! Archivo: usuarios/adapter.py cuya finalidad es personalizar el comportamiento de django-allauth
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import Group
from django.dispatch import receiver
from allauth.account.signals import user_signed_up

#? ADAPTER PARA REDIRECCIONAR SEGUN TIPO DE USUARIO
class AccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        # Check if the user has a PerfilPagador
        if hasattr(request.user, 'perfil_pagador'):
            return '/gestion_creditos/pagador/dashboard/'
        
        # Default redirect for other users
        return super().get_login_redirect_url(request)

#? ADAPTER PARA ASIGNAR GRUPO 'EMPLEADOS' A USUARIOS QUE SE REGISTREN DESDE FLUJO DE LIBRANZA
class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        """
        Se llama cuando se guarda una cuenta social para un usuario.
        Aquí es donde interceptamos el registro para añadir al usuario al grupo correcto.
        """
        user = super().save_user(request, sociallogin, form)

        #* Verificamos si el usuario es nuevo y si viene del flujo de libranza
        if not sociallogin.is_existing:
            #* La información del 'next' se guarda en el estado de la sesión de allauth
            next_url = sociallogin.state.get('next')
            if next_url == '/gestion_creditos/solicitar/libranza/':
                try:
                    empleados_group = Group.objects.get(name='Empleados')
                    user.groups.add(empleados_group)
                except Group.DoesNotExist:
                    #* Si el grupo no existe, no hacemos nada, pero sería bueno loggearlo.
                    pass
        return user
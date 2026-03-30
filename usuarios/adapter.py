from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import redirect

from .models import ProductAccessProfile
from .product_flow import ProductFlowConflict, assign_user_flow, get_flow_home_path, get_user_flow


def _target_flow_from_next(next_url):
    if next_url and next_url.startswith('/libranza/'):
        return ProductAccessProfile.ProductFlow.LIBRANZA
    if next_url and next_url.startswith('/emprendimiento/'):
        return ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
    if next_url and next_url.startswith('/marketplace/'):
        return ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
    if next_url and next_url.startswith('/inversionista/'):
        return ProductAccessProfile.ProductFlow.INVERSIONISTA
    return None


class AccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        if hasattr(request.user, 'perfil_pagador'):
            return '/pagador/'
        if hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
            return '/marketplace/panel/'
        flow = get_user_flow(request.user)
        if flow:
            return get_flow_home_path(flow)
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

        target_flow = _target_flow_from_next(sociallogin.state.get('next'))
        User = get_user_model()
        existing_user = User.objects.filter(email__iexact=email).first()
        if existing_user:
            if target_flow:
                try:
                    assign_user_flow(existing_user, target_flow)
                except ProductFlowConflict as exc:
                    current_flow = exc.args[0] if exc.args else get_user_flow(existing_user)
                    messages.error(
                        request,
                        f'Tu cuenta ya pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
                    )
                    raise ImmediateHttpResponse(redirect(get_flow_home_path(current_flow)))
            messages.info(
                request,
                'Ya tenias una cuenta registrada. Google se conecto a ese acceso existente.'
            )
            sociallogin.connect(request, existing_user)

    def save_user(self, request, sociallogin, form=None):
        """
        Guarda el usuario social y, si el flujo viene desde libranza,
        intenta asignarlo al grupo Empleados.
        """
        user = super().save_user(request, sociallogin, form)
        target_flow = _target_flow_from_next(sociallogin.state.get('next'))

        if target_flow:
            try:
                assign_user_flow(user, target_flow)
            except ProductFlowConflict as exc:
                current_flow = exc.args[0] if exc.args else get_user_flow(user)
                messages.error(
                    request,
                    f'Tu cuenta ya pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
                )

        if not sociallogin.is_existing:
            next_url = sociallogin.state.get('next')
            if next_url in {'/gestion_creditos/solicitar/libranza/', '/libranza/solicitar/'}:
                try:
                    empleados_group = Group.objects.get(name='Empleados')
                    user.groups.add(empleados_group)
                except Group.DoesNotExist:
                    pass
        return user

from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from usuarios.models import PerfilEmpresaMarketing, PerfilPagador
from usuarios.product_flow import get_flow_home_path, get_flow_label, get_user_flow


def pagador_required(view_func):
    """
    Decorador que verifica si el usuario logueado tiene un perfil de pagador activo.
    Si no lo tiene, redirige a la pagina de inicio con un mensaje de error.
    Si lo tiene, anade el objeto 'empresa' al request para facil acceso en la vista.
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not getattr(request.user, 'is_authenticated', False):
            return redirect(f"/pagador/login/?next={request.get_full_path()}")

        try:
            perfil_pagador = request.user.perfil_pagador
            request.empresa = perfil_pagador.empresa
        except PerfilPagador.DoesNotExist:
            messages.error(request, "Tu cuenta no tiene permisos de pagador.")
            logout(request)
            return redirect(f"/pagador/login/?next={request.get_full_path()}")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def marketing_required(view_func):
    """
    Decorador que restringe acceso al panel marketplace solo a usuarios
    con perfil activo de empresa_marketing.
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not getattr(request.user, 'is_authenticated', False):
            return redirect('marketplace:admin_login_without_company')

        try:
            perfil_marketing = request.user.perfil_marketing
        except PerfilEmpresaMarketing.DoesNotExist:
            messages.error(request, "No tiene permisos para acceder al panel de marketing.")
            return redirect('marketplace:admin_login_without_company')

        if not perfil_marketing.activo:
            messages.error(request, "Su perfil de marketing esta inactivo.")
            return redirect('marketplace:admin_login_without_company')
        if not getattr(perfil_marketing.empresa, 'permite_marketplace', False):
            messages.error(request, "La empresa asociada no esta habilitada para marketplace.")
            return redirect('marketplace:home')

        request.empresa_marketing = perfil_marketing.empresa
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def marketplace_admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not getattr(request.user, 'is_authenticated', False):
            return redirect('marketplace:admin_login_without_company')

        try:
            perfil_marketing = request.user.perfil_marketing
        except PerfilEmpresaMarketing.DoesNotExist:
            messages.error(request, "No tiene permisos para acceder al panel de marketing.")
            return redirect('marketplace:admin_login_without_company')

        if not perfil_marketing.activo:
            messages.error(request, "Su perfil de marketing esta inactivo.")
            return redirect('marketplace:admin_login_without_company')
        if not getattr(perfil_marketing.empresa, 'permite_marketplace', False):
            messages.error(request, "La empresa asociada no esta habilitada para marketplace.")
            return redirect('marketplace:home')

        request.empresa_marketing = perfil_marketing.empresa
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def marketplace_buyer_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if hasattr(request.user, 'perfil_marketing') and getattr(request.user.perfil_marketing, 'activo', False):
            messages.error(
                request,
                'Tu cuenta pertenece al panel de empresa y no puede usar compras del marketplace.'
            )
            return redirect('marketplace:admin_login_without_company')

        current_flow = get_user_flow(request.user)
        if current_flow and current_flow != 'MARKETPLACE_BUYER':
            messages.error(
                request,
                f"Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede usar compras del marketplace."
            )
            return redirect(get_flow_home_path(current_flow))
        return view_func(request, *args, **kwargs)

    return _wrapped_view

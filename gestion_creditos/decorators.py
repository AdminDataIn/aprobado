from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from usuarios.models import PerfilPagador, PerfilEmpresaMarketing

def pagador_required(view_func):
    """
    Decorador que verifica si el usuario logueado tiene un perfil de pagador activo.
    Si no lo tiene, redirige a la página de inicio con un mensaje de error.
    Si lo tiene, añade el objeto 'empresa' al request para fácil acceso en la vista.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        try:
            perfil_pagador = request.user.perfil_pagador
            request.empresa = perfil_pagador.empresa
        except PerfilPagador.DoesNotExist:
            messages.error(request, "No tiene los permisos necesarios para acceder a esta sección.")
            return redirect('usuarios:libranza_landing')  # Redirige al landing de Libranza
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def marketing_required(view_func):
    """
    Decorador que restringe acceso al panel marketplace solo a usuarios
    con perfil activo de empresa_marketing.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        try:
            perfil_marketing = request.user.perfil_marketing
            if not perfil_marketing.activo:
                messages.error(request, "Su perfil de marketing está inactivo.")
                return redirect('marketplace:login')
            request.empresa_marketing = perfil_marketing.empresa
        except PerfilEmpresaMarketing.DoesNotExist:
            messages.error(request, "No tiene permisos para acceder al panel de marketing.")
            return redirect('marketplace:login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

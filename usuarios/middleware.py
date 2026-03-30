"""
Middleware para contexto de producto y aislamiento de flujos.
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import ProductAccessProfile
from .product_flow import get_flow_home_path, get_flow_label, get_user_flow


class ProductoContextMiddleware:
    """
    Detecta el producto actual por URL y evita que un usuario autenticado
    salte entre Libranza y Emprendimiento cuando ya tiene un flujo bloqueado.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            path = request.path
            full_path = request.get_full_path()
            nuevo_producto = None
            target_login = None

            if any(url_part in path for url_part in ['/libranza/', '/pagador/']):
                nuevo_producto = ProductAccessProfile.ProductFlow.LIBRANZA
                target_login = '/libranza/login/'
            elif any(url_part in path for url_part in ['/emprendimiento/', '/aplicando/']):
                nuevo_producto = ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
                target_login = '/emprendimiento/login/'
            elif path.startswith('/marketplace/'):
                nuevo_producto = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
                target_login = '/marketplace/login/'
            elif path.startswith('/inversionista/'):
                nuevo_producto = ProductAccessProfile.ProductFlow.INVERSIONISTA
                target_login = '/inversionista/login/'

            if nuevo_producto and request.session.get('producto_actual') != nuevo_producto:
                request.session['producto_actual'] = nuevo_producto

            protected_flow = None
            if path.startswith('/libranza/'):
                protected_flow = ProductAccessProfile.ProductFlow.LIBRANZA
            elif path.startswith('/emprendimiento/'):
                protected_flow = ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
            elif path.startswith('/marketplace/'):
                protected_flow = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
            elif path.startswith('/inversionista/'):
                protected_flow = ProductAccessProfile.ProductFlow.INVERSIONISTA

            if path.startswith('/marketplace/panel/') or '/marketplace/empresa/' in path and path.endswith('/login/'):
                if hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
                    return self.get_response(request)
                current_flow = get_user_flow(request.user)
                if current_flow:
                    messages.error(
                        request,
                        'Tu sesion actual no puede usarse en el panel marketplace. Inicia sesion de nuevo con una cuenta administradora.'
                    )
                    logout(request)
                    return redirect('/marketplace/panel/login/')

            if protected_flow:
                current_flow = get_user_flow(request.user)
                if current_flow and current_flow != protected_flow:
                    messages.error(
                        request,
                        f'Tu sesion pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
                    )
                    logout(request)
                    return redirect(f'{target_login}?next={full_path}')

        return self.get_response(request)

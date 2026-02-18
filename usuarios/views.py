from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.contrib.auth import logout
from django.views.generic import TemplateView
from django.urls import reverse


# Create your views here.
def index(request):
    return render(request, 'index.html')

#def aplicar_formulario(request):
#    return render(request, 'emprendimiento/aplicando.html')

@login_required
def aplicar_formulario(request):
    # if not SocialAccount.objects.filter(user=request.user, provider='google').exists():
        # return redirect('/accounts/google/login/?next=/emprendimiento/solicitar/')
    return render(request, 'emprendimiento/aplicando.html')


#? Simulador de EMPRENDIMIENTO
def simulador(request):
    """
    Vista del simulador de crédito de EMPRENDIMIENTO.
    Siempre muestra el simulador de emprendimiento, independiente del grupo del usuario.
    """
    context = {
        'es_empleado': False  # SIEMPRE False porque este es el simulador de EMPRENDIMIENTO
    }
    return render(request, 'emprendimiento/simulacion.html', context)


# def simulador(request):
#     #* Por defecto el usuario no es una empresa
#     es_empleado = False

#     #* Verificamos si el usuario está autenticado y pertenece al grupo "Empresas"
#     if request.user.is_authenticated and request.user.groups.filter(name='Empresas').exists():
#         es_empleado = True

#     #* Pasamos la variable 'es_empleadoado' al contexto del template
#     context = {
#         'es_empleado': es_empleado
#     }
#     return render(request, 'emprendimiento/simulacion.html', context)


class EmpresaLoginView(LoginView):
    template_name = 'account/login_empresa.html'
    redirect_authenticated_user = False  # ⭐ Cambiado a False para permitir acceso a usuarios autenticados

    def get(self, request, *args, **kwargs):
        # Si el usuario ya está autenticado, verificamos si tiene perfil de pagador
        if request.user.is_authenticated:
            if hasattr(request.user, 'perfil_pagador'):
                # Si ya tiene perfil de pagador, lo redirigimos al dashboard
                return redirect(reverse('pagador:dashboard'))
            else:
                # Si está autenticado pero NO es pagador, mostramos mensaje y redirigimos
                messages.warning(request, 'Su cuenta actual no tiene permisos de pagador. Si necesita acceso como pagador, contacte al administrador.')
                return redirect(reverse('libranza:landing'))

        # Marcamos la sesión para identificar que el flujo de login empezó aquí
        request.session['login_flow'] = 'empresa'
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        # Verificamos si el usuario tiene un perfil de pagador asociado
        if hasattr(user, 'perfil_pagador'):
            return super().form_valid(form)
        else:
            # Si no tiene perfil de pagador, rechazamos el login
            logout(self.request)
            messages.error(self.request, 'Este usuario no tiene permisos para acceder como pagador.')
            return self.form_invalid(form)

    def get_success_url(self):
        # Redirige al dashboard de pagador (nueva estructura)
        return reverse('pagador:dashboard')


class MarketingLoginView(LoginView):
    template_name = 'account/login_marketing.html'
    redirect_authenticated_user = False

    def get(self, request, *args, **kwargs):
        # Si ya está autenticado y tiene perfil marketing activo, va directo al panel.
        if request.user.is_authenticated:
            if hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
                return redirect(reverse('marketplace:panel'))
            messages.warning(request, 'Su cuenta actual no tiene acceso activo al panel marketplace.')
            return redirect(reverse('home'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if hasattr(user, 'perfil_marketing') and user.perfil_marketing.activo:
            return super().form_valid(form)
        logout(self.request)
        messages.error(self.request, 'Este usuario no tiene permisos para ingresar al panel marketplace.')
        return self.form_invalid(form)

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return reverse('marketplace:panel')


# Vista para la Landing Page de Crédito de Libranza
def libranza_landing(request):
    """
    Vista para mostrar la landing page del producto de Crédito de Libranza.

    Esta página es pública y no requiere autenticación.
    Muestra información completa sobre el producto incluyendo:
    - Características y beneficios
    - Simulador (enlace al simulador completo)
    - Proceso paso a paso
    - Requisitos y documentación
    - Preguntas frecuentes
    - Llamados a la acción para solicitar el crédito

    Returns:
        Renderiza 'libranza/libranza_landing.html'
    """
    context = {
        # Se puede agregar contexto adicional si es necesario en el futuro
        # Por ejemplo: tasas, montos, convenios, etc.
    }
    return render(request, 'libranza/libranza_landing.html', context)


# Vista para el Simulador de Crédito de Libranza
def simulador_libranza(request):
    """
    Vista para mostrar el simulador exclusivo de Crédito de Libranza.

    Esta página es pública y no requiere autenticación.
    Permite calcular:
    - Monto solicitado: $500.000 - $2.000.000
    - Plazo: 1 - 6 meses
    - Comisión: 10% + IVA (19%)
    - Afianzadora: 4% + IVA (próximamente)
    - Cuota mensual
    - Total a pagar

    Returns:
        Renderiza 'libranza/simulacion_libranza.html'
    """
    context = {
        # Se puede agregar contexto adicional si es necesario
        # Por ejemplo: tasas dinámicas, rangos personalizados, etc.
    }
    return render(request, 'libranza/simulacion_libranza.html', context)


# Vista para el login de Libranza
class LoginLibranzaView(TemplateView):
    """
    Vista que muestra la página de login específica para Libranza.
    Usa el template libranza/base_libranza.html con navbar y footer de Libranza.
    """
    template_name = 'libranza/login.html'


# Vista para el login de Emprendimiento
class LoginEmprendimientoView(TemplateView):
    """
    Vista que muestra la página de login específica para Emprendimiento.
    Usa el template emprendimiento/base_emprendimiento.html con navbar y footer de Emprendimiento.
    """
    template_name = 'emprendimiento/login.html'


class CustomLogoutView(LogoutView):
    """
    Vista personalizada de logout que redirige según el producto del usuario.

    Utiliza el middleware ProductoContextMiddleware que detecta automáticamente
    el producto (LIBRANZA o EMPRENDIMIENTO) y lo guarda en la sesión.

    Esto evita consultas a la base de datos en cada logout.

    - Si producto_actual = 'LIBRANZA' → redirige a landing de Libranza
    - En otros casos → redirige a inicio de Emprendimiento
    """
    http_method_names = ['post', 'options']  # Solo permite POST

    def post(self, request, *args, **kwargs):
        # Obtener el producto actual desde la sesión (detectado por middleware)
        producto_actual = request.session.get('producto_actual', 'EMPRENDIMIENTO')

        # Determinar la URL de redirección ANTES de hacer logout
        if producto_actual == 'LIBRANZA':
            # Redirigir a landing de libranza
            next_page = reverse('libranza:landing')
        else:
            # Redirigir a landing de emprendimiento (home)
            next_page = reverse('home')

        # Realizar el logout
        logout(request)

        # Redirigir a la página correspondiente
        return redirect(next_page)

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.contrib.auth import logout


# Create your views here.
def index(request):
    return render(request, 'index.html')

#def aplicar_formulario(request):
#    return render(request, 'aplicando.html')

@login_required
def aplicar_formulario(request):
    # if not SocialAccount.objects.filter(user=request.user, provider='google').exists():
        # return redirect('/accounts/google/login/?next=/usuarios/aplicar/')
    return render(request, 'aplicando.html')


#? Adaptamos el simulador al grupo de empresas
def simulador(request):
    es_empleado = False
    
    # --- INICIO: Código de depuración ---
    print(f"Usuario actual: {request.user}")
    if request.user.is_authenticated:
        grupos = list(request.user.groups.all().values_list('name', flat=True))
        print(f"El usuario pertenece a los siguientes grupos: {grupos}")
        if 'Empleados' in grupos:
            es_empleado = True
    print(f"¿Se considera empleado para la plantilla?: {es_empleado}")
    # --- FIN: Código de depuración ---

    context = {
        'es_empleado': es_empleado
    }
    return render(request, 'simulacion.html', context)


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
#     return render(request, 'simulacion.html', context)


class EmpresaLoginView(LoginView):
    template_name = 'account/login_empresa.html'
    redirect_authenticated_user = True

    def get(self, request, *args, **kwargs):
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
        # Redirige al dashboard principal. Se puede cambiar a un dashboard de empresa si se crea.
        return '/gestion-creditos/pagador/dashboard/'


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
        Renderiza 'libranza_landing.html'
    """
    context = {
        # Se puede agregar contexto adicional si es necesario en el futuro
        # Por ejemplo: tasas, montos, convenios, etc.
    }
    return render(request, 'libranza_landing.html', context)


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
        Renderiza 'simulacion_libranza.html'
    """
    context = {
        # Se puede agregar contexto adicional si es necesario
        # Por ejemplo: tasas dinámicas, rangos personalizados, etc.
    }
    return render(request, 'simulacion_libranza.html', context)

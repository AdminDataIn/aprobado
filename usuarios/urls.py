from django.urls import path
from . import views

app_name = 'usuarios'

urlpatterns = [
    path('inicio/', views.index, name='inicio'),
    path('aplicar/', views.aplicar_formulario, name='aplicar'),
    path('simulador/', views.simulador, name='simulador'),
    path('empresas/login/', views.EmpresaLoginView.as_view(), name='empresa_login'),
    path('logout/', views.CustomLogoutView.as_view(), name='custom_logout'),  # Vista personalizada de logout
    # URLs de Libranza
    path('personas/credito-libranza/', views.libranza_landing, name='libranza_landing'),
    path('personas/credito-libranza/simulador/', views.simulador_libranza, name='simulador_libranza'),
    # Logins independientes por producto
    path('login/libranza/', views.LoginLibranzaView.as_view(), name='login_libranza'),
    path('login/emprendimiento/', views.LoginEmprendimientoView.as_view(), name='login_emprendimiento'),
]
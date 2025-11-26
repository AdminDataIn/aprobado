from django.urls import path
from . import views

app_name = 'usuarios'

urlpatterns = [
    path('inicio/', views.index, name='inicio'),
    path('aplicar/', views.aplicar_formulario, name='aplicar'),
    path('simulador/', views.simulador, name='simulador'),
    path('empresas/login/', views.EmpresaLoginView.as_view(), name='empresa_login'),
    # URLs de Libranza
    path('personas/credito-libranza/', views.libranza_landing, name='libranza_landing'),
    path('personas/credito-libranza/simulador/', views.simulador_libranza, name='simulador_libranza'),
]
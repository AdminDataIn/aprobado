from django.urls import path
from . import views

urlpatterns = [
    path('inicio/', views.index, name='inicio'),
    path('aplicar/', views.aplicar_formulario, name='aplicar'),
    path('simulador/', views.simulador, name='simulador'),
]
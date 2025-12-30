"""
URLs de SOLICITUDES DE CRÉDITO
Estas rutas se incluirán en emprendimiento y libranza

IMPORTANTE: Este archivo es auxiliar y sus vistas serán incluidas
en urls_emprendimiento.py y urls_libranza.py respectivamente
"""
from django.urls import path
from . import views

# NO tiene app_name porque se incluirá en otros namespaces

urlpatterns_emprendimiento = [
    path('solicitar/', views.solicitud_credito_emprendimiento_view, name='solicitar'),
]

urlpatterns_libranza = [
    path('solicitar/', views.solicitud_credito_libranza_view, name='solicitar'),
]
"""
URLs del producto LIBRANZA
Prefijo: /libranza/

Estructura IDÉNTICA a Emprendimiento (simetría)
"""
from django.urls import path
from . import views
from gestion_creditos import views as gestion_views
from usuariocreditos import views as credito_views

app_name = 'libranza'

urlpatterns = [
    # ========================================
    # SECCIÓN PÚBLICA
    # ========================================
    path('', views.libranza_landing, name='landing'),
    path('simulador/', views.simulador_libranza, name='simulador'),

    # ========================================
    # AUTENTICACIÓN
    # ========================================
    path('login/', views.LoginLibranzaView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    # path('registro/', views.RegistroLibranzaView.as_view(), name='registro'),  # Futuro

    # ========================================
    # SOLICITUD DE CRÉDITO
    # ========================================
    path('solicitar/', gestion_views.solicitud_credito_libranza_view, name='solicitar'),

    # ========================================
    # DASHBOARD DEL CLIENTE (MI CRÉDITO)
    # ========================================
    path('mi-credito/', credito_views.dashboard_libranza_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', credito_views.dashboard_libranza_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', credito_views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', credito_views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),
]
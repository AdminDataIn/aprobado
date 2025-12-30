"""
URLs de PAGADOR (Pagaduria de la empresa)
Prefijo: /pagador/

Rol: Usuarios con PerfilPagador que gestionan pagos de libranza
"""
from django.urls import path
from usuarios import views as usuarios_views
from . import views

app_name = 'pagador'

urlpatterns = [
    # ========================================
    # AUTENTICACIÓN
    # ========================================
    path('login/', usuarios_views.EmpresaLoginView.as_view(), name='login'),

    # ========================================
    # DASHBOARD
    # ========================================
    path('', views.pagador_dashboard_view, name='dashboard'),
    path('credito/<int:credito_id>/', views.pagador_detalle_credito_view, name='credito_detalle'),

    # ========================================
    # PROCESAMIENTO DE PAGOS
    # ========================================
    path('pagar/<int:credito_id>/', views.iniciar_pago_view, name='pagar_individual'),
    path('pagar/callback/', views.procesar_pago_callback_view, name='pago_callback'),
    path('pagos-masivos/', views.pagador_procesar_pagos_view, name='pagos_masivos'),
]
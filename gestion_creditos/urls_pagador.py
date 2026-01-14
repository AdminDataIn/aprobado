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
    path('credito/<int:credito_id>/decision/', views.pagador_decidir_solicitud_view, name='decidir_solicitud'),

    # ========================================
    # PROCESAMIENTO DE PAGOS - SIMULACIÓN
    # ========================================
    path('pagar/<int:credito_id>/', views.iniciar_pago_view, name='pagar_individual'),
    path('pagar/callback/', views.procesar_pago_callback_view, name='pago_callback'),
    path('pagos-masivos/', views.pagador_procesar_pagos_view, name='pagos_masivos'),

    # ========================================
    # PROCESAMIENTO DE PAGOS - WOMPI (REAL)
    # ========================================
    path('pago/wompi/<int:credito_id>/', views.iniciar_pago_wompi_view, name='pagar_wompi'),
    path('pago/wompi/procesar/', views.procesar_pago_wompi_view, name='procesar_pago_wompi'),
    path('pago/wompi/callback/', views.pago_wompi_callback_view, name='pago_wompi_callback'),

    # ========================================
    # PAGO MASIVO CON WOMPI
    # ========================================
    path('pago-masivo-wompi/', views.iniciar_pago_masivo_wompi_view, name='pagar_masivo_wompi'),

    # ========================================
    # UTILIDADES Y REPORTES
    # ========================================
    path('descargar-csv-cuotas/', views.descargar_csv_cuotas_pendientes_view, name='descargar_csv_cuotas'),

    # API endpoints
    path('api/bancos-pse/', views.get_pse_banks_view, name='api_bancos_pse'),
]

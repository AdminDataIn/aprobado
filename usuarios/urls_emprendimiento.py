"""
URLs del producto EMPRENDIMIENTO
Prefijo: /emprendimiento/

Incluye:
- Landing y páginas públicas
- Autenticación
- Solicitud de crédito
- Dashboard del cliente
"""
from django.urls import path
from . import views
from gestion_creditos import views as gestion_views
from usuariocreditos import views as credito_views

app_name = 'emprendimiento'

urlpatterns = [
    #? ========================================
    #? SECCIÓN PÚBLICA
    #? ========================================
    path('', views.index, name='landing'),
    path('simulador/', views.simulador, name='simulador'),

    #? ========================================
    #? AUTENTICACIÓN
    #? ========================================
    path('login/', views.LoginEmprendimientoView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    # path('registro/', views.RegistroEmprendimientoView.as_view(), name='registro'),  # Futuro

    #? ========================================
    #? SOLICITUD DE CRÉDITO
    #? ========================================
    path('solicitar/', gestion_views.solicitud_credito_emprendimiento_view, name='solicitar'),

    #? ========================================
    #? DASHBOARD DEL CLIENTE (MI CRÉDITO)
    #? ========================================
    path('mi-credito/', credito_views.dashboard_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', credito_views.dashboard_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', credito_views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', credito_views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),

    #? ========================================
    #? ABONOS Y REESTRUCTURACIONES
    #? ========================================
    path('mi-credito/<int:credito_id>/calcular-pago-total/', gestion_views.calcular_pago_total_view, name='calcular_pago_total'),
    path('mi-credito/<int:credito_id>/analizar-abono/', gestion_views.analizar_abono_credito_view, name='analizar_abono'),
    path('mi-credito/<int:credito_id>/confirmar-abono/', gestion_views.confirmar_abono_credito_view, name='confirmar_abono'),
    path('mi-credito/<int:credito_id>/historial-abonos/', gestion_views.historial_reestructuraciones_view, name='historial_abonos'),

    #? ========================================
    #? PAGOS WOMPI - CLIENTE
    #? ========================================
    path('mi-credito/<int:credito_id>/pago/wompi/', gestion_views.iniciar_pago_wompi_emprendimiento_view, name='pagar_wompi'),
    path('mi-credito/pago/wompi/procesar/', gestion_views.procesar_pago_wompi_emprendimiento_view, name='procesar_pago_wompi'),
    path('mi-credito/pago/wompi/callback/', gestion_views.pago_wompi_emprendimiento_callback_view, name='pago_wompi_callback'),
]

"""
URLs de BILLETERA DIGITAL (Ahorro)
Prefijo: /billetera/

Sistema de ahorro separado del flujo de créditos
"""
from django.urls import path
from . import views

app_name = 'billetera'

urlpatterns = [
    # ========================================
    # SECCIÓN USUARIO (CLIENTE)
    # ========================================
    path('', views.billetera_digital_view, name='dashboard'),
    path('consignar/', views.consignacion_offline_view, name='consignar'),

    # ========================================
    # SECCIÓN GESTIÓN (ANALISTAS)
    # ========================================
    path('gestion/', views.admin_billetera_dashboard_view, name='gestion_dashboard'),
    path('gestion/aprobar/<int:movimiento_id>/', views.aprobar_consignacion_view, name='gestion_aprobar'),
    path('gestion/rechazar/<int:movimiento_id>/', views.rechazar_consignacion_view, name='gestion_rechazar'),
    path('gestion/abono-manual/', views.cargar_abono_manual_view, name='gestion_abono_manual'),
]

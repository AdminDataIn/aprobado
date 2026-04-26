from django.urls import path

from .. import views
from usuarios import views as usuarios_views

app_name = 'gestion_creditos'

urlpatterns = [
    path('solicitar/libranza/', views.solicitud_credito_libranza_view, name='solicitud_libranza'),
    path('solicitar/emprendimiento/', views.solicitud_credito_emprendimiento_view, name='solicitud_emprendimiento'),
    path('admin/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
    path('admin/asesores/', views.admin_asesores_dashboard_view, name='admin_asesores'),
    path('admin/solicitudes/', views.admin_solicitudes_view, name='admin_solicitudes'),
    path('admin/adelantos-nomina/', views.admin_adelantos_nomina_view, name='admin_adelantos_nomina'),
    path('admin/creditos/', views.admin_creditos_activos_view, name='admin_creditos_activos'),
    path('admin/cartera/', views.admin_cartera_view, name='admin_cartera'),
    path('admin/detalle_credito/<int:credito_id>/', views.detalle_credito_view, name='admin_detalle_credito'),
    path('admin/procesar-solicitud/<int:credito_id>/', views.procesar_solicitud_view, name='procesar_solicitud'),
    path('admin/credito/<int:credito_id>/confirmar-desembolso/', views.confirmar_desembolso_view, name='confirmar_desembolso'),
    path('admin/agregar-pago/<int:credito_id>/', views.agregar_pago_manual_view, name='agregar_pago_manual'),
    path('admin/descargar-documentos/<int:credito_id>/', views.descargar_documentos_view, name='descargar_documentos'),
    path('pagador/dashboard/', views.pagador_dashboard_view, name='pagador_dashboard'),
    path('pagador/adelantos/', views.pagador_adelantos_dashboard_view, name='pagador_adelantos_dashboard'),
    path('pagador/credito/<int:credito_id>/', views.pagador_detalle_credito_view, name='pagador_detalle_credito'),
    path('pagador/procesar-pagos/', views.pagador_procesar_pagos_view, name='pagador_procesar_pagos'),
    path('asesores/login/', usuarios_views.AsesorLoginView.as_view(), name='asesor_login'),
    path('asesores/activar/<str:token>/', usuarios_views.executive_activate_account_view, name='asesor_activar_cuenta'),
    path('asesores/panel/', views.asesor_dashboard_view, name='asesor_dashboard'),
    path('pago/iniciar/<int:credito_id>/', views.iniciar_pago_view, name='iniciar_pago'),
    path('pago/callback/', views.procesar_pago_callback_view, name='pago_callback'),
    path('billetera/', views.billetera_digital_view, name='billetera_digital'),
    path('billetera/consignacion-offline/', views.consignacion_offline_view, name='consignacion_offline'),
    path('billetera/notificaciones/marcar-leidas/', views.marcar_notificaciones_leidas_view, name='billetera_marcar_notificaciones_leidas'),
    path('admin/billetera/', views.admin_billetera_dashboard_view, name='admin_billetera_dashboard'),
    path('admin/billetera/aprobar/<int:movimiento_id>/', views.aprobar_consignacion_view, name='aprobar_consignacion'),
    path('admin/billetera/rechazar/<int:movimiento_id>/', views.rechazar_consignacion_view, name='rechazar_consignacion'),
    path('admin/billetera/abono-manual/', views.cargar_abono_manual_view, name='cargar_abono_manual'),
]

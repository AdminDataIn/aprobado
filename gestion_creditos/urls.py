from django.urls import path
from . import views

app_name = 'gestion_creditos'

urlpatterns = [
    path('solicitar/libranza/', views.solicitud_credito_libranza_view, name='solicitud_libranza'),
    path('solicitar/emprendimiento/', views.solicitud_credito_emprendimiento_view, name='solicitud_emprendimiento'),
    path('webhook/firma/<uuid:numero_credito>/', views.webhook_firma_documento, name='webhook_firma_documento'), # DESCOMENTAR AL IMPLEMENTAR LA INTEGRACION
    
    #! URLs Dashboard Administrativo (APROBADOR DE CREDITOS)
    path('admin/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
    path('admin/solicitudes/', views.admin_solicitudes_view, name='admin_solicitudes'),
    path('admin/creditos/', views.admin_creditos_activos_view, name='admin_creditos_activos'),
    path('admin/credito/<int:credito_id>/', views.detalle_credito_view, name='admin_detalle_credito'),
    path('admin/procesar-solicitud/<int:credito_id>/', views.procesar_solicitud_view, name='procesar_solicitud'),
    path('admin/cambiar-estado/<int:credito_id>/', views.cambiar_estado_credito_view, name='cambiar_estado_credito'),
    path('admin/agregar-pago/<int:credito_id>/', views.agregar_pago_manual_view, name='agregar_pago_manual'),
    path('admin/descargar-documentos/<int:credito_id>/', views.descargar_documentos_view, name='descargar_documentos'),

    #! URLs Dashboard Pagador
    path('pagador/dashboard/', views.pagador_dashboard_view, name='pagador_dashboard'),
    path('pagador/credito/<int:credito_id>/', views.pagador_detalle_credito_view, name='pagador_detalle_credito'),
    path('pagador/procesar-pagos/', views.pagador_procesar_pagos_view, name='pagador_procesar_pagos'),

    #! URLs Flujo de Pago Individual
    path('pago/iniciar/<int:credito_id>/', views.iniciar_pago_view, name='iniciar_pago'),
    path('pago/callback/', views.procesar_pago_callback_view, name='pago_callback'),
]
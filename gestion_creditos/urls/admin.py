"""
URLs de GESTIÓN (Analistas de Crédito)
Prefijo: /gestion/

Rol: Staff members que aprueban/rechazan créditos
"""
from django.urls import path
from django.views.generic import RedirectView
from .. import views

app_name = 'gestion'

urlpatterns = [
    # ========================================
    # DASHBOARDS ADMINISTRATIVOS
    # ========================================
    path('', views.admin_dashboard_view, name='dashboard'),
    path(
        'obligaciones-pendientes/',
        views.admin_obligaciones_pendientes_view,
        name='obligaciones_pendientes',
    ),
    path('exportar-reporte/', views.admin_dashboard_export_view, name='dashboard_export'),
    path('pagos-breb/', views.admin_pagos_breb_view, name='pagos_breb'),
    path('pagos-breb/<int:pago_id>/decidir/', views.admin_decidir_pago_breb_view, name='pago_breb_decidir'),
    path('pagos-breb/<int:pago_id>/comprobante/', views.comprobante_pago_breb_view, name='pago_breb_comprobante'),
    path('ejecutivos/', views.admin_asesores_dashboard_view, name='ejecutivos'),
    path('asesores/', RedirectView.as_view(pattern_name='gestion:ejecutivos', permanent=False), name='asesores'),
    path('solicitudes/', views.admin_solicitudes_view, name='solicitudes'),
    path('adelantos-nomina/', views.admin_adelantos_nomina_view, name='adelantos_nomina'),
    path('creditos/', views.admin_creditos_activos_view, name='creditos_activos'),
    path('cartera/', views.admin_cartera_view, name='cartera_mora'),
    path('libranza/casos-especiales/simular/', views.admin_libranza_special_case_simulator_view, name='libranza_special_case_simulator'),
    path('libranza/casos-especiales/<int:audit_id>/originar/', views.admin_libranza_special_case_originate_view, name='libranza_special_case_originate'),

    # ========================================
    # DETALLE Y GESTIÓN DE CRÉDITOS
    # ========================================
    path('credito/<int:credito_id>/', views.detalle_credito_view, name='credito_detalle'),
    path('credito/<int:credito_id>/aprobar/', views.procesar_solicitud_view, name='credito_aprobar'),
    path('credito/<int:credito_id>/rechazar/', views.procesar_solicitud_view, name='credito_rechazar'),
    path('credito/<int:credito_id>/desembolsar/', views.confirmar_desembolso_view, name='credito_desembolsar'),
    path('credito/<int:credito_id>/agregar-pago/', views.agregar_pago_manual_view, name='credito_agregar_pago'),
    path('pago/<int:pago_id>/comprobante/', views.comprobante_pago_manual_view, name='pago_comprobante'),
    path('pago/<int:pago_id>/comprobante/actualizar/', views.actualizar_comprobante_pago_manual_view, name='pago_comprobante_actualizar'),
    path('pago/<int:pago_id>/reconciliar-redondeo/', views.reconciliar_pago_manual_view, name='pago_reconciliar_redondeo'),
    path('credito/<int:credito_id>/saldar/', views.saldar_credito_formalmente_view, name='credito_saldar'),
    path('credito/<int:credito_id>/documentos/', views.descargar_documentos_view, name='credito_documentos'),
    path('credito/<int:credito_id>/documentacion/', views.documentacion_credito_view, name='credito_documentacion'),
    path('documento/preview/', views.documento_preview_view, name='documento_preview'),

    # Desarrollo (simulación)
]

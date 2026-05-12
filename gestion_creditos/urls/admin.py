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
    path('exportar-reporte/', views.admin_dashboard_export_view, name='dashboard_export'),
    path('ejecutivos/', views.admin_asesores_dashboard_view, name='ejecutivos'),
    path('asesores/', RedirectView.as_view(pattern_name='gestion:ejecutivos', permanent=False), name='asesores'),
    path('solicitudes/', views.admin_solicitudes_view, name='solicitudes'),
    path('adelantos-nomina/', views.admin_adelantos_nomina_view, name='adelantos_nomina'),
    path('creditos/', views.admin_creditos_activos_view, name='creditos_activos'),
    path('cartera/', views.admin_cartera_view, name='cartera_mora'),

    # ========================================
    # DETALLE Y GESTIÓN DE CRÉDITOS
    # ========================================
    path('credito/<int:credito_id>/', views.detalle_credito_view, name='credito_detalle'),
    path('credito/<int:credito_id>/aprobar/', views.procesar_solicitud_view, name='credito_aprobar'),
    path('credito/<int:credito_id>/rechazar/', views.procesar_solicitud_view, name='credito_rechazar'),
    path('credito/<int:credito_id>/desembolsar/', views.confirmar_desembolso_view, name='credito_desembolsar'),
    path('credito/<int:credito_id>/agregar-pago/', views.agregar_pago_manual_view, name='credito_agregar_pago'),
    path('credito/<int:credito_id>/saldar/', views.saldar_credito_formalmente_view, name='credito_saldar'),
    path('credito/<int:credito_id>/documentos/', views.descargar_documentos_view, name='credito_documentos'),
    path('credito/<int:credito_id>/documentacion/', views.documentacion_credito_view, name='credito_documentacion'),
    path('documento/preview/', views.documento_preview_view, name='documento_preview'),

    # Desarrollo (simulación)
]

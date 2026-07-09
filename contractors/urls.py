from django.urls import path

from contractors import views, views_admin


app_name = 'contractors'

urlpatterns = [
    path('', views.inicio_prestadores_view, name='inicio'),
    path('solicitar/', views.solicitar_prestador_view, name='solicitar'),
    path('solicitud/<int:solicitud_id>/documentos/', views.documentos_prestador_view, name='documentos'),
    path(
        'solicitud/<int:solicitud_id>/documentos/<int:documento_id>/descargar/',
        views.descargar_documento_prestador_view,
        name='descargar_documento',
    ),
    path('simular/', views.simular_prestador_view, name='simular'),
    path('mi-credito/', views.mi_credito_prestador_view, name='mi_credito'),
    path('gestion/prestadores/', views_admin.bandeja_prestadores_view, name='admin_bandeja'),
    path('gestion/prestadores/<int:solicitud_id>/', views_admin.detalle_prestador_view, name='admin_detalle'),
    path(
        'gestion/prestadores/documentos/<int:documento_id>/descargar/',
        views_admin.descargar_documento_prestador_staff_view,
        name='admin_descargar_documento',
    ),
]

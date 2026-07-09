from django.urls import path

from contractors import views


app_name = 'contractors'

urlpatterns = [
    path('', views.inicio_prestadores_view, name='inicio'),
    path('solicitar/', views.solicitar_prestador_view, name='solicitar'),
    path('solicitud/<int:solicitud_id>/documentos/', views.documentos_prestador_view, name='documentos'),
    path('simular/', views.simular_prestador_view, name='simular'),
    path('mi-credito/', views.mi_credito_prestador_view, name='mi_credito'),
]

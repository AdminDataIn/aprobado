from django.urls import path
from gestion_creditos.views.documentos_privados import descargar_documento

app_name = 'documentos'
urlpatterns = [
    path('<slug:recurso>/<int:objeto_id>/<slug:tipo>/', descargar_documento, name='descargar'),
]

from django.urls import path
from gestion_creditos.views import captura_documental as views

app_name = 'captura'
urlpatterns = [
    path('archivo/<uuid:captura_id>/', views.descargar_captura, name='descargar'),
    path('credito/<int:credito_id>/<str:lado>/', views.descargar_cedula_credito, name='cedula_credito'),
    path('<str:producto>/crear/', views.crear, name='crear'),
    path('<str:producto>/<uuid:sesion_id>/', views.continuar, name='continuar'),
    path('<str:producto>/<uuid:sesion_id>/estado/', views.estado, name='estado'),
    path('<str:producto>/<uuid:sesion_id>/<str:accion>/', views.operar, name='operar'),
]

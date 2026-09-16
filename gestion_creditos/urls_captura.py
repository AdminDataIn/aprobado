from django.urls import path
from gestion_creditos.views import captura_documental as views

app_name = 'captura'
urlpatterns = [
    path('archivo/<uuid:captura_id>/', views.descargar_captura, name='descargar'),
    path('credito/<int:credito_id>/<str:lado>/', views.descargar_cedula_credito, name='cedula_credito'),
    path('<str:producto>/crear/', views.crear, name='crear'),
    path('<str:producto>/<uuid:sesion_id>/movil/', views.movil, name='movil'),
    path('<str:producto>/<uuid:sesion_id>/movil/canjear/', views.canjear_movil, name='canjear_movil'),
    path('<str:producto>/<uuid:sesion_id>/movil/estado/', views.estado_movil, name='estado_movil'),
    path('<str:producto>/<uuid:sesion_id>/movil/frontal/', views.operar_movil, {'accion': 'frontal'}, name='frontal_movil'),
    path('<str:producto>/<uuid:sesion_id>/movil/trasera/', views.operar_movil, {'accion': 'trasera'}, name='trasera_movil'),
    path('<str:producto>/<uuid:sesion_id>/movil/finalizar/', views.operar_movil, {'accion': 'finalizar'}, name='finalizar_movil'),
    path('<str:producto>/<uuid:sesion_id>/', views.continuar, name='continuar'),
    path('<str:producto>/<uuid:sesion_id>/estado/', views.estado, name='estado'),
    path('<str:producto>/<uuid:sesion_id>/<str:accion>/', views.operar, name='operar'),
]

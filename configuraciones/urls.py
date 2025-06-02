from django.urls import path
from . import views

urlpatterns = [
    path('recibir_data/', views.recibir_data, name='recibir_data'),
    path('obtener_estimacion/', views.obtener_estimacion, name='obtener_estimacion'),
]
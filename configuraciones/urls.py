from django.urls import path
from . import views

urlpatterns = [
    path('recibir_data/', views.recibir_data, name='recibir_data'),
]
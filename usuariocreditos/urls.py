from django.urls import path
from . import views

app_name = 'usuariocreditos'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard_view'),
    path('dashboard/<int:credito_id>/', views.dashboard_view, name='dashboard_credito'),
    path('billetera/', views.billetera_digital, name='billetera_digital'),

    #! Descargar extracto de los pagos realizados
    path('descargar-extracto/<int:credito_id>/', views.descargar_extracto, name='descargar_extracto'),
    # Otras URLs se reactivarán después
]
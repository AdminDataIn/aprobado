from django.urls import path
from . import views

app_name = 'usuariocreditos'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/<int:credito_id>/', views.dashboard_view, name='dashboard_credito'),
    # Otras URLs se reactivarán después
]
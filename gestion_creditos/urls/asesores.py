from django.urls import path

from .. import views
from usuarios import views as usuarios_views

app_name = 'asesores'

urlpatterns = [
    path('login/', usuarios_views.AsesorLoginView.as_view(), name='login'),
    path('activar/<str:token>/', usuarios_views.executive_activate_account_view, name='activar_cuenta'),
    path('panel/', views.asesor_dashboard_view, name='dashboard'),
]

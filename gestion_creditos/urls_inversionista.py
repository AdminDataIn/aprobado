from django.urls import path

from . import views_inversionista
from usuarios import views as usuarios_views


app_name = 'inversionista'

urlpatterns = [
    path('login/', usuarios_views.LoginInversionistaView.as_view(), name='login'),
    path('activar/<str:token>/', usuarios_views.investor_activate_account_view, name='activar_cuenta'),
    path('logout/', usuarios_views.CustomLogoutView.as_view(), name='logout'),
    path('', views_inversionista.investor_dashboard_view, name='dashboard'),
]


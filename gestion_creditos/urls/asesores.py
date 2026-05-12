from django.urls import path
from django.views.generic import RedirectView

app_name = 'asesores'

urlpatterns = [
    path('login/', RedirectView.as_view(pattern_name='ejecutivos:login', permanent=False), name='login'),
    path('activar/<str:token>/', RedirectView.as_view(pattern_name='ejecutivos:activar_cuenta', permanent=False), name='activar_cuenta'),
    path('panel/', RedirectView.as_view(pattern_name='ejecutivos:dashboard', permanent=False), name='dashboard'),
]

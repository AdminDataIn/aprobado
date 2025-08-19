from django.urls import path #type: ignore
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/<int:credito_id>/', views.dashboard_view, name='dashboard_credito'),
    path('cambiar-credito/<int:credito_id>/', views.cambiar_credito, name='cambiar_credito'),
]
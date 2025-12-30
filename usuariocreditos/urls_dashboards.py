"""
URLs de DASHBOARDS DE CLIENTES (MI CRÉDITO)
Estas rutas se incluirán en emprendimiento y libranza

IMPORTANTE: Este archivo es auxiliar y sus vistas serán incluidas
en urls_emprendimiento.py y urls_libranza.py respectivamente
"""
from django.urls import path
from . import views

# NO tiene app_name porque se incluirá en otros namespaces

# Para incluir en /emprendimiento/mi-credito/
urlpatterns_emprendimiento = [
    path('mi-credito/', views.dashboard_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', views.dashboard_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),
]

# Para incluir en /libranza/mi-credito/
urlpatterns_libranza = [
    path('mi-credito/', views.dashboard_libranza_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', views.dashboard_libranza_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),
]
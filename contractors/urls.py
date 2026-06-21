from django.urls import path

from contractors.views import (
    VistaLoginContratistas,
    VistaRegistroContratistas,
    analizar_contrato_contratista_view,
    buscar_empresas_contratistas_view,
    calcular_simulacion_contratista_view,
    documentos_solicitud_contratista_view,
    landing_contratista_view,
    logout_contratistas_view,
    mi_credito_contratista_view,
    politica_privacidad_contratistas_view,
    simulador_contratista_view,
    solicitud_contratista_view,
    terminos_condiciones_contratistas_view,
)


app_name = 'contractors'

urlpatterns = [
    path('', landing_contratista_view, name='landing_contratista'),
    path('login/', VistaLoginContratistas.as_view(), name='login_contratistas'),
    path('registro/', VistaRegistroContratistas.as_view(), name='registro_contratistas'),
    path('logout/', logout_contratistas_view, name='logout_contratistas'),
    path('simular/', simulador_contratista_view, name='simulador_contratista'),
    path('simular/calcular/', calcular_simulacion_contratista_view, name='calcular_simulacion_contratista'),
    path('solicitar/', solicitud_contratista_view, name='solicitud_contratista'),
    path('contrato/analizar/', analizar_contrato_contratista_view, name='analizar_contrato_contratista'),
    path('mi-credito/', mi_credito_contratista_view, name='mi_credito_contratista'),
    path('empresas/buscar/', buscar_empresas_contratistas_view, name='buscar_empresas_contratistas'),
    path('terminos-y-condiciones/', terminos_condiciones_contratistas_view, name='terminos_condiciones_contratistas'),
    path('politica-de-privacidad/', politica_privacidad_contratistas_view, name='politica_privacidad_contratistas'),
    path(
        'solicitud/<int:solicitud_id>/documentos/',
        documentos_solicitud_contratista_view,
        name='documentos_solicitud_contratista',
    ),
]

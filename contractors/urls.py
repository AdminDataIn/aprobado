from django.urls import path

from contractors import views, views_admin


app_name = 'contractors'

urlpatterns = [
    path('', views.inicio_prestadores_view, name='inicio'),
    path('solicitar/', views.solicitar_prestador_view, name='solicitar'),
    path(
        'contrato/analizar/',
        views.analizar_contrato_prestador_view,
        name='analizar_contrato_temporal',
    ),
    path('solicitud/<int:solicitud_id>/documentos/', views.documentos_prestador_view, name='documentos'),
    path(
        'solicitud/<int:solicitud_id>/contrato/analizar/',
        views.analizar_contrato_prestador_view,
        name='analizar_contrato',
    ),
    path(
        'solicitud/<int:solicitud_id>/documentos/<int:documento_id>/',
        views.ver_documento_prestador_view,
        name='ver_documento',
    ),
    path(
        'solicitud/<int:solicitud_id>/documentos/<int:documento_id>/descargar/',
        views.descargar_documento_prestador_view,
        name='descargar_documento',
    ),
    path('simular/', views.simular_prestador_view, name='simular'),
    path('simular/calcular/', views.calcular_simulacion_prestador_view, name='calcular_simulacion'),
    path('mi-credito/', views.mi_credito_prestador_view, name='mi_credito'),
    path(
        'mi-credito/solicitud/<int:solicitud_id>/condiciones/',
        views.condiciones_solicitud_prestador_view,
        name='condiciones_solicitud',
    ),
    path(
        'mi-credito/solicitud/<int:solicitud_id>/subsanacion/<int:requerimiento_id>/',
        views.atender_subsanacion_prestador_view,
        name='atender_subsanacion',
    ),
    path('terminos-y-condiciones/', views.legal_prestadores_view, {'seccion': 'terminos'}, name='terminos'),
    path('politica-de-privacidad/', views.legal_prestadores_view, {'seccion': 'privacidad'}, name='privacidad'),
    path('centrales-de-informacion/', views.legal_prestadores_view, {'seccion': 'centrales'}, name='centrales_informacion'),
    path('gestion/prestadores/', views_admin.bandeja_prestadores_view, name='admin_bandeja'),
    path('gestion/prestadores/<int:solicitud_id>/', views_admin.detalle_prestador_view, name='admin_detalle'),
    path(
        'gestion/prestadores/solicitudes/<int:solicitud_id>/evaluar/',
        views_admin.ejecutar_evaluacion_prestador_view,
        name='admin_ejecutar_evaluacion',
    ),
    path(
        'gestion/prestadores/<int:solicitud_id>/aprobacion-interna/crear/',
        views_admin.crear_aprobacion_interna_prestador_view,
        name='admin_crear_aprobacion_interna',
    ),
    path(
        'gestion/prestadores/aprobaciones/<int:gate_id>/accion/',
        views_admin.accion_aprobacion_interna_prestador_view,
        name='admin_accion_aprobacion_interna',
    ),
    path(
        'gestion/prestadores/aprobaciones/<int:gate_id>/originar/',
        views_admin.originar_credito_prestador_view,
        name='admin_originar_credito',
    ),
    path(
        'gestion/prestadores/origenes/<int:origen_id>/formalizar/',
        views_admin.preparar_formalizacion_prestador_view,
        name='admin_preparar_formalizacion',
    ),
    path(
        'gestion/prestadores/formalizaciones/<int:formalizacion_id>/enviar-firma/',
        views_admin.enviar_formalizacion_prestador_view,
        name='admin_enviar_formalizacion',
    ),
    path(
        'gestion/prestadores/formalizaciones/<int:formalizacion_id>/novedad-operativa/',
        views_admin.crear_novedad_operativa_prestador_view,
        name='admin_crear_novedad_operativa',
    ),
    path(
        'gestion/prestadores/novedades/<int:novedad_id>/enviar/',
        views_admin.enviar_novedad_operativa_prestador_view,
        name='admin_enviar_novedad_operativa',
    ),
    path(
        'gestion/prestadores/revisiones/<int:revision_id>/accion/',
        views_admin.accion_revision_prestador_view,
        name='admin_accion_revision',
    ),
    path(
        'gestion/prestadores/documentos/<int:documento_id>/descargar/',
        views_admin.descargar_documento_prestador_staff_view,
        name='admin_descargar_documento',
    ),
]

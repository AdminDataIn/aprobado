"""
URLs de PAGADOR (Pagaduria de la empresa)
Prefijo: /pagador/

Rol: Usuarios con PerfilPagador que gestionan pagos de libranza
"""
from django.urls import path
from usuarios import views as usuarios_views
from contractors import views_pagador as contractors_views_pagador
from .. import views

app_name = 'pagador'

urlpatterns = [
    # ========================================
    # AUTENTICACIÓN
    # ========================================
    path('login/', usuarios_views.EmpresaLoginView.as_view(), name='login'),
    path('logout/', usuarios_views.CustomLogoutView.as_view(), name='logout'),
    path('activar/<str:token>/', usuarios_views.pagador_activate_account_view, name='activar_cuenta'),
    path('recuperar-acceso/', usuarios_views.pagador_password_reset_request_view, name='password_reset_request'),
    path('recuperar-acceso/<str:token>/', usuarios_views.pagador_password_reset_confirm_view, name='reset_password_confirm'),

    # ========================================
    # DASHBOARD
    # ========================================
    path('', views.pagador_dashboard_view, name='dashboard'),
    path('adelantos/', views.pagador_adelantos_dashboard_view, name='adelantos_dashboard'),
    path('empleados/cargar/', views.pagador_carga_empleados_view, name='carga_empleados'),
    path('empleados/plantilla/', views.descargar_plantilla_empleados_view, name='descargar_plantilla_empleados'),
    path('empleados/reconciliar/', views.pagador_reconciliar_empleados_view, name='reconciliar_empleados'),
    path('credito/<int:credito_id>/', views.pagador_detalle_credito_view, name='credito_detalle'),
    path('credito/<int:credito_id>/registrar-pago-offline/', views.pagador_registrar_pago_offline_view, name='registrar_pago_offline'),
    path('pago/<int:pago_id>/comprobante/', views.pagador_comprobante_pago_view, name='comprobante_pago'),
    path('obligaciones/pagar/', views.pagador_pagar_obligaciones_seleccionadas_view, name='pagar_obligaciones'),
    path('pagos-breb/qr/', views.pagador_qr_breb_view, name='pago_breb_qr'),
    path('pagos-breb/<int:pago_id>/comprobante/', views.comprobante_pago_breb_view, name='pago_breb_comprobante'),
    path('credito/<int:credito_id>/decision/', views.pagador_decidir_solicitud_view, name='decidir_solicitud'),
    path(
        'prestadores/aprobaciones/',
        contractors_views_pagador.aprobaciones_prestadores_view,
        name='prestadores_aprobaciones',
    ),
    path(
        'prestadores/aprobaciones/<int:aprobacion_id>/',
        contractors_views_pagador.detalle_aprobacion_prestador_view,
        name='prestadores_aprobacion_detalle',
    ),
    path(
        'prestadores/aprobaciones/<int:aprobacion_id>/decidir/',
        contractors_views_pagador.decidir_aprobacion_prestador_view,
        name='prestadores_aprobacion_decidir',
    ),
    path(
        'prestadores/novedades/',
        contractors_views_pagador.novedades_operativas_prestadores_view,
        name='prestadores_novedades',
    ),
    path(
        'prestadores/novedades/<int:novedad_id>/',
        contractors_views_pagador.detalle_novedad_operativa_prestador_view,
        name='prestadores_novedad_detalle',
    ),
    path(
        'prestadores/novedades/<int:novedad_id>/confirmar-recepcion/',
        contractors_views_pagador.confirmar_recepcion_novedad_prestador_view,
        name='prestadores_novedad_confirmar_recepcion',
    ),
    path(
        'prestadores/novedades/<int:novedad_id>/marcar-gestionada/',
        contractors_views_pagador.marcar_gestionada_novedad_prestador_view,
        name='prestadores_novedad_marcar_gestionada',
    ),

    # ========================================
    # PROCESAMIENTO DE PAGOS - SIMULACIÓN
    # ========================================
    path('pagar/<int:credito_id>/', views.iniciar_pago_view, name='pagar_individual'),
    path('pagar/callback/', views.procesar_pago_callback_view, name='pago_callback'),
    path('pagos-masivos/', views.pagador_procesar_pagos_view, name='pagos_masivos'),
    path('pagos-masivos/<int:lote_id>/', views.pagador_confirmar_pagos_masivos_view, name='pagos_masivos_confirmar'),

    # ========================================
    # PROCESAMIENTO DE PAGOS - WOMPI (REAL)
    # ========================================
    path('pago/wompi/<int:credito_id>/', views.iniciar_pago_wompi_view, name='pagar_wompi'),
    path('pago/wompi/procesar/', views.procesar_pago_wompi_view, name='procesar_pago_wompi'),
    path('pago/wompi/callback/', views.pago_wompi_callback_view, name='pago_wompi_callback'),
    path('pago/wompi/resumen/<str:transaction_id>/', views.pagador_pago_resumen_wompi_view, name='pago_wompi_resumen'),
    path('pago/wompi/comprobante/<str:transaction_id>/', views.pagador_pago_comprobante_wompi_view, name='pago_wompi_comprobante'),

    # ========================================
    # PAGO MASIVO CON WOMPI
    # ========================================
    path('pago-masivo-wompi/', views.iniciar_pago_masivo_wompi_view, name='pagar_masivo_wompi'),

    # ========================================
    # UTILIDADES Y REPORTES
    # ========================================
    path('descargar-cuotas-excel/', views.descargar_csv_cuotas_pendientes_view, name='descargar_cuotas_excel'),
    path('descargar-cuotas/', views.descargar_csv_cuotas_pendientes_view, name='descargar_cuotas_excel_legacy'),
    path('descargar-reporte/', views.descargar_reporte_pagador_view, name='descargar_reporte'),

    # API endpoints
    path('api/bancos-pse/', views.get_pse_banks_view, name='api_bancos_pse'),
]

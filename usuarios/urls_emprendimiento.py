"""
URLs del producto EMPRENDIMIENTO
Prefijo: /emprendimiento/
"""
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import ProductPasswordResetForm
from gestion_creditos import views as gestion_views
from usuariocreditos import views as credito_views


app_name = 'emprendimiento'

urlpatterns = [
    path('', views.index, name='landing'),
    path('simulador/', views.simulador, name='simulador'),

    path('login/', views.LoginEmprendimientoView.as_view(), name='login'),
    path('registro/', views.EmprendimientoRegisterView.as_view(), name='register'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=ProductPasswordResetForm,
            template_name='account/emprendimiento/password_reset_form.html',
            email_template_name='account/common/customer_password_reset_email.txt',
            html_email_template_name='account/common/customer_password_reset_email.html',
            subject_template_name='account/common/customer_password_reset_subject.txt',
            success_url=reverse_lazy('emprendimiento:password_reset_done'),
            extra_email_context={
                'producto': 'Emprendimiento',
                'reset_route_name': 'emprendimiento:password_reset_confirm',
            },
            extra_context={
                'login_url': reverse_lazy('emprendimiento:login'),
                'back_url': reverse_lazy('emprendimiento:landing'),
            },
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/emprendimiento/password_reset_done.html'
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/emprendimiento/password_reset_confirm.html',
            success_url=reverse_lazy('emprendimiento:password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/emprendimiento/password_reset_complete.html'
        ),
        name='password_reset_complete',
    ),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),

    path('solicitar/', gestion_views.solicitud_credito_emprendimiento_view, name='solicitar'),

    path('mi-credito/', credito_views.dashboard_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', credito_views.dashboard_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', credito_views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', credito_views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),

    path('mi-credito/<int:credito_id>/calcular-pago-total/', gestion_views.calcular_pago_total_view, name='calcular_pago_total'),
    path('mi-credito/<int:credito_id>/analizar-abono/', gestion_views.analizar_abono_credito_view, name='analizar_abono'),
    path('mi-credito/<int:credito_id>/confirmar-abono/', gestion_views.confirmar_abono_credito_view, name='confirmar_abono'),
    path('mi-credito/<int:credito_id>/historial-abonos/', gestion_views.historial_reestructuraciones_view, name='historial_abonos'),

    path('mi-credito/<int:credito_id>/pago/wompi/', gestion_views.iniciar_pago_wompi_emprendimiento_view, name='pagar_wompi'),
    path('mi-credito/pago/wompi/procesar/', gestion_views.procesar_pago_wompi_emprendimiento_view, name='procesar_pago_wompi'),
    path('mi-credito/pago/wompi/callback/', gestion_views.pago_wompi_emprendimiento_callback_view, name='pago_wompi_callback'),
]

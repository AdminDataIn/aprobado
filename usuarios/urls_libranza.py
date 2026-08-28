"""
URLs del producto LIBRANZA
Prefijo: /libranza/
"""
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import ProductPasswordResetForm
from gestion_creditos import views as gestion_views
from usuariocreditos import views as credito_views


app_name = 'libranza'

urlpatterns = [
    path('', views.libranza_landing, name='landing'),
    path('simulador/', views.simulador_libranza, name='simulador'),
    path(
        'api/simulador/originacion/',
        gestion_views.simular_libranza_financiera_view,
        name='simular_originacion',
    ),
    path('api/empresas/buscar/', gestion_views.buscar_empresas_convenio_view, name='buscar_empresas'),
    path('api/adelanto/simular/', gestion_views.simular_adelanto_nomina_view, name='simular_adelanto'),

    path('login/', views.LoginLibranzaView.as_view(), name='login'),
    path('registro/', views.LibranzaRegisterView.as_view(), name='register'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=ProductPasswordResetForm,
            template_name='account/libranza/password_reset_form.html',
            email_template_name='account/common/customer_password_reset_email.txt',
            html_email_template_name='account/common/customer_password_reset_email.html',
            subject_template_name='account/common/customer_password_reset_subject.txt',
            success_url=reverse_lazy('libranza:password_reset_done'),
            extra_email_context={
                'producto': 'Libranza',
                'reset_route_name': 'libranza:password_reset_confirm',
            },
            extra_context={
                'login_url': reverse_lazy('libranza:login'),
                'back_url': reverse_lazy('libranza:landing'),
            },
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/libranza/password_reset_done.html'
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/libranza/password_reset_confirm.html',
            success_url=reverse_lazy('libranza:password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/libranza/password_reset_complete.html'
        ),
        name='password_reset_complete',
    ),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),

    path('solicitar/', gestion_views.solicitud_credito_libranza_view, name='solicitar'),
    path('adelanto-nomina/', gestion_views.solicitud_adelanto_nomina_view, name='adelanto_nomina'),

    path('mi-credito/', credito_views.dashboard_libranza_view, name='mi_credito'),
    path('mi-credito/<int:credito_id>/', credito_views.dashboard_libranza_view, name='mi_credito_detalle'),
    path('mi-credito/<int:credito_id>/extracto/', credito_views.descargar_extracto, name='descargar_extracto'),
    path('mi-credito/<int:credito_id>/plan-pagos/', credito_views.descargar_plan_pagos_pdf, name='descargar_plan_pagos'),
]

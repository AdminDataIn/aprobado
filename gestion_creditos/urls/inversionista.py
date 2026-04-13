from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import path, reverse_lazy

from usuarios.forms import ProductPasswordResetForm

from ..views import inversionista as views_inversionista
from usuarios import views as usuarios_views


app_name = 'inversionista'

urlpatterns = [
    path('login/', usuarios_views.LoginInversionistaView.as_view(), name='login'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=ProductPasswordResetForm,
            template_name='account/inversionista/password_reset_form.html',
            email_template_name='account/inversionista/password_reset_email.txt',
            html_email_template_name='account/inversionista/password_reset_email.html',
            subject_template_name='account/inversionista/password_reset_subject.txt',
            success_url=reverse_lazy('inversionista:password_reset_done'),
            extra_email_context={
                'producto': 'Inversionista',
                'reset_route_name': 'inversionista:password_reset_confirm',
            },
            extra_context={
                'login_url': reverse_lazy('inversionista:login'),
                'back_url': reverse_lazy('inversionista:login'),
            },
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/inversionista/password_reset_done.html'
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/inversionista/password_reset_confirm.html',
            success_url=reverse_lazy('inversionista:password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/inversionista/password_reset_complete.html'
        ),
        name='password_reset_complete',
    ),
    path(
        'password-change/',
        login_required(
            auth_views.PasswordChangeView.as_view(
                template_name='account/inversionista/password_change_form.html',
                success_url=reverse_lazy('inversionista:password_change_done'),
            ),
            login_url=reverse_lazy('inversionista:login'),
        ),
        name='password_change',
    ),
    path(
        'password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='account/inversionista/password_change_done.html'
        ),
        name='password_change_done',
    ),
    path('activar/<str:token>/', usuarios_views.investor_activate_account_view, name='activar_cuenta'),
    path('logout/', usuarios_views.CustomLogoutView.as_view(), name='logout'),
    path('', views_inversionista.investor_dashboard_view, name='dashboard'),
]

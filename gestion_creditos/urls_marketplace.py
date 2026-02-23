from django.urls import path
from . import views
from usuarios.views import MarketingLoginView
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

app_name = 'marketplace'

urlpatterns = [
    path('', views.marketplace_general_view, name='home'),
    path('login/', MarketingLoginView.as_view(), name='login'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='account/marketplace_password_reset_form.html',
            email_template_name='account/marketplace_password_reset_email.txt',
            html_email_template_name='account/marketplace_password_reset_email.html',
            subject_template_name='account/marketplace_password_reset_subject.txt',
            success_url=reverse_lazy('marketplace:password_reset_done')
        ),
        name='password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/marketplace_password_reset_done.html'
        ),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/marketplace_password_reset_confirm.html',
            success_url=reverse_lazy('marketplace:password_reset_complete')
        ),
        name='password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/marketplace_password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),
    path('empresa/<slug:empresa_slug>/', views.marketplace_empresa_view, name='empresa'),
    path('panel/', views.marketplace_panel_view, name='panel'),
    path('panel/nuevo/', views.marketplace_item_create_view, name='item_create'),
    path('panel/<int:item_id>/editar/', views.marketplace_item_edit_view, name='item_edit'),
    path('panel/<int:item_id>/desactivar/', views.marketplace_item_deactivate_view, name='item_deactivate'),
]

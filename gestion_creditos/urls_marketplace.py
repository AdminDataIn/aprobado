from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from usuarios.forms import ProductPasswordResetForm
from usuarios import views as usuarios_views

from . import views
from . import views_marketplace_checkout


app_name = 'marketplace'

urlpatterns = [
    path('', views.marketplace_general_view, name='home'),
    path('login/', usuarios_views.MarketplaceBuyerLoginView.as_view(), name='login'),
    path('registro/', usuarios_views.MarketplaceBuyerRegisterView.as_view(), name='register'),
    path('logout/', usuarios_views.CustomLogoutView.as_view(), name='logout'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=ProductPasswordResetForm,
            template_name='account/marketplace_buyer/password_reset_form.html',
            email_template_name='account/common/marketplace_password_reset_email.txt',
            html_email_template_name='account/common/marketplace_password_reset_email.html',
            subject_template_name='account/common/marketplace_password_reset_subject.txt',
            success_url=reverse_lazy('marketplace:password_reset_done'),
        ),
        name='password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/marketplace_buyer/password_reset_done.html'
        ),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/marketplace_buyer/password_reset_confirm.html',
            success_url=reverse_lazy('marketplace:password_reset_complete')
        ),
        name='password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/marketplace_buyer/password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),
    path('empresa/<slug:empresa_slug>/', views.marketplace_empresa_view, name='empresa'),
    path('empresa/<slug:empresa_slug>/login/', usuarios_views.MarketplaceAdminLoginView.as_view(), name='admin_login'),
    path('panel/login/', usuarios_views.MarketplaceAdminLoginView.as_view(), name='admin_login_without_company'),
    path(
        'panel/password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=ProductPasswordResetForm,
            template_name='account/marketplace_admin/password_reset_form.html',
            email_template_name='account/common/marketplace_password_reset_email.txt',
            html_email_template_name='account/common/marketplace_password_reset_email.html',
            subject_template_name='account/common/marketplace_password_reset_subject.txt',
            success_url=reverse_lazy('marketplace:admin_password_reset_done')
        ),
        name='admin_password_reset'
    ),
    path(
        'panel/password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='account/marketplace_admin/password_reset_done.html'
        ),
        name='admin_password_reset_done'
    ),
    path(
        'panel/reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='account/marketplace_admin/password_reset_confirm.html',
            success_url=reverse_lazy('marketplace:admin_password_reset_complete')
        ),
        name='admin_password_reset_confirm'
    ),
    path(
        'panel/reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='account/marketplace_admin/password_reset_complete.html'
        ),
        name='admin_password_reset_complete'
    ),
    path('item/<int:item_id>/comprar/', views_marketplace_checkout.marketplace_checkout_view, name='checkout'),
    path('pedido/<str:numero_pedido>/', views_marketplace_checkout.marketplace_checkout_detail_view, name='checkout_detail'),
    path('mis-pedidos/', views_marketplace_checkout.marketplace_order_list_view, name='orders'),
    path('panel/', views.marketplace_panel_view, name='panel'),
    path('panel/nuevo/', views.marketplace_item_create_view, name='item_create'),
    path('panel/<int:item_id>/editar/', views.marketplace_item_edit_view, name='item_edit'),
    path('panel/<int:item_id>/desactivar/', views.marketplace_item_deactivate_view, name='item_deactivate'),
]

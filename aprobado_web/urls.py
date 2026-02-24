"""
URL configuration for aprobado_web project.

🎯 NUEVA ESTRUCTURA ORGANIZADA (2025-12-21)

Prefijos principales:
- /emprendimiento/  → Producto de microcréditos para emprendedores
- /libranza/        → Producto de crédito de nómina
- /gestion/         → Panel de analistas de crédito
- /pagador/         → Panel de pagadores de empresas (RR.HH.)
- /billetera/       → Sistema de ahorro digital

Rutas legacy eliminadas.
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static
from gestion_creditos import views as gestion_views
from gestion_creditos.services.pagare_url import descargar_pagare_publico
from .views import portal_entrypoint_view

urlpatterns = [
    # ========================================
    # ADMINISTRACIÓN DJANGO
    # ========================================
    path('admin/', admin.site.urls),

    # ========================================
    # WEBHOOKS Y APIs PÚBLICAS (Sin autenticación)
    # ========================================
    path('webhook/wompi/events/', gestion_views.wompi_webhook_view, name='wompi_webhook'),
    path('api/webhooks/zapsign/', gestion_views.zapsign_webhook_view, name='zapsign_webhook'),
    path('api/pagares/download/<str:token>/', descargar_pagare_publico, name='descargar_pagare_publico'),

    # ========================================
    # AUTENTICACIÓN (Django Allauth)
    # ========================================
    path('accounts/', include('allauth.urls')),

    # ========================================
    # PRODUCTOS (NUEVA ESTRUCTURA)
    # ========================================
    path('emprendimiento/', include('usuarios.urls_emprendimiento')),
    path('libranza/', include('usuarios.urls_libranza')),

    # ========================================
    # LEGALES
    # ========================================
    path('privacidad/', TemplateView.as_view(
        template_name='legal/politica_privacidad.html'
    ), name='politica_privacidad'),
    path('terminos/', TemplateView.as_view(
        template_name='legal/terminos_condiciones.html'
    ), name='terminos_condiciones'),

    # ========================================
    # ROLES ADMINISTRATIVOS
    # ========================================
    path('gestion/', include('gestion_creditos.urls_gestion')),
    path('pagador/', include('gestion_creditos.urls_pagador')),

    # ========================================
    # MARKETPLACE (PÚBLICO)
    # ========================================
    path('marketplace/', include('gestion_creditos.urls_marketplace')),

    # ========================================
    # BILLETERA DIGITAL
    # ========================================
    path('billetera/', include('gestion_creditos.urls_billetera')),

    # ========================================
    # PÁGINA DE INICIO
    # ========================================
    path('', portal_entrypoint_view, name='home'),
]

# Servir archivos media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

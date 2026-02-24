"""
Rutas compartidas entre dominios/subdominios.
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from gestion_creditos import views as gestion_views
from gestion_creditos.services.pagare_url import descargar_pagare_publico


common_urlpatterns = [
    # Admin Django
    path("admin/", admin.site.urls),

    # Webhooks/APIs publicas
    path("webhook/wompi/events/", gestion_views.wompi_webhook_view, name="wompi_webhook"),
    path("api/webhooks/zapsign/", gestion_views.zapsign_webhook_view, name="zapsign_webhook"),
    path("api/pagares/download/<str:token>/", descargar_pagare_publico, name="descargar_pagare_publico"),

    # Autenticacion
    path("accounts/", include("allauth.urls")),

    # Legales
    path(
        "privacidad/",
        TemplateView.as_view(template_name="legal/politica_privacidad.html"),
        name="politica_privacidad",
    ),
    path(
        "terminos/",
        TemplateView.as_view(template_name="legal/terminos_condiciones.html"),
        name="terminos_condiciones",
    ),
]

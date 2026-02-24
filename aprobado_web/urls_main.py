"""
URLConf del dominio principal (aprobado.com.co).
Scope principal: Libranza + paneles internos.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .urls_common import common_urlpatterns
from .views import portal_entrypoint_view


urlpatterns = [
    *common_urlpatterns,

    # Producto principal
    path("libranza/", include("usuarios.urls_libranza")),

    # Roles administrativos internos
    path("gestion/", include("gestion_creditos.urls_gestion")),
    path("pagador/", include("gestion_creditos.urls_pagador")),

    # Billetera
    path("billetera/", include("gestion_creditos.urls_billetera")),

    # Inicio segun host
    path("", portal_entrypoint_view, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

"""
URLConf del dominio principal (aprobado.com.co).
Scope principal: Libranza en raiz + paneles internos.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponsePermanentRedirect
from django.urls import include, path, re_path

from .urls_common import common_urlpatterns


def redirect_legacy_libranza(request, subpath=""):
    """
    Mantiene compatibilidad con URLs antiguas /libranza/... y las
    redirige a la nueva estructura en raiz del dominio principal.
    """
    target = f"/{subpath}" if subpath else "/"
    query = request.META.get("QUERY_STRING")
    if query:
        target = f"{target}?{query}"
    return HttpResponsePermanentRedirect(target)


urlpatterns = [
    *common_urlpatterns,

    # Roles administrativos internos
    path("gestion/", include("gestion_creditos.urls_gestion")),
    path("pagador/", include("gestion_creditos.urls_pagador")),

    # Billetera
    path("billetera/", include("gestion_creditos.urls_billetera")),

    # Compatibilidad legacy: /libranza/... -> /...
    path("libranza/", redirect_legacy_libranza, name="legacy_libranza_root"),
    re_path(r"^libranza/(?P<subpath>.*)$", redirect_legacy_libranza, name="legacy_libranza_path"),

    # Producto principal en raiz del dominio principal
    path("", include("usuarios.urls_libranza")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

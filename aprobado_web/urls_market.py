"""
URLConf del subdominio de marketplace (market.aprobado.com.co).
Scope: contenido de marketplace.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .urls_common import common_urlpatterns
from .views import portal_entrypoint_view


urlpatterns = [
    *common_urlpatterns,
    path("marketplace/", include("gestion_creditos.urls_marketplace")),
    path("", portal_entrypoint_view, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

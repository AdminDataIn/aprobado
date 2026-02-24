"""
URLConf del subdominio de emprendimiento (emprender.aprobado.com.co).
Scope: contenido de emprendimiento.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .urls_common import common_urlpatterns
from .views import portal_entrypoint_view


urlpatterns = [
    *common_urlpatterns,
    path("emprendimiento/", include("usuarios.urls_emprendimiento")),
    path("", portal_entrypoint_view, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

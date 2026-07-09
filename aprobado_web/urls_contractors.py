from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .urls_common import common_urlpatterns


urlpatterns = [
    *common_urlpatterns,
    path('', include('contractors.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

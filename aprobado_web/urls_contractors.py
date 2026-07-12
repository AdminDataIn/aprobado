from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from contractors import views as contractors_views

from .urls_common import common_urlpatterns


urlpatterns = [
    path('', contractors_views.inicio_prestadores_view, name='home'),
    *common_urlpatterns,
    path('', include('contractors.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

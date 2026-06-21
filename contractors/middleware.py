from django.conf import settings

from contractors.selectors import (
    obtener_configuracion_portal_contratistas_por_host,
    obtener_configuracion_portal_contratistas_por_slug,
    obtener_organizacion_por_subdominio,
)


SUBDOMINIO_PORTAL_CONTRATISTAS = 'contratistas'


class ContractorTenantMiddleware:
    """Resuelve la configuracion del portal unico de contratistas.

    Este middleware solo fija contexto de portal en el request. No autentica
    usuarios y no autoriza accesos.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.configuracion_portal_contratistas = self._resolver_configuracion_portal(request)
        request.contractor_organization = self._resolve_organization(request)
        return self.get_response(request)

    def _resolver_configuracion_portal(self, request):
        host = (request.get_host() or '').split(':', 1)[0].lower()
        if not self._es_host_portal(request, host):
            return None
        configuracion = obtener_configuracion_portal_contratistas_por_host(host)
        if configuracion is None and getattr(settings, 'DEBUG', False) and host == f'{SUBDOMINIO_PORTAL_CONTRATISTAS}.localhost':
            configuracion = obtener_configuracion_portal_contratistas_por_slug(SUBDOMINIO_PORTAL_CONTRATISTAS)
        return configuracion

    def _resolve_organization(self, request):
        subdominio = self._extraer_subdominio_portal(request)
        if not subdominio:
            return None
        return obtener_organizacion_por_subdominio(subdominio)

    def _extraer_subdominio_portal(self, request):
        host = (request.get_host() or '').split(':', 1)[0].lower()
        if not self._es_host_portal(request, host):
            return None
        return SUBDOMINIO_PORTAL_CONTRATISTAS

    def _es_host_portal(self, request, host):
        primary_domain = self._normalizar_host_configurado(
            getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co'),
        )
        portal_host = self._normalizar_host_configurado(
            getattr(settings, 'CONTRACTORS_PORTAL_HOST', f'{SUBDOMINIO_PORTAL_CONTRATISTAS}.{primary_domain}'),
        )

        if host == portal_host:
            return True

        if getattr(settings, 'DEBUG', False) and host == f'{SUBDOMINIO_PORTAL_CONTRATISTAS}.localhost':
            return True

        return False

    @staticmethod
    def _normalizar_host_configurado(host):
        host = (host or '').strip().lower()
        host = host.removeprefix('https://').removeprefix('http://')
        return host.split('/', 1)[0].split(':', 1)[0]

    # Alias interno temporal para tests/compatibilidad de Fase 1.
    _extract_subdomain = _extraer_subdominio_portal

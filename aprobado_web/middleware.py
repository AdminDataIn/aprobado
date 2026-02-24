"""
Middleware de enrutamiento estricto por subdominio.

Define el URLConf activo segun el host y evita mezcla de rutas entre
subdominios (libranza/emprendimiento/marketplace).
"""
from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class SubdomainRoutingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()

        primary_host = getattr(settings, "PRIMARY_DOMAIN_HOST", "aprobado.com.co").lower()
        emprender_host = getattr(settings, "EMPRENDER_SUBDOMAIN_HOST", "emprender.aprobado.com.co").lower()
        market_host = getattr(settings, "MARKET_SUBDOMAIN_HOST", "market.aprobado.com.co").lower()
        www_primary_host = f"www.{primary_host}"

        if host == emprender_host:
            request.urlconf = "aprobado_web.urls_emprender"
            redirect_host = self._redirect_host_for_path(request.path, primary_host, emprender_host, market_host)
            if redirect_host and redirect_host != host:
                return self._redirect(request, redirect_host)
        elif host == market_host:
            request.urlconf = "aprobado_web.urls_market"
            redirect_host = self._redirect_host_for_path(request.path, primary_host, emprender_host, market_host)
            if redirect_host and redirect_host != host:
                return self._redirect(request, redirect_host)
        else:
            # Dominio principal + fallback (localhost/IPs/otros hosts permitidos)
            request.urlconf = "aprobado_web.urls_main"
            if host in {primary_host, www_primary_host}:
                redirect_host = self._redirect_host_for_path(request.path, primary_host, emprender_host, market_host)
                if redirect_host and redirect_host != host:
                    return self._redirect(request, redirect_host)

        return self.get_response(request)

    @staticmethod
    def _redirect_host_for_path(path, primary_host, emprender_host, market_host):
        normalized_path = (path or "/").lower()

        if normalized_path.startswith("/emprendimiento/"):
            return emprender_host
        if normalized_path.startswith("/marketplace/"):
            return market_host
        if normalized_path.startswith("/libranza/"):
            return primary_host
        return None

    @staticmethod
    def _redirect(request, target_host):
        scheme = "https" if request.is_secure() else "http"
        query = request.META.get("QUERY_STRING")
        suffix = f"?{query}" if query else ""
        return HttpResponsePermanentRedirect(f"{scheme}://{target_host}{request.path}{suffix}")

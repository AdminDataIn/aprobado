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

        primary_host = self._normalizar_host_configurado(
            getattr(settings, "PRIMARY_DOMAIN_HOST", "aprobado.com.co"),
        )
        emprender_host = self._normalizar_host_configurado(
            getattr(settings, "EMPRENDER_SUBDOMAIN_HOST", "emprender.aprobado.com.co"),
        )
        market_host = self._normalizar_host_configurado(
            getattr(settings, "MARKET_SUBDOMAIN_HOST", "market.aprobado.com.co"),
        )
        contractors_host = self._normalizar_host_configurado(
            getattr(settings, "CONTRACTORS_PORTAL_HOST", f"contratistas.{primary_host}"),
        )
        www_primary_host = f"www.{primary_host}"

        if self._es_host_contratistas(host, contractors_host):
            request.urlconf = "aprobado_web.urls_contractors"
        elif host in {'127.0.0.1', 'localhost'}:
            request.urlconf = self._urlconf_for_local_path(request.path)
        elif host == emprender_host:
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
    def _urlconf_for_local_path(path):
        normalized_path = (path or "/").lower()
        if normalized_path.startswith('/emprendimiento/'):
            return "aprobado_web.urls_emprender"
        if normalized_path.startswith('/marketplace/'):
            return "aprobado_web.urls_market"
        return "aprobado_web.urls_main"

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
    def _es_host_contratistas(host, contractors_host):
        if host == contractors_host:
            return True
        return bool(getattr(settings, "DEBUG", False) and host == "contratistas.localhost")

    @staticmethod
    def _normalizar_host_configurado(host):
        host = (host or "").strip().lower()
        host = host.removeprefix("https://").removeprefix("http://")
        return host.split("/", 1)[0].split(":", 1)[0]

    @staticmethod
    def _redirect(request, target_host):
        scheme = "https" if request.is_secure() else "http"
        query = request.META.get("QUERY_STRING")
        suffix = f"?{query}" if query else ""
        return HttpResponsePermanentRedirect(f"{scheme}://{target_host}{request.path}{suffix}")

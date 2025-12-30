"""
Middleware para gestión de contexto de producto (Libranza/Emprendimiento)
"""

class ProductoContextMiddleware:
    """
    Middleware que detecta automáticamente el producto (LIBRANZA o EMPRENDIMIENTO)
    basándose en la URL actual y lo guarda en la sesión del usuario.

    Esto optimiza el sistema de logout evitando consultas a la base de datos.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Solo procesar si el usuario está autenticado
        if request.user.is_authenticated:
            path = request.path

            # Detectar si viene de URLs de libranza
            if any(url_part in path for url_part in ['/libranza/', '/pagador/']):
                request.session['producto_actual'] = 'LIBRANZA'

            # Detectar si viene de URLs de emprendimiento
            elif any(url_part in path for url_part in ['/emprendimiento/', '/aplicando/']):
                request.session['producto_actual'] = 'EMPRENDIMIENTO'

            # Billetera y otras URLs no cambian el producto actual
            # Se mantiene el último producto detectado

        response = self.get_response(request)
        return response

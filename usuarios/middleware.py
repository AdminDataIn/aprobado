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

            nuevo_producto = None

            # Detectar si viene de URLs de libranza
            if any(url_part in path for url_part in ['/libranza/', '/pagador/']):
                nuevo_producto = 'LIBRANZA'

            # Detectar si viene de URLs de emprendimiento
            elif any(url_part in path for url_part in ['/emprendimiento/', '/aplicando/']):
                nuevo_producto = 'EMPRENDIMIENTO'

            if nuevo_producto and request.session.get('producto_actual') != nuevo_producto:
                request.session['producto_actual'] = nuevo_producto

            # Billetera y otras URLs no cambian el producto actual
            # Se mantiene el último producto detectado

        response = self.get_response(request)
        return response

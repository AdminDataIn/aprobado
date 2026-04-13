"""
Inicialización del proyecto Aprobado.
Importa la aplicación Celery para que esté disponible cuando Django inicie.
"""
try:
    from .celery import app as celery_app
except ModuleNotFoundError:
    celery_app = None

__all__ = ('celery_app',)

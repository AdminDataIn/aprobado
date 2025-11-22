"""
Inicialización del proyecto Aprobado.
Importa la aplicación Celery para que esté disponible cuando Django inicie.
"""
from .celery import app as celery_app

__all__ = ('celery_app',)

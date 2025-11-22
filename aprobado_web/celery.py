"""
Configuración de Celery para el proyecto Aprobado.

Este archivo inicializa la aplicación Celery y la configura para usar Django settings.
"""
import os
from celery import Celery
from celery.schedules import crontab

# Configurar el módulo de settings de Django para Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')

# Crear la aplicación Celery
app = Celery('aprobado_web')

# Cargar configuración desde Django settings con namespace 'CELERY'
app.config_from_object('django.conf:settings', namespace='CELERY')

# Autodescubrir tareas en todas las apps instaladas
app.autodiscover_tasks()

# Configuración de tareas programadas (Celery Beat)
app.conf.beat_schedule = {
    # Tarea para marcar créditos en mora - Ejecutar todos los días a las 6:00 AM
    'marcar-creditos-en-mora': {
        'task': 'gestion_creditos.tasks.marcar_creditos_en_mora_task',
        'schedule': crontab(hour=6, minute=0),  # Diariamente a las 6:00 AM
    },

    # Tarea para enviar recordatorios de pago - Ejecutar todos los días a las 8:00 AM
    'enviar-recordatorios-pago': {
        'task': 'gestion_creditos.tasks.enviar_recordatorios_pago_task',
        'schedule': crontab(hour=8, minute=0),  # Diariamente a las 8:00 AM
    },

    # Tarea para enviar alertas de mora - Ejecutar todos los días a las 9:00 AM
    'enviar-alertas-mora': {
        'task': 'gestion_creditos.tasks.enviar_alertas_mora_task',
        'schedule': crontab(hour=9, minute=0),  # Diariamente a las 9:00 AM
    },
}

@app.task(bind=True)
def debug_task(self):
    """Tarea de prueba para verificar que Celery funciona correctamente."""
    print(f'Request: {self.request!r}')

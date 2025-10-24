from django.core.management.base import BaseCommand
from gestion_creditos import services
from django.utils import timezone

class Command(BaseCommand):
    help = 'Actualiza el estado de los créditos a EN_MORA usando el servicio centralizado.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE(f'[{timezone.now()}] Iniciando la verificación de créditos vencidos...'))
        
        try:
            actualizados = services.marcar_creditos_en_mora()
            self.stdout.write(self.style.SUCCESS(f'[{timezone.now()}] Proceso finalizado. Se actualizaron {actualizados} créditos a EN_MORA.'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'[{timezone.now()}] Ocurrió un error inesperado durante la actualización de mora: {e}'))
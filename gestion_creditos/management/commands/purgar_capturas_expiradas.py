from django.core.management.base import BaseCommand
from gestion_creditos.services.captura_documental import purgar_sesiones_expiradas


class Command(BaseCommand):
    help = 'Elimina archivos temporales expirados no vinculados; conserva auditoria y documentos historicos.'

    def handle(self, *args, **options):
        self.stdout.write(str(purgar_sesiones_expiradas()))

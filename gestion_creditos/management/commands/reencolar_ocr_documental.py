from django.core.management.base import BaseCommand
from gestion_creditos.services.ocr_documental import recuperar


class Command(BaseCommand):
    help = 'Reencola OCR pendiente o con reserva vencida, sin reprocesar resultados terminales.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=100)

    def handle(self, *args, **options):
        self.stdout.write(f'OCR seleccionados para reencolar: {recuperar(options["limite"])}')

"""
Comando Django para marcar créditos en mora manualmente.

Uso:
    python manage.py marcar_moras
"""
from django.core.management.base import BaseCommand
from gestion_creditos.services import marcar_creditos_en_mora


class Command(BaseCommand):
    help = 'Marca automáticamente los créditos que han entrado en mora'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Iniciando proceso de marcación de moras...'))

        creditos_actualizados = marcar_creditos_en_mora()

        if creditos_actualizados > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ {creditos_actualizados} crédito(s) marcado(s) en mora exitosamente'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('✓ No hay créditos para marcar en mora')
            )

"""
Comando de Django para limpiar datos huérfanos en la base de datos.
Uso: python manage.py limpiar_datos_huerfanos
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from gestion_creditos.models import (
    Credito, CuotaAmortizacion, HistorialPago,
    HistorialEstado, CreditoEmprendimiento, CreditoLibranza
)


class Command(BaseCommand):
    help = 'Limpia datos huérfanos (cuotas sin crédito asociado) de la base de datos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirmar',
            action='store_true',
            help='Confirma la eliminación sin preguntar',
        )

    def handle(self, *args, **options):
        self.stdout.write("Verificando datos huerfanos...")

        # Obtener todos los IDs de créditos válidos
        creditos_validos = set(Credito.objects.values_list('id', flat=True))
        self.stdout.write(self.style.SUCCESS(f"Creditos validos en la BD: {len(creditos_validos)}"))

        # Buscar datos huérfanos en todas las tablas relacionadas
        tablas_a_verificar = [
            ('CuotaAmortizacion', CuotaAmortizacion.objects.exclude(credito_id__in=creditos_validos)),
            ('HistorialPago', HistorialPago.objects.exclude(credito_id__in=creditos_validos)),
            ('HistorialEstado', HistorialEstado.objects.exclude(credito_id__in=creditos_validos)),
            ('CreditoEmprendimiento', CreditoEmprendimiento.objects.exclude(credito_id__in=creditos_validos)),
            ('CreditoLibranza', CreditoLibranza.objects.exclude(credito_id__in=creditos_validos)),
        ]

        total_huerfanas = 0
        resumen = {}

        for nombre_tabla, queryset in tablas_a_verificar:
            count = queryset.count()
            if count > 0:
                total_huerfanas += count
                resumen[nombre_tabla] = queryset

        self.stdout.write(self.style.WARNING(f"\nTotal de registros huerfanos encontrados: {total_huerfanas}"))

        if total_huerfanas == 0:
            self.stdout.write(self.style.SUCCESS("No se encontraron datos huerfanos. La base de datos esta limpia."))
            return

        # Mostrar detalles por tabla
        self.stdout.write("\nDetalles de registros huerfanos por tabla:")
        for nombre_tabla, queryset in resumen.items():
            count = queryset.count()
            self.stdout.write(f"\n  {nombre_tabla}: {count} registro(s)")

            # Mostrar primeros 5 registros de cada tabla
            for obj in queryset[:5]:
                self.stdout.write(f"    - ID {obj.id} -> Credito inexistente ID {obj.credito_id}")

            if count > 5:
                self.stdout.write(f"    ... y {count - 5} mas")

        # Confirmar eliminación
        if not options['confirmar']:
            self.stdout.write(self.style.WARNING("\nPara eliminar estos registros huerfanos, ejecuta:"))
            self.stdout.write(self.style.WARNING("   python manage.py limpiar_datos_huerfanos --confirmar"))
            return

        # Eliminar todos los registros huérfanos
        self.stdout.write("\nEliminando registros huerfanos...")
        total_eliminadas = 0

        with transaction.atomic():
            for nombre_tabla, queryset in resumen.items():
                eliminadas, detalles = queryset.delete()
                total_eliminadas += eliminadas
                self.stdout.write(self.style.SUCCESS(f"  {nombre_tabla}: {eliminadas} registro(s) eliminado(s)"))

            self.stdout.write(self.style.SUCCESS(f"\nTotal: {total_eliminadas} registros huerfanos eliminados exitosamente"))
            self.stdout.write(self.style.SUCCESS("\nAhora puedes ejecutar: python manage.py migrate"))

from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from gestion_creditos.models import Credito


class Command(BaseCommand):
    help = "Marca un credito como especial y permite parametrizar plazo, tasa y fecha de primera cuota."

    def add_arguments(self, parser):
        parser.add_argument('numero_credito', help='Numero de credito a parametrizar')
        parser.add_argument('--plazo', type=int, help='Plazo forzado en meses')
        parser.add_argument('--tasa', type=Decimal, help='Tasa mensual forzada')
        parser.add_argument(
            '--fecha-primera-cuota',
            dest='fecha_primera_cuota',
            help='Fecha forzada de primera cuota en formato YYYY-MM-DD',
        )
        parser.add_argument(
            '--observacion',
            default='Marcado como credito especial por operacion.',
            help='Observacion operativa obligatoria del ajuste',
        )

    def handle(self, *args, **options):
        numero_credito = options['numero_credito']
        try:
            credito = Credito.objects.get(numero_credito=numero_credito)
        except Credito.DoesNotExist as exc:
            raise CommandError(f'No existe el credito {numero_credito}.') from exc

        fecha_forzada = None
        if options.get('fecha_primera_cuota'):
            try:
                fecha_forzada = datetime.strptime(options['fecha_primera_cuota'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('La fecha debe tener formato YYYY-MM-DD.') from exc

        credito.tipo_regla_credito = Credito.TipoReglaCredito.ESPECIAL
        credito.plazo_forzado = options.get('plazo') or credito.plazo_forzado
        credito.tasa_forzada = options.get('tasa') or credito.tasa_forzada
        credito.fecha_primera_cuota_forzada = fecha_forzada or credito.fecha_primera_cuota_forzada
        credito.observacion_regla_especial = options['observacion']
        credito.save(update_fields=[
            'tipo_regla_credito',
            'plazo_forzado',
            'tasa_forzada',
            'fecha_primera_cuota_forzada',
            'observacion_regla_especial',
        ])

        self.stdout.write(
            self.style.SUCCESS(
                f'Credito {credito.numero_credito} marcado como ESPECIAL '
                f'(plazo={credito.plazo_forzado or "-"}, tasa={credito.tasa_forzada or "-"}, '
                f'primera_cuota={credito.fecha_primera_cuota_forzada or "-"})'
            )
        )

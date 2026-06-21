from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from contractors.models import ConfiguracionPortalContratistas


VALORES_POLITICA_V2 = {
    'monto_minimo': Decimal('300000.00'),
    'monto_maximo': Decimal('10000000.00'),
    'plazo_maximo_meses': 8,
    'tasa_mensual': Decimal('2.2000'),
    'tasa_comision': Decimal('10.0000'),
    'comision_fija': Decimal('0.00'),
    'tasa_iva': Decimal('19.0000'),
    'tasa_fondo_garantia': Decimal('2.0000'),
    'iva_fondo_garantia': Decimal('19.0000'),
    'fondo_garantia_incluye_iva': True,
    'factor_seguro_vida': Decimal('0.003711'),
    'seguro_vida_financiado': True,
}


class Command(BaseCommand):
    help = 'Muestra o aplica la politica financiera V2 de prestadores a un host del portal contratistas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            required=True,
            help='Host exacto de ConfiguracionPortalContratistas que se desea revisar o actualizar.',
        )
        parser.add_argument(
            '--confirmar',
            action='store_true',
            help='Aplica los cambios. Sin este flag solo muestra diferencias.',
        )

    def handle(self, *args, **options):
        host = ConfiguracionPortalContratistas.normalizar_host(options['host'])
        try:
            configuracion = ConfiguracionPortalContratistas.objects.get(host=host)
        except ConfiguracionPortalContratistas.DoesNotExist as exc:
            raise CommandError(f'No existe ConfiguracionPortalContratistas para host={host}') from exc

        diferencias = self._resolver_diferencias(configuracion)
        if not diferencias:
            self.stdout.write(self.style.SUCCESS(f'El host {host} ya cumple la politica V2.'))
            return

        self.stdout.write(f'Politica V2 para host: {host}')
        for campo, valores in diferencias.items():
            self.stdout.write(f'- {campo}: actual={valores["actual"]} objetivo={valores["objetivo"]}')

        if not options['confirmar']:
            self.stdout.write(
                self.style.WARNING(
                    'No se aplicaron cambios. Ejecute nuevamente con --confirmar para actualizar este host.',
                ),
            )
            return

        for campo, valor in VALORES_POLITICA_V2.items():
            setattr(configuracion, campo, valor)
        configuracion.save(update_fields=[*VALORES_POLITICA_V2.keys(), 'updated_at'])
        self.stdout.write(self.style.SUCCESS(f'Politica V2 aplicada a {host}.'))

    def _resolver_diferencias(self, configuracion):
        diferencias = {}
        for campo, objetivo in VALORES_POLITICA_V2.items():
            actual = getattr(configuracion, campo)
            if actual != objetivo:
                diferencias[campo] = {
                    'actual': actual,
                    'objetivo': objetivo,
                }
        return diferencias

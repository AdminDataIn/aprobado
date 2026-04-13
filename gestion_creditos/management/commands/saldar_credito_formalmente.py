from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gestion_creditos.models import Credito
from gestion_creditos.services.credit_lifecycle import saldar_credito_formalmente


class Command(BaseCommand):
    help = 'Saldar formalmente un credito dejando cuotas y saldos en cero de forma consistente.'

    def add_arguments(self, parser):
        parser.add_argument('--credito', required=True, help='Numero de credito a saldar.')
        parser.add_argument('--motivo', default='Cierre administrativo controlado.', help='Motivo del cierre.')
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica el cierre. Sin este flag solo valida el credito objetivo.',
        )

    def handle(self, *args, **options):
        numero_credito = (options['credito'] or '').strip().upper()
        credito = Credito.objects.filter(numero_credito=numero_credito).first()
        if not credito:
            raise CommandError(f'No existe el credito {numero_credito}.')

        self.stdout.write(
            f'Credito: {credito.numero_credito} | Estado={credito.estado} | '
            f'Saldo={credito.saldo_pendiente} | Proximo pago={credito.fecha_proximo_pago}'
        )

        if not options['apply']:
            self.stdout.write('Dry-run: no se aplicaron cambios. Usa --apply para saldar formalmente.')
            return

        credito = saldar_credito_formalmente(
            credito,
            actor=None,
            motivo=options['motivo'],
            fecha_operacion=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Credito {credito.numero_credito} saldado. Estado={credito.estado} | '
                f'Saldo={credito.saldo_pendiente} | Proximo pago={credito.fecha_proximo_pago}'
            )
        )

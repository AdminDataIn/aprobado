from django.core.management.base import BaseCommand, CommandError

from gestion_creditos.models import NotificacionPagoBREB
from gestion_creditos.services.breb_notifications import reencolar_alerta_interna


class Command(BaseCommand):
    help = 'Consulta una alerta BRE-B y opcionalmente reencola solo PENDIENTE/FALLIDA; nunca INCIERTA.'

    def add_arguments(self, parser):
        parser.add_argument('evento_id', type=int)
        parser.add_argument('--confirmar', action='store_true')

    def handle(self, *args, **options):
        try:
            evento = NotificacionPagoBREB.objects.get(pk=options['evento_id'])
        except NotificacionPagoBREB.DoesNotExist:
            raise CommandError('Evento inexistente.')
        if options['confirmar'] and not reencolar_alerta_interna(evento.pk):
            raise CommandError('No se reenvian alertas ENVIADA/INCIERTA. Revisar evidencia SMTP.')
        evento.refresh_from_db()
        self.stdout.write(f'pago_breb_id={evento.pago_breb_id} estado={evento.estado} '
                          f'intento={evento.numero_intentos} codigo={evento.codigo_error or "OK"}')

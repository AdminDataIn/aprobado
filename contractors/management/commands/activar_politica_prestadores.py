from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from contractors.models import ConfiguracionScorePrestador
from contractors.services.politica_score import activar_politica_score_prestador


class Command(BaseCommand):
    help = 'Activa de forma transaccional y auditada una politica de score de prestadores.'

    def add_arguments(self, parser):
        # Django reserva --version para imprimir su propia version. En este
        # comando el nombre forma parte del contrato administrativo solicitado.
        for action in list(parser._actions):
            if '--version' not in action.option_strings:
                continue
            parser._remove_action(action)
            for group in parser._action_groups:
                if action in group._group_actions:
                    group._group_actions.remove(action)
            for option in action.option_strings:
                parser._option_string_actions.pop(option, None)
        parser.add_argument(
            '--version',
            required=True,
            help='Version exacta de la politica que se desea activar.',
        )
        parser.add_argument(
            '--motivo',
            required=True,
            help='Motivo administrativo obligatorio de la activacion.',
        )
        parser.add_argument(
            '--actor-username',
            required=True,
            help='Usuario existente que ejecuta la activacion y posee el permiso requerido.',
        )

    def handle(self, *args, **options):
        politica = ConfiguracionScorePrestador.objects.filter(
            version=options['version'],
        ).first()
        if politica is None:
            raise CommandError('No existe una politica con la version indicada.')

        User = get_user_model()
        actor = User.objects.filter(username=options['actor_username']).first()
        if actor is None:
            raise CommandError('El actor administrativo indicado no existe.')

        try:
            resultado = activar_politica_score_prestador(
                politica_id=politica.pk,
                actor=actor,
                motivo=options['motivo'],
            )
        except (ValidationError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc

        anterior = (
            resultado.politica_anterior.version
            if resultado.politica_anterior else 'ninguna'
        )
        estado = 'activada' if resultado.cambio_realizado else 'sin cambios'
        self.stdout.write(self.style.SUCCESS(
            f'Politica anterior={anterior}; '
            f'politica nueva={resultado.politica_nueva.version}; '
            f'estado={estado}; auditoria={resultado.auditoria.pk}.'
        ))

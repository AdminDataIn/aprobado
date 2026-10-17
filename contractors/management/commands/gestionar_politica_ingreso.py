"""Publicacion y adopcion explicitas, sin activacion global ni PII en salida."""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from contractors.models import ConfiguracionScorePrestador, ContractorApplication
from contractors.services.politica_ingreso_publicacion import (
    VERSION_INGRESO, adoptar_politica_ingreso, publicar_politica_ingreso,
)
from contractors.services.politica_score import _exigir_actor_autorizado


class Command(BaseCommand):
    help = 'Publica o adopta una politica de ingreso; solo escribe con --aplicar.'

    def add_arguments(self, parser):
        parser.add_argument('accion', choices=('publicar', 'adoptar'))
        parser.add_argument('--actor-id', required=True, type=int)
        parser.add_argument('--motivo', required=True)
        parser.add_argument('--origen-id', type=int)
        parser.add_argument('--vigente-desde', help='Fecha explicita YYYY-MM-DD para publicar.')
        parser.add_argument('--descuento', default='25.00')
        parser.add_argument('--solicitud-id', type=int)
        parser.add_argument('--politica-id', type=int)
        modo = parser.add_mutually_exclusive_group()
        modo.add_argument('--aplicar', action='store_true')
        modo.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not options['motivo'].strip():
            raise CommandError('estado=MOTIVO_REQUERIDO')
        try:
            actor = get_user_model().objects.get(pk=options['actor_id'])
            _exigir_actor_autorizado(actor)
            if options['accion'] == 'publicar':
                self._publicar(actor, options)
            else:
                self._adoptar(actor, options)
        except ObjectDoesNotExist:
            raise CommandError('estado=ID_INEXISTENTE') from None
        except PermissionDenied:
            raise CommandError('estado=PERMISO_DENEGADO') from None
        except ValidationError:
            raise CommandError('estado=OPERACION_RECHAZADA') from None

    def _publicar(self, actor, options):
        if options['origen_id'] is None or not options['vigente_desde']:
            raise CommandError('estado=ORIGEN_Y_FECHA_REQUERIDOS')
        if options['solicitud_id'] is not None or options['politica_id'] is not None:
            raise CommandError('estado=ARGUMENTOS_INCOMPATIBLES')
        try:
            fecha = date.fromisoformat(options['vigente_desde'])
        except (TypeError, ValueError):
            raise CommandError('estado=FECHA_INVALIDA') from None
        if fecha.isoformat() != options['vigente_desde']:
            raise CommandError('estado=FECHA_INVALIDA')
        origen = ConfiguracionScorePrestador.objects.get(pk=options['origen_id'])
        if not options['aplicar']:
            # No invocar servicios de escritura, ni siquiera dentro de un rollback.
            self.stdout.write(f'estado=DRY_RUN_NO_VALIDADO origen_id={origen.pk}')
            return
        nueva = publicar_politica_ingreso(
            origen_id=origen.pk, actor=actor, motivo=options['motivo'],
            vigente_desde=fecha, descuento=options['descuento'],
        )
        self.stdout.write(f'estado=PUBLICADA_INACTIVA politica_id={nueva.pk}')

    def _adoptar(self, actor, options):
        if options['solicitud_id'] is None or options['politica_id'] is None:
            raise CommandError('estado=SOLICITUD_Y_POLITICA_REQUERIDAS')
        if options['origen_id'] is not None or options['vigente_desde']:
            raise CommandError('estado=ARGUMENTOS_INCOMPATIBLES')
        if not actor.has_perm('contractors.can_evaluate_contractor_application'):
            raise PermissionDenied
        politica = ConfiguracionScorePrestador.objects.get(pk=options['politica_id'])
        if (politica.version != VERSION_INGRESO
                or politica.version_politica != VERSION_INGRESO):
            raise CommandError('estado=POLITICA_NO_CORRESPONDE')
        solicitud = ContractorApplication.objects.get(pk=options['solicitud_id'])
        if not options['aplicar']:
            self.stdout.write(
                f'estado=DRY_RUN_NO_VALIDADO solicitud_id={solicitud.pk} politica_id={politica.pk}'
            )
            return
        resultado = adoptar_politica_ingreso(
            solicitud_id=solicitud.pk, politica_id=politica.pk,
            actor=actor, motivo=options['motivo'],
        )
        self.stdout.write(f'estado=ADOPTADA solicitud_id={resultado.pk} politica_id={politica.pk}')

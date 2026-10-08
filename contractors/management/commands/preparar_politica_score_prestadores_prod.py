import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError

from contractors.services.preparacion_score_prod import extraer_parametros_score_prod, preparar_politica_score_prod


class Command(BaseCommand):
    help = 'Valida SCORE PROD en dry-run o persiste INACTIVA con actor autorizado; nunca activa.'

    def add_arguments(self, parser):
        parser.add_argument('--parametros', help='JSON de parametros explicitamente ratificados.')
        parser.add_argument('--fecha-vigencia', help='Fecha autorizada YYYY-MM-DD; no activa la politica.')
        parser.add_argument('--persistir-inactiva', action='store_true', help='Persistencia inactiva explicita.')
        parser.add_argument('--actor-id', type=int, help='PK de usuario Django autorizado para persistir.')
        parser.add_argument('--motivo', help='Motivo auditable obligatorio para persistir.')

    def handle(self, *args, **options):
        try:
            documento = json.loads(Path(options['parametros']).read_text(encoding='utf-8')) if options['parametros'] else {}
            parametros = extraer_parametros_score_prod(documento)
            if options['fecha_vigencia']:
                parametros['fecha_vigencia_desde'] = options['fecha_vigencia']
            actor = None
            if options['persistir_inactiva']:
                if not options['actor_id'] or not (options['motivo'] or '').strip():
                    raise CommandError('--persistir-inactiva exige --actor-id y --motivo no vacio.')
                actor = get_user_model().objects.get(pk=options['actor_id'])
            elif options['actor_id'] is not None or options['motivo'] is not None:
                raise CommandError('--actor-id y --motivo requieren --persistir-inactiva.')
            resultado = preparar_politica_score_prod(
                parametros=parametros, persistir=options['persistir_inactiva'],
                actor=actor, motivo=options['motivo'] or '',
            )
            if options['persistir_inactiva']:
                resultado = {
                    'politica_id': resultado.pk, 'version': resultado.version,
                    'activa': resultado.activa, 'persistida': True, 'pendientes': [],
                    'fecha_vigencia_desde': resultado.fecha_vigencia_desde.isoformat(),
                    'configuracion_financiera_id': resultado.configuracion_financiera_id,
                    'bandas': resultado.bandas.count(),
                }
        except PermissionDenied as exc:
            raise CommandError('Persistencia inactiva no autorizada: exige staff con permiso y motivo.') from exc
        except ValidationError as exc:
            raise CommandError('Definicion de politica invalida: ' + '; '.join(exc.messages)) from exc
        except (OSError, ValueError, TypeError, ObjectDoesNotExist) as exc:
            raise CommandError('Definicion o actor inexistente/invalido.') from exc
        self.stdout.write(json.dumps(resultado, ensure_ascii=True, default=str))

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from contractors.services.preparacion_score_prod import preparar_politica_score_prod


class Command(BaseCommand):
    help = 'Valida la definicion SCORE PROD en dry-run; no persiste ni activa politicas.'

    def add_arguments(self, parser):
        parser.add_argument('--parametros', help='JSON de parametros explicitamente ratificados.')

    def handle(self, *args, **options):
        try:
            parametros = json.loads(Path(options['parametros']).read_text(encoding='utf-8')) if options['parametros'] else None
            resultado = preparar_politica_score_prod(parametros=parametros)
        except (OSError, ValueError, TypeError, ValidationError, ObjectDoesNotExist) as exc:
            raise CommandError('Definicion de politica incompleta o invalida.') from exc
        self.stdout.write(json.dumps(resultado, ensure_ascii=True, default=str))

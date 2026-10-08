import json

from django.core.management.base import BaseCommand

from integrations.datacredito.readiness import evaluar_readiness_datacredito


class Command(BaseCommand):
    help = 'Audita configuracion DataCredito local sin HTTP/DNS, escrituras o activacion.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Salida estructurada, sin valores secretos.')

    def handle(self, *args, **options):
        resultado = evaluar_readiness_datacredito()
        if options['json']:
            self.stdout.write(json.dumps(resultado, ensure_ascii=True))
            return
        for seccion, requisitos in resultado['secciones'].items():
            self.stdout.write(seccion)
            for requisito in requisitos:
                estado = requisito['estado']
                if estado == 'DEFAULT_NO_CONFIRMADO':
                    estado += ' / PENDIENTE_CONFIRMACION'
                detalle = requisito['mensaje']
                if 'url' in requisito:
                    detalle += ' ' + requisito['url']
                if 'habilitado' in requisito:
                    detalle = 'True' if requisito['habilitado'] else 'False'
                self.stdout.write(f"  {requisito['nombre']}: {estado} - {detalle}")
        self.stdout.write('RESULTADO: ' + resultado['resultado'] + ' / ' + resultado['estado'])

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from contractors.models import ContractorApplication
from contractors.services.archivo_solicitud import bloqueos_archivo, exigir_archivista


IDS = tuple(range(1, 11))
EMPRESA = 'PRUEBAS DATAIN'


class Command(BaseCommand):
    help = 'Solo lectura: verifica la elegibilidad de archivo de PRUEBAS DATAIN #1 a #10.'

    def add_arguments(self, parser):
        parser.add_argument('--empresa-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int, required=True)

    @transaction.atomic
    def handle(self, *args, empresa_id, actor_id, **options):
        if connection.vendor == 'postgresql' and len(connection.atomic_blocks) == 1:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cursor.execute('SET LOCAL row_security = off')
        actor = get_user_model().objects.filter(pk=actor_id).first()
        try:
            exigir_archivista(actor)
        except PermissionDenied as exc:
            raise CommandError('ACTOR_NO_AUTORIZADO') from exc
        solicitudes = list(ContractorApplication.objects.select_related('empresa', 'archivo_operativo')
            .filter(pk__in=IDS, empresa_id=empresa_id, empresa__nombre=EMPRESA).order_by('pk'))
        if len(solicitudes) != len(IDS) or any(s.empresa.nombre != EMPRESA for s in solicitudes):
            raise CommandError('LOTE_INCOMPLETO_O_FUERA_DE_EMPRESA')
        filas = []
        for solicitud in solicitudes:
            archivada = hasattr(solicitud, 'archivo_operativo')
            bloqueos = [] if archivada else bloqueos_archivo(solicitud)
            filas.append({'id': solicitud.pk, 'bloqueos': bloqueos, 'resultado': (
                'ARCHIVADA' if archivada else 'BLOCKED' if bloqueos else 'ELEGIBLE')})
        self.stdout.write(json.dumps({'modo': 'DRY_RUN', 'empresa_id': empresa_id, 'solicitudes': filas}))

import json
from copy import copy
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from contractors.models import ConfiguracionSimuladorPrestador
from contractors.services.capacidad_contractual import obtener_configuracion_simulador_prestador


CAMPOS = (
    'version', 'monto_minimo', 'monto_maximo', 'plazo_minimo_meses',
    'plazo_maximo_meses', 'tasa_mensual', 'porcentaje_originacion',
    'porcentaje_iva_originacion', 'porcentaje_fondo_garantia',
    'porcentaje_seguro_vida_primera_cuota',
)


class Command(BaseCommand):
    help = 'Valida y, con --aplicar, habilita solo el simulador. No crea ni activa score.'

    def add_arguments(self, parser):
        parser.add_argument('--archivo', required=True, help='JSON con todos los parametros aprobados.')
        parser.add_argument('--aplicar', action='store_true', help='Persistir; por defecto solo valida.')

    def handle(self, *args, **options):
        try:
            datos = json.loads(Path(options['archivo']).read_text(encoding='utf-8'), parse_float=Decimal)
        except (OSError, ValueError):
            raise CommandError('No se pudo leer un JSON valido.') from None
        if not isinstance(datos, dict) or set(datos) != set(CAMPOS):
            raise CommandError('El JSON debe contener exactamente: ' + ', '.join(CAMPOS))
        if not isinstance(datos['version'], str) or not datos['version'].strip():
            raise CommandError('Se requiere una version no vacia.')
        try:
            with transaction.atomic():
                actuales = list(ConfiguracionSimuladorPrestador.objects.select_for_update())
                actual = next((item for item in actuales if item.version == datos['version']), None)
                if any(item.activo and item != actual for item in actuales):
                    raise CommandError('Existe otra configuracion activa. Resolver en Admin; no se reemplaza.')
                candidata = copy(actual) if actual else ConfiguracionSimuladorPrestador()
                for campo, valor in datos.items():
                    setattr(candidata, campo, valor)
                candidata.activo = True
                candidata.full_clean()
                for campo in CAMPOS[1:]:
                    valor = getattr(candidata, campo)
                    if valor < 0 or (campo.startswith(('monto_', 'plazo_')) and valor == 0):
                        raise CommandError('Montos/plazos deben ser positivos y porcentajes no negativos.')
                if actual and any(getattr(actual, campo) != getattr(candidata, campo) for campo in CAMPOS):
                    raise CommandError('La version existe con otros valores. No se modifica su significado.')
                if not actual:
                    candidata.save()
                elif not actual.activo:
                    actual.activo = True
                    actual.save(update_fields=['activo', 'updated_at'])
                efectiva = obtener_configuracion_simulador_prestador()
                if efectiva is None or efectiva.pk != candidata.pk:
                    raise CommandError('La politica vigente no permite esta configuracion. Revisar su enlace en Admin.')
                if not options['aplicar']:
                    transaction.set_rollback(True)
        except (ValidationError, IntegrityError, TypeError, ValueError) as exc:
            raise CommandError('Configuracion invalida o conflicto concurrente; no se guardaron cambios.') from exc
        self.stdout.write(self.style.SUCCESS(
            'Simulador habilitado; score sin cambios.' if options['aplicar']
            else 'Configuracion valida. Validacion revertida; use --aplicar para persistir.'
        ))

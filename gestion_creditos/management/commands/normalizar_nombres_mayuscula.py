from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from gestion_creditos.models import CreditoEmprendimiento, CreditoLibranza, VinculoLaboralEmpresa
from gestion_creditos.services.name_normalization import build_full_name_upper, normalize_name_upper


class Command(BaseCommand):
    help = (
        'Normaliza nombres existentes a MAYUSCULA en usuarios, vinculos laborales y '
        'detalles de credito. Usa dry-run por defecto.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios en base de datos. Sin este flag solo reporta.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = bool(options['apply'])
        User = get_user_model()
        counters = {
            'users': 0,
            'vinculos': 0,
            'libranza': 0,
            'emprendimiento': 0,
        }

        for user in User.objects.all().only('id', 'first_name', 'last_name'):
            first_name = normalize_name_upper(user.first_name)
            last_name = normalize_name_upper(user.last_name)
            if first_name != (user.first_name or '') or last_name != (user.last_name or ''):
                counters['users'] += 1
                if apply_changes:
                    user.first_name = first_name
                    user.last_name = last_name
                    user.save(update_fields=['first_name', 'last_name'])

        for vinculo in VinculoLaboralEmpresa.objects.all().only('id', 'nombre_empleado'):
            nombre_empleado = normalize_name_upper(vinculo.nombre_empleado)
            if nombre_empleado != (vinculo.nombre_empleado or ''):
                counters['vinculos'] += 1
                if apply_changes:
                    vinculo.nombre_empleado = nombre_empleado
                    vinculo.save(update_fields=['nombre_empleado'])

        for detalle in CreditoLibranza.objects.all().only('id', 'nombres', 'apellidos'):
            nombres = normalize_name_upper(detalle.nombres)
            apellidos = normalize_name_upper(detalle.apellidos)
            if nombres != (detalle.nombres or '') or apellidos != (detalle.apellidos or ''):
                counters['libranza'] += 1
                if apply_changes:
                    detalle.nombres = nombres
                    detalle.apellidos = apellidos
                    detalle.save(update_fields=['nombres', 'apellidos'])

        for detalle in CreditoEmprendimiento.objects.all().only('id', 'nombre'):
            nombre = normalize_name_upper(detalle.nombre)
            if nombre != (detalle.nombre or ''):
                counters['emprendimiento'] += 1
                if apply_changes:
                    detalle.nombre = nombre
                    detalle.save(update_fields=['nombre'])

        mode = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.SUCCESS(f'Normalizacion de nombres completada en modo {mode}.'))
        self.stdout.write(f"Usuarios a corregir: {counters['users']}")
        self.stdout.write(f"Vinculos laborales a corregir: {counters['vinculos']}")
        self.stdout.write(f"Detalles de libranza a corregir: {counters['libranza']}")
        self.stdout.write(f"Detalles de emprendimiento a corregir: {counters['emprendimiento']}")

        if not apply_changes:
            self.stdout.write('No se aplicaron cambios. Usa --apply para persistirlos.')

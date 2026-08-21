from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from gestion_creditos.models import Credito
from gestion_creditos.services.anulacion_credito import (
    ESTADOS_ANULABLES_POR_ERROR_DATOS,
    anular_credito_por_error_datos,
)


class Command(BaseCommand):
    help = 'Anula administrativamente un credito por error de datos. Opera en dry-run por defecto.'

    def add_arguments(self, parser):
        parser.add_argument('--numero-credito', required=True)
        parser.add_argument('--motivo', required=True)
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica la anulacion. Sin esta bandera solo muestra el diagnostico.',
        )

    def handle(self, *args, **options):
        numero_credito = options['numero_credito'].strip()
        motivo = options['motivo'].strip()
        if not motivo:
            raise CommandError('El motivo no puede estar vacio.')

        try:
            credito = Credito.objects.select_related('usuario').get(
                numero_credito=numero_credito
            )
        except Credito.DoesNotExist as exc:
            raise CommandError(f'No existe el credito {numero_credito}.') from exc

        pagare = getattr(credito, 'pagare', None)
        aprobaciones = list(
            credito.aprobaciones_pagador_libranza
            .order_by('created_at')
            .values('id', 'nivel', 'decision', 'usuario_id', 'created_at')
        )

        self.stdout.write('Diagnostico de anulacion administrativa')
        self.stdout.write(f'credito={credito.numero_credito} | id={credito.pk}')
        self.stdout.write(f'estado_actual={credito.estado}')
        self.stdout.write(
            f'usuario_id={credito.usuario_id} | email={credito.usuario.email or "sin_email"}'
        )
        self.stdout.write(f'linea={credito.linea}')
        self.stdout.write(
            'pagare=' + (
                f'id={pagare.pk} | estado={pagare.estado}' if pagare else 'no_asociado'
            )
        )
        self.stdout.write(f'aprobaciones_pagador={len(aprobaciones)}')
        for aprobacion in aprobaciones:
            self.stdout.write(
                '  '
                f'id={aprobacion["id"]} | nivel={aprobacion["nivel"]} | '
                f'decision={aprobacion["decision"]} | usuario_id={aprobacion["usuario_id"]} | '
                f'fecha={aprobacion["created_at"].isoformat()}'
            )
        self.stdout.write(f'motivo={motivo}')

        if not options['apply']:
            if credito.estado == Credito.EstadoCredito.ANULADO:
                self.stdout.write(self.style.WARNING(
                    'Dry-run: el credito ya esta ANULADO. No se ejecutaria ninguna escritura.'
                ))
                return
            if (
                credito.linea != Credito.LineaCredito.LIBRANZA
                or credito.estado not in ESTADOS_ANULABLES_POR_ERROR_DATOS
            ):
                self.stdout.write(self.style.ERROR(
                    'Dry-run: anulacion bloqueada por linea o estado actual.'
                ))
                return
            self.stdout.write(self.style.WARNING(
                'Dry-run: se cambiaria el credito a ANULADO y el pagare CREATED/SENT a CANCELLED.'
            ))
            self.stdout.write('No se aplicaron cambios. Usa --apply para confirmar.')
            return

        if credito.estado == Credito.EstadoCredito.ANULADO:
            self.stdout.write(self.style.WARNING('El credito ya esta ANULADO. No se realizaron cambios.'))
            return

        try:
            resultado = anular_credito_por_error_datos(
                credito=credito,
                actor=None,
                motivo=motivo,
            )
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc

        self.stdout.write(self.style.SUCCESS(
            f'{resultado.numero_credito}: {resultado.estado_anterior} -> {resultado.estado_nuevo}.'
        ))
        if resultado.pagare_id:
            self.stdout.write(
                f'pagare_id={resultado.pagare_id} | '
                f'{resultado.pagare_estado_anterior} -> {resultado.pagare_estado_nuevo}'
            )

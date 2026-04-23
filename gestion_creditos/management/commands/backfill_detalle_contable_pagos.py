from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos.services.accounting import (
    backfill_accounting_for_credito,
    get_platform_disbursed_creditos_queryset,
)


class Command(BaseCommand):
    help = (
        'Backfillea DetalleContablePago para creditos desembolsados por plataforma '
        'que tengan flujo completo de transferencia y desembolso confirmado.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--credito-id',
            type=int,
            help='Procesa solo un credito especifico.',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Regenera el detalle contable aunque ya exista.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        creditos = get_platform_disbursed_creditos_queryset().order_by('id')
        credito_id = options.get('credito_id')
        overwrite = options.get('overwrite', False)

        if credito_id:
            creditos = creditos.filter(id=credito_id)

        if not creditos.exists():
            raise CommandError('No se encontraron creditos elegibles para backfill contable.')

        procesados = 0
        omitidos = 0
        total_pagos = 0
        total_detalles = 0

        for credito in creditos:
            resultado = backfill_accounting_for_credito(credito=credito, overwrite=overwrite)
            if resultado['omitido']:
                omitidos += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Omitido {credito.numero_credito}: ya tiene detalle contable."
                    )
                )
                continue

            procesados += 1
            total_pagos += resultado['pagos_procesados']
            total_detalles += resultado['detalles_creados']
            self.stdout.write(
                self.style.SUCCESS(
                    f"{credito.numero_credito}: pagos={resultado['pagos_procesados']} "
                    f"detalles={resultado['detalles_creados']}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill contable completado. creditos={procesados} omitidos={omitidos} "
                f"pagos={total_pagos} detalles={total_detalles}"
            )
        )

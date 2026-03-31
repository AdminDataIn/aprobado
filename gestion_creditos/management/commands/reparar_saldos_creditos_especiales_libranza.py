import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, Empresa


class Command(BaseCommand):
    help = (
        'Recalcula saldo pendiente, capital pendiente, próxima fecha de pago y estado '
        'de créditos especiales de libranza usando la tabla de amortización como fuente de verdad.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--empresa-nombre',
            dest='empresa_nombre',
            help='Nombre exacto de la empresa para filtrar los créditos a reparar.',
        )
        parser.add_argument(
            '--numero-credito',
            dest='numeros_credito',
            nargs='*',
            help='Números de crédito específicos a reparar.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios. Sin esta bandera solo hace dry-run.',
        )
        parser.add_argument(
            '--report-file',
            dest='report_file',
            help='Ruta opcional para guardar un reporte JSON.',
        )

    def handle(self, *args, **options):
        empresa_nombre = options.get('empresa_nombre')
        numeros_credito = options.get('numeros_credito') or []
        apply_changes = options.get('apply', False)
        report_file = options.get('report_file')

        if not empresa_nombre and not numeros_credito:
            raise CommandError('Debe indicar --empresa-nombre o al menos un --numero-credito.')

        queryset = Credito.objects.filter(
            linea=Credito.LineaCredito.LIBRANZA,
            tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL,
        ).select_related('detalle_libranza__empresa')

        if empresa_nombre:
            empresa = Empresa.objects.filter(nombre__iexact=empresa_nombre).first()
            if not empresa:
                raise CommandError(f'No se encontró la empresa "{empresa_nombre}".')
            queryset = queryset.filter(detalle_libranza__empresa=empresa)

        if numeros_credito:
            queryset = queryset.filter(numero_credito__in=numeros_credito)

        creditos = list(queryset.order_by('numero_credito').distinct())
        if not creditos:
            raise CommandError('No se encontraron créditos especiales de libranza con ese criterio.')

        self.stdout.write(
            self.style.WARNING('Apply mode activo.' if apply_changes else 'Dry-run: no se aplicarán cambios.')
        )

        resultados = []
        with transaction.atomic():
            for credito in creditos:
                anterior = {
                    'saldo_pendiente': str(credito.saldo_pendiente or 0),
                    'capital_pendiente': str(credito.capital_pendiente or 0),
                    'fecha_proximo_pago': credito.fecha_proximo_pago.isoformat() if credito.fecha_proximo_pago else None,
                    'estado': credito.estado,
                }
                resumen = credit_services.recalcular_credito_desde_tabla_amortizacion(
                    credito,
                    persist=apply_changes,
                )
                resultado = {
                    'numero_credito': credito.numero_credito,
                    'empresa': getattr(credito.empresa_relacionada, 'nombre', ''),
                    'antes': anterior,
                    'despues': {
                        'saldo_pendiente': str(resumen['saldo_pendiente']),
                        'capital_pendiente': str(resumen['capital_pendiente']),
                        'fecha_proximo_pago': (
                            resumen['fecha_proximo_pago'].isoformat()
                            if resumen['fecha_proximo_pago']
                            else None
                        ),
                        'estado': Credito.EstadoCredito.PAGADO if resumen['cuotas_restantes'] == 0 else Credito.EstadoCredito.ACTIVO,
                    },
                    'cuotas_pagadas': resumen['cuotas_pagadas'],
                    'cuotas_restantes': resumen['cuotas_restantes'],
                    'total_pagado': str(resumen['total_pagado']),
                    'fuente': resumen['fuente'],
                    'aplicado': apply_changes,
                }
                resultados.append(resultado)
                self.stdout.write(
                    f"{credito.numero_credito}: saldo {anterior['saldo_pendiente']} -> {resultado['despues']['saldo_pendiente']} | "
                    f"capital {anterior['capital_pendiente']} -> {resultado['despues']['capital_pendiente']} | "
                    f"proximo {anterior['fecha_proximo_pago']} -> {resultado['despues']['fecha_proximo_pago']}"
                )

            if not apply_changes:
                transaction.set_rollback(True)

        if report_file:
            path = Path(report_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        'mode': 'apply' if apply_changes else 'dry-run',
                        'creditos': resultados,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )
            self.stdout.write(self.style.SUCCESS(f'Reporte guardado en {path}'))

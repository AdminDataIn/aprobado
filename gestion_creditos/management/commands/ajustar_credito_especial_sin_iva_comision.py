import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos import credit_services
from gestion_creditos.models import Credito


class Command(BaseCommand):
    help = (
        'Quita el IVA de la comision de un credito especial especifico y '
        'regenera su plan de pagos de forma consistente.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--numero-credito',
            required=True,
            help='Numero exacto del credito a ajustar.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica el ajuste. Sin esta bandera solo hace dry-run.',
        )
        parser.add_argument(
            '--report-file',
            help='Ruta opcional para guardar un reporte JSON.',
        )

    def handle(self, *args, **options):
        numero_credito = options['numero_credito'].strip()
        apply_changes = options['apply']
        report_file = options.get('report_file')

        try:
            credito = (
                Credito.objects
                .select_related('detalle_libranza')
                .prefetch_related('tabla_amortizacion', 'historial_pagos', 'wompi_intentos')
                .get(numero_credito=numero_credito)
            )
        except Credito.DoesNotExist as exc:
            raise CommandError(f'No existe el credito {numero_credito}.') from exc

        self.stdout.write(
            self.style.WARNING('Apply mode activo.' if apply_changes else 'Dry-run: no se aplicaran cambios.')
        )

        try:
            with transaction.atomic():
                if apply_changes:
                    credito = (
                        Credito.objects
                        .select_for_update()
                        .select_related('detalle_libranza')
                        .prefetch_related('tabla_amortizacion', 'historial_pagos', 'wompi_intentos')
                        .get(pk=credito.pk)
                    )

                antes = {
                    'numero_credito': credito.numero_credito,
                    'linea': credito.linea,
                    'estado': credito.estado,
                    'tipo_regla_credito': credito.tipo_regla_credito,
                    'monto_aprobado': str(credito.monto_aprobado or 0),
                    'plazo': credito.plazo,
                    'plazo_forzado': credito.plazo_forzado,
                    'tasa_interes': str(credito.tasa_interes or 0),
                    'tasa_forzada': str(credito.tasa_forzada or 0),
                    'comision': str(credito.comision or 0),
                    'iva_comision': str(credito.iva_comision or 0),
                    'valor_cuota': str(credito.valor_cuota or 0),
                    'total_a_pagar': str(credito.total_a_pagar or 0),
                    'saldo_pendiente': str(credito.saldo_pendiente or 0),
                    'fecha_proximo_pago': credito.fecha_proximo_pago.isoformat() if credito.fecha_proximo_pago else None,
                    'fecha_primera_cuota_forzada': (
                        credito.fecha_primera_cuota_forzada.isoformat()
                        if credito.fecha_primera_cuota_forzada else None
                    ),
                    'cuotas_existentes': credito.tabla_amortizacion.count(),
                    'cuotas_pagadas': credito.tabla_amortizacion.filter(pagada=True).count(),
                    'historial_pagos': credito.historial_pagos.count(),
                    'wompi_intentos': credito.wompi_intentos.count(),
                }

                resultado = credit_services.recalcular_credito_especial_sin_iva_comision(
                    credito,
                    persist=apply_changes,
                )

                despues = {
                    'comision': str(resultado['comision_actual']),
                    'iva_comision': str(resultado['iva_nuevo']),
                    'capital_financiado': str(resultado['capital_financiado_nuevo']),
                    'valor_cuota': str(resultado['valor_cuota_nuevo']),
                    'total_a_pagar': str(resultado['total_a_pagar_nuevo']),
                    'saldo_pendiente': str(resultado['saldo_pendiente_nuevo']),
                    'capital_pendiente': str(resultado['capital_pendiente_nuevo']),
                    'fecha_primera_cuota': resultado['fecha_primera_cuota'].isoformat(),
                    'fecha_proximo_pago': resultado['fecha_proximo_pago_nueva'].isoformat(),
                    'plazo_aplicado': resultado['plazo_aplicado'],
                    'tasa_aplicada': str(resultado['tasa_aplicada']),
                    'cuotas_regeneradas': len(resultado['cuotas_generadas']),
                }

                if not apply_changes:
                    transaction.set_rollback(True)

        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = {
            'mode': 'apply' if apply_changes else 'dry-run',
            'credito': numero_credito,
            'antes': antes,
            'despues': despues,
        }

        self.stdout.write(
            f"{numero_credito}: IVA {antes['iva_comision']} -> {despues['iva_comision']} | "
            f"cuota {antes['valor_cuota']} -> {despues['valor_cuota']} | "
            f"total {antes['total_a_pagar']} -> {despues['total_a_pagar']}"
        )

        if report_file:
            path = Path(report_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'Reporte guardado en {path}'))

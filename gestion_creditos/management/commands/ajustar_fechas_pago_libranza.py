import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import Credito, HistorialPago
from gestion_creditos.services.libranza_rules import (
    calcular_primera_fecha_pago_libranza,
    reprogramar_cuotas_pendientes,
)


class Command(BaseCommand):
    help = (
        'Reprograma la fecha de la primera cuota y las cuotas pendientes de creditos '
        'de libranza especificos. Excluye explicitamente el credito especial por numero.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--creditos',
            nargs='+',
            required=True,
            help='Numeros de credito a ajustar.',
        )
        parser.add_argument(
            '--excluir-creditos',
            nargs='*',
            default=[],
            help='Numeros de credito a excluir explicitamente, por ejemplo el credito especial.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios. Sin esta bandera solo hace dry-run.',
        )
        parser.add_argument(
            '--report-file',
            help='Ruta opcional para guardar un reporte JSON con el diagnostico y/o resultado.',
        )

    def handle(self, *args, **options):
        numeros = options['creditos']
        excluidos = set(options['excluir_creditos'] or [])
        apply_changes = options['apply']
        report_file = options.get('report_file')

        creditos = list(
            Credito.objects.filter(numero_credito__in=numeros, linea=Credito.LineaCredito.LIBRANZA)
            .prefetch_related('tabla_amortizacion', 'historial_pagos', 'reestructuraciones')
            .order_by('numero_credito')
        )
        encontrados = {credito.numero_credito for credito in creditos}
        faltantes = [numero for numero in numeros if numero not in encontrados]
        if faltantes:
            raise CommandError(f'No se encontraron estos creditos de libranza: {", ".join(faltantes)}')

        self.stdout.write(self.style.WARNING('Modo apply activo.' if apply_changes else 'Dry-run: no se aplicaran cambios.'))
        report_rows = []

        with transaction.atomic():
            for credito in creditos:
                analisis = self._analizar_credito(credito, excluidos)
                fecha_base = credito.fecha_desembolso.date() if credito.fecha_desembolso else credito.fecha_solicitud.date()
                nueva_fecha = calcular_primera_fecha_pago_libranza(fecha_aprobacion=fecha_base)
                analisis['fecha_base'] = fecha_base
                analisis['nueva_fecha'] = nueva_fecha
                report_rows.append(analisis)
                self._imprimir_analisis(analisis)

                if apply_changes and analisis['puede_aplicar']:
                    reprogramar_cuotas_pendientes(credito, nueva_fecha)
                    analisis['aplicado'] = True
                    self.stdout.write(self.style.SUCCESS(f"{credito.numero_credito}: ajuste aplicado correctamente."))
                elif apply_changes:
                    analisis['aplicado'] = False

            if not apply_changes:
                transaction.set_rollback(True)

        if report_file:
            self._guardar_reporte(report_file, report_rows, apply_changes)

    def _analizar_credito(self, credito, excluidos):
        cuotas_pagadas = list(credito.tabla_amortizacion.filter(pagada=True).order_by('numero_cuota'))
        cuotas_pendientes = list(credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota'))
        pagos_exitosos = list(credito.historial_pagos.filter(estado=HistorialPago.EstadoPago.EXITOSO))
        reestructuraciones = list(credito.reestructuraciones.all())
        cuota_pendiente = cuotas_pendientes[0] if cuotas_pendientes else None
        fecha_actual = cuota_pendiente.fecha_vencimiento if cuota_pendiente else credito.fecha_proximo_pago
        motivos = []

        if credito.numero_credito in excluidos:
            motivos.append('Exclusion explicita')
        if credito.tipo_regla_credito == Credito.TipoReglaCredito.ESPECIAL:
            motivos.append('Credito especial')
        if cuotas_pagadas:
            motivos.append(f'{len(cuotas_pagadas)} cuota(s) pagada(s)')
        if pagos_exitosos:
            motivos.append(f'{len(pagos_exitosos)} pago(s) exitoso(s)')
        if reestructuraciones:
            motivos.append(f'{len(reestructuraciones)} reestructuracion(es)')
        if not cuotas_pendientes:
            motivos.append('Sin cuotas pendientes')

        return {
            'numero_credito': credito.numero_credito,
            'linea': credito.linea,
            'estado': credito.estado,
            'tipo_regla_credito': credito.tipo_regla_credito,
            'fecha_actual': fecha_actual,
            'cuotas_pendientes': len(cuotas_pendientes),
            'cuotas_pagadas': len(cuotas_pagadas),
            'pagos_exitosos': len(pagos_exitosos),
            'reestructuraciones': len(reestructuraciones),
            'motivos_bloqueo': motivos,
            'puede_aplicar': not motivos,
            'aplicado': False,
        }

    def _imprimir_analisis(self, analisis):
        self.stdout.write(
            (
                f"{analisis['numero_credito']}: "
                f"linea={analisis['linea']} | estado={analisis['estado']} | "
                f"regla={analisis['tipo_regla_credito']} | "
                f"fecha_base={analisis['fecha_base']} | "
                f"actual={analisis['fecha_actual']} | nueva={analisis['nueva_fecha']} | "
                f"pendientes={analisis['cuotas_pendientes']} | "
                f"pagadas={analisis['cuotas_pagadas']} | "
                f"pagos={analisis['pagos_exitosos']} | "
                f"reestructuraciones={analisis['reestructuraciones']}"
            )
        )
        if analisis['puede_aplicar']:
            self.stdout.write(self.style.SUCCESS(f"{analisis['numero_credito']}: listo para aplicar."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{analisis['numero_credito']}: omitido -> {', '.join(analisis['motivos_bloqueo'])}"
                )
            )

    def _guardar_reporte(self, report_file, report_rows, apply_changes):
        path = Path(report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'generated_at': timezone.now().isoformat(),
            'mode': 'apply' if apply_changes else 'dry-run',
            'creditos': report_rows,
        }
        path.write_text(json.dumps(payload, default=str, ensure_ascii=True, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Reporte guardado en {path}'))

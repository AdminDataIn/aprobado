import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos.models import Credito
from gestion_creditos.services.libranza_rules import (
    obtener_dia_ancla_vencimiento,
    sumar_meses_con_dia_ancla,
)


class Command(BaseCommand):
    help = (
        'Reancla el cronograma de créditos de libranza al día original de la primera cuota '
        'para evitar arrastres de fecha después de febrero.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--empresa-nombre', help='Empresa exacta para filtrar créditos de libranza.')
        parser.add_argument('--creditos', nargs='*', help='Números de crédito específicos a reparar.')
        parser.add_argument('--solo-especiales', action='store_true', help='Limita el ajuste a créditos especiales.')
        parser.add_argument('--apply', action='store_true', help='Aplica la reparación. Sin esta bandera solo hace dry-run.')
        parser.add_argument('--report-file', help='Ruta opcional para guardar reporte JSON.')

    def handle(self, *args, **options):
        queryset = Credito.objects.filter(linea=Credito.LineaCredito.LIBRANZA).prefetch_related('tabla_amortizacion')

        if options.get('empresa_nombre'):
            queryset = queryset.filter(detalle_libranza__empresa__nombre__iexact=options['empresa_nombre'])

        if options.get('creditos'):
            queryset = queryset.filter(numero_credito__in=options['creditos'])

        if options.get('solo_especiales'):
            queryset = queryset.filter(tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL)

        creditos = list(queryset.distinct().order_by('numero_credito'))
        if not creditos:
            raise CommandError('No se encontraron créditos de libranza para reparar.')

        report_rows = []
        for credito in creditos:
            cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
            if not cuotas:
                report_rows.append({
                    'numero_credito': credito.numero_credito,
                    'can_fix': False,
                    'motivo': 'sin_tabla_amortizacion',
                })
                continue

            primera_fecha = cuotas[0].fecha_vencimiento
            dia_ancla = obtener_dia_ancla_vencimiento(credito, primera_fecha)
            nuevas_fechas = []
            fecha_cursor = primera_fecha
            for index, cuota in enumerate(cuotas):
                if index == 0:
                    nuevas_fechas.append(fecha_cursor)
                else:
                    fecha_cursor = sumar_meses_con_dia_ancla(fecha_cursor, 1, dia_ancla)
                    nuevas_fechas.append(fecha_cursor)

            cambios = [
                {
                    'numero_cuota': cuota.numero_cuota,
                    'actual': str(cuota.fecha_vencimiento),
                    'nuevo': str(nueva_fecha),
                }
                for cuota, nueva_fecha in zip(cuotas, nuevas_fechas)
                if cuota.fecha_vencimiento != nueva_fecha
            ]

            proxima_pendiente = None
            for cuota, nueva_fecha in zip(cuotas, nuevas_fechas):
                if not cuota.pagada:
                    proxima_pendiente = nueva_fecha
                    break

            report_rows.append({
                'numero_credito': credito.numero_credito,
                'can_fix': True,
                'dia_ancla': dia_ancla,
                'cambios': cambios,
                'fecha_proximo_pago_actual': str(credito.fecha_proximo_pago) if credito.fecha_proximo_pago else '',
                'fecha_proximo_pago_nueva': str(proxima_pendiente) if proxima_pendiente else '',
            })

        payload = {
            'mode': 'apply' if options['apply'] else 'dry-run',
            'creditos': report_rows,
        }

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry-run: no se aplicarán cambios.'))
            self._print_summary(report_rows)
            self._write_report(options.get('report_file'), payload)
            return

        with transaction.atomic():
            for credito in creditos:
                cuotas = list(credito.tabla_amortizacion.order_by('numero_cuota'))
                if not cuotas:
                    continue
                dia_ancla = obtener_dia_ancla_vencimiento(credito, cuotas[0].fecha_vencimiento)
                fecha_cursor = cuotas[0].fecha_vencimiento
                for index, cuota in enumerate(cuotas):
                    if index == 0:
                        nueva_fecha = fecha_cursor
                    else:
                        fecha_cursor = sumar_meses_con_dia_ancla(fecha_cursor, 1, dia_ancla)
                        nueva_fecha = fecha_cursor
                    if cuota.fecha_vencimiento != nueva_fecha:
                        cuota.fecha_vencimiento = nueva_fecha
                        cuota.save(update_fields=['fecha_vencimiento'])

                primera_pendiente = credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
                credito.fecha_proximo_pago = primera_pendiente.fecha_vencimiento if primera_pendiente else None
                credito.save(update_fields=['fecha_proximo_pago'])

        self.stdout.write(self.style.SUCCESS('Cronograma de libranza reparado.'))
        self._print_summary(report_rows)
        self._write_report(options.get('report_file'), payload)

    def _print_summary(self, rows):
        for row in rows:
            if not row.get('can_fix'):
                self.stdout.write(f"{row['numero_credito']}: omitido ({row['motivo']})")
                continue
            self.stdout.write(
                f"{row['numero_credito']}: cambios={len(row['cambios'])} | "
                f"proximo_actual={row['fecha_proximo_pago_actual'] or '-'} | "
                f"proximo_nuevo={row['fecha_proximo_pago_nueva'] or '-'}"
            )

    def _write_report(self, report_file, payload):
        if not report_file:
            return
        with open(report_file, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        self.stdout.write(self.style.SUCCESS(f'Reporte guardado en {report_file}'))

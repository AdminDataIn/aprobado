from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gestion_creditos.services.pagador_notifications import (
    enviar_resumenes_pagador,
    preparar_lotes_resumen_pagador,
)


class Command(BaseCommand):
    help = 'Prueba o ejecuta el resumen mensual de obligaciones para pagadores.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fecha-corte',
            type=date.fromisoformat,
            help='Fecha de referencia YYYY-MM-DD. Si se omite, usa hoy.',
        )
        parser.add_argument(
            '--empresa-id',
            type=int,
            help='Limita el envio a una empresa especifica.',
        )
        parser.add_argument(
            '--email',
            help='Destinatario de prueba. Si se omite, usa el primer correo interno configurado.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Permite ejecutar fuera de la ventana automatica de fin de mes.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No envia correos; solo muestra el diagnostico del lote.',
        )
        parser.add_argument(
            '--include-internal-cc',
            action='store_true',
            help='Mantiene copia a correos internos en la prueba.',
        )

    def handle(self, *args, **options):
        fecha_corte = options.get('fecha_corte')
        empresa_id = options.get('empresa_id')
        exigir_ventana = not options.get('force', False)
        destinatario = (options.get('email') or '').strip()
        internos = list(getattr(settings, 'CREDIT_INTERNAL_NOTIFICATION_EMAILS', []))

        if not destinatario:
            if not internos:
                raise CommandError(
                    'No se indico --email y tampoco hay CREDIT_INTERNAL_NOTIFICATION_EMAILS configurado.'
                )
            destinatario = internos[0]

        prepared = preparar_lotes_resumen_pagador(
            fecha_referencia=fecha_corte,
            exigir_ventana_mensual=exigir_ventana,
        )
        if prepared['status'] != 'ready':
            self.stdout.write(self.style.WARNING(f"Sin envio: {prepared['reason']}"))
            return

        batches = prepared['batches']
        if empresa_id is not None:
            batches = [batch for batch in batches if batch['empresa'].id == empresa_id]

        if not batches:
            self.stdout.write(self.style.WARNING('No hay empresas elegibles para enviar.'))
            return

        self.stdout.write(
            f"Fecha de referencia: {prepared['fecha_referencia']} | "
            f"fecha de corte: {prepared['fecha_corte']} | "
            f"ventana: {prepared['window']}"
        )
        self.stdout.write(
            f"Cuotas evaluadas: {prepared['diagnostics']['cuotas_evaluadas']} | "
            f"Empresas con cuotas: {prepared['diagnostics']['empresas_con_cuotas']} | "
            f"Empresas con envio: {prepared['diagnostics']['empresas_con_envio']}"
        )
        if prepared['diagnostics']['empresas_sin_destinatarios']:
            self.stdout.write(
                self.style.WARNING(
                    'Empresas sin destinatarios: '
                    + ', '.join(prepared['diagnostics']['empresas_sin_destinatarios'])
                )
            )

        for batch in batches:
            self.stdout.write(
                f"- Empresa #{batch['empresa'].id} {batch['empresa'].nombre}: "
                f"{len(batch['cuotas'])} cuotas, total ${batch['total']:,.2f}"
            )

        if options.get('dry_run'):
            self.stdout.write(self.style.SUCCESS('Dry-run completado. No se enviaron correos.'))
            return

        resultado = enviar_resumenes_pagador(
            fecha_referencia=fecha_corte,
            exigir_ventana_mensual=exigir_ventana,
            destinatarios_override=[destinatario],
            include_internal_cc=options.get('include_internal_cc', False),
            marcar_enviado=False,
            empresa_id=empresa_id,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Envio de prueba completado: {resultado['empresas_notificadas']}/"
                f"{resultado['empresas_evaluadas']} correos enviados a {destinatario}."
            )
        )

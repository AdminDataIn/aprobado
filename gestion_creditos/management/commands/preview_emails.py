import json

from django.core.management.base import BaseCommand, CommandError

from gestion_creditos.services.email_catalog import (
    build_email_inventory,
    get_email_preview_specs,
    send_email_previews,
)


class Command(BaseCommand):
    help = 'Renderiza y envía previews controlados de los correos automáticos del sistema.'

    def add_arguments(self, parser):
        parser.add_argument('--to', help='Correo destino para los previews.')
        parser.add_argument(
            '--only',
            nargs='*',
            help='Slugs específicos a enviar.',
        )
        parser.add_argument(
            '--audit-only',
            action='store_true',
            help='No envía correos. Imprime el inventario auditado.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Con --audit-only, imprime el inventario en JSON.',
        )

    def handle(self, *args, **options):
        if options['audit_only']:
            inventory = build_email_inventory()
            if options['json']:
                self.stdout.write(json.dumps(inventory, ensure_ascii=False, indent=2, default=str))
                return

            for item in inventory:
                self.stdout.write(
                    f"[{item['categoria']}] {item['slug']} | {item['destinatario']} | "
                    f"{item['template_html']} | CTA={item['cta']} | Footer={item['footer']}"
                )
            self.stdout.write(self.style.SUCCESS(f'Inventario auditado: {len(inventory)} correos.'))
            return

        to_email = (options.get('to') or '').strip()
        if not to_email:
            raise CommandError('Debes indicar --to para enviar previews.')

        requested = set(options.get('only') or [])
        if requested:
            valid = {spec.slug for spec in get_email_preview_specs()}
            invalid = sorted(requested - valid)
            if invalid:
                raise CommandError(f'Slugs inválidos: {", ".join(invalid)}')

        sent = send_email_previews(
            to_email=to_email,
            only=options.get('only') or None,
        )
        self.stdout.write(self.style.SUCCESS(f'Previews enviados: {len(sent)}'))
        for slug in sent:
            self.stdout.write(f'- {slug}')

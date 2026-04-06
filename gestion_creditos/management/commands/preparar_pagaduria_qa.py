from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos.models import Empresa
from usuarios.models import PerfilPagador, ProductAccessProfile
from usuarios.product_flow import ProductFlowConflict, assign_user_flow, get_user_flow


class Command(BaseCommand):
    help = (
        'Crea o actualiza una pagaduria QA con su usuario pagador, '
        'sin tocar datos reales y dejando la empresa lista para cargues controlados.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--empresa', required=True, help='Nombre visible de la empresa QA.')
        parser.add_argument('--pagador-email', required=True, help='Correo del usuario pagador de prueba.')
        parser.add_argument('--pagador-nombre', default='Pagador QA', help='Nombre completo del pagador.')
        parser.add_argument('--nit', default='', help='NIT de referencia.')
        parser.add_argument('--correo-contacto', default='', help='Correo de contacto de la empresa.')
        parser.add_argument('--telefono-contacto', default='', help='Telefono de contacto de la empresa.')
        parser.add_argument(
            '--tipo-empresa',
            choices=Empresa.TipoEmpresa.values,
            default=Empresa.TipoEmpresa.CONVENIO,
            help='Tipo de empresa. Default: CONVENIO.',
        )
        parser.add_argument(
            '--sin-convenio',
            action='store_true',
            help='Deja la empresa sin convenio activo para probar bloqueos.',
        )
        parser.add_argument(
            '--habilitar-pagos',
            action='store_true',
            help='Activa pagos_habilitados en la empresa QA.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()

        empresa_nombre = (options['empresa'] or '').strip()
        pagador_email = (options['pagador_email'] or '').strip().lower()
        pagador_nombre = (options['pagador_nombre'] or '').strip()

        if not empresa_nombre:
            raise CommandError('Debes indicar --empresa.')
        if not pagador_email:
            raise CommandError('Debes indicar --pagador-email.')

        empresa, empresa_creada = Empresa.objects.update_or_create(
            nombre=empresa_nombre,
            defaults={
                'razon_social': empresa_nombre,
                'nit': (options.get('nit') or '').strip(),
                'correo_contacto': (options.get('correo_contacto') or pagador_email).strip().lower(),
                'telefono_contacto': (options.get('telefono_contacto') or '').strip(),
                'tipo_empresa': options['tipo_empresa'],
                'convenio_activo': not options['sin_convenio'],
                'pagos_habilitados': bool(options['habilitar_pagos']),
            },
        )

        user = User.objects.filter(email__iexact=pagador_email).first()
        nombres = pagador_nombre.split()
        first_name = nombres[0] if nombres else 'Pagador'
        last_name = ' '.join(nombres[1:]) if len(nombres) > 1 else 'QA'

        if not user:
            user = User.objects.create(
                username=pagador_email,
                email=pagador_email,
                first_name=first_name[:150],
                last_name=last_name[:150],
                is_active=True,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            user_creado = True
        else:
            user_creado = False
            update_fields = []
            if not user.username:
                user.username = pagador_email
                update_fields.append('username')
            if user.email != pagador_email:
                user.email = pagador_email
                update_fields.append('email')
            if pagador_nombre and user.first_name != first_name[:150]:
                user.first_name = first_name[:150]
                update_fields.append('first_name')
            if pagador_nombre and user.last_name != last_name[:150]:
                user.last_name = last_name[:150]
                update_fields.append('last_name')
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            if update_fields:
                user.save(update_fields=update_fields)

        try:
            assign_user_flow(user, ProductAccessProfile.ProductFlow.LIBRANZA)
        except ProductFlowConflict:
            raise CommandError(
                f'El usuario {pagador_email} ya pertenece al flujo {get_user_flow(user)} y no puede reutilizarse como pagador QA.'
            )

        perfil, perfil_creado = PerfilPagador.objects.update_or_create(
            usuario=user,
            defaults={
                'empresa': empresa,
                'es_pagador': True,
            },
        )

        self.stdout.write(self.style.SUCCESS('Pagaduria QA preparada correctamente.'))
        self.stdout.write(
            f'Empresa: {empresa.nombre} | creada={empresa_creada} | '
            f'tipo={empresa.tipo_empresa} | convenio_activo={empresa.convenio_activo} | '
            f'pagos_habilitados={empresa.pagos_habilitados}'
        )
        self.stdout.write(
            f'Pagador: {user.email} | creado={user_creado} | perfil_creado={perfil_creado} | empresa={perfil.empresa.nombre}'
        )

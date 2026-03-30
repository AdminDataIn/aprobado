from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import Empresa, VinculoLaboralEmpresa
from usuarios.product_flow import ProductAccessProfile, ProductFlowConflict, assign_user_flow, get_user_flow


class Command(BaseCommand):
    help = 'Crea o actualiza un escenario limpio para probar adelanto de nomina sin depender de creditos legacy.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='Correo del usuario a preparar.')
        parser.add_argument('--empresa', required=True, help='Nombre exacto de la empresa aliada.')
        parser.add_argument('--documento', required=True, help='Documento del empleado.')
        parser.add_argument('--nombre', required=True, help='Nombre completo del empleado.')
        parser.add_argument('--telefono', default='', help='Telefono del empleado.')
        parser.add_argument('--salario', required=True, type=Decimal, help='Salario base mensual.')
        parser.add_argument(
            '--dias-antiguedad',
            type=int,
            default=45,
            help='Dias transcurridos desde el alta en Aprobado. Default: 45.',
        )
        parser.add_argument(
            '--sin-convenio',
            action='store_true',
            help='Deja la empresa sin convenio activo para probar el bloqueo.',
        )
        parser.add_argument(
            '--inactivo',
            action='store_true',
            help='Deja el vinculo laboral inactivo para probar el bloqueo.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        email = (options['email'] or '').strip().lower()
        empresa_nombre = (options['empresa'] or '').strip()
        documento = ''.join(ch for ch in str(options['documento']) if ch.isdigit())
        telefono = ''.join(ch for ch in str(options['telefono']) if ch.isdigit())
        salario = options['salario']
        nombre = (options['nombre'] or '').strip()

        if not email:
            raise CommandError('Debes indicar --email.')
        if not empresa_nombre:
            raise CommandError('Debes indicar --empresa.')
        if not documento:
            raise CommandError('Debes indicar un --documento numerico.')
        if salario <= 0:
            raise CommandError('El --salario debe ser mayor a cero.')

        empresa = Empresa.objects.filter(nombre__iexact=empresa_nombre).first()
        if not empresa:
            raise CommandError(f'No existe una empresa con nombre "{empresa_nombre}".')

        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            user = User.objects.create(
                username=email,
                email=email,
                first_name=nombre.split(' ')[0] if nombre else '',
                last_name=' '.join(nombre.split(' ')[1:]) if len(nombre.split(' ')) > 1 else '',
            )
            user.set_unusable_password()
            user.save()
        else:
            if not user.email:
                user.email = email
            if not user.username:
                user.username = email
            if nombre and not user.first_name:
                user.first_name = nombre.split(' ')[0]
            if nombre and not user.last_name and len(nombre.split(' ')) > 1:
                user.last_name = ' '.join(nombre.split(' ')[1:])
            user.save()

        try:
            assign_user_flow(user, ProductAccessProfile.ProductFlow.LIBRANZA)
        except ProductFlowConflict:
            raise CommandError(
                f'El usuario ya pertenece al flujo de {get_user_flow(user)} y no puede reutilizarse para libranza.'
            )

        empresa.convenio_activo = not options['sin_convenio']
        empresa.save(update_fields=['convenio_activo'])

        fecha_alta = timezone.localdate() - timedelta(days=max(options['dias_antiguedad'], 0))
        vinculo, vinculo_creado = VinculoLaboralEmpresa.objects.update_or_create(
            usuario=user,
            empresa=empresa,
            defaults={
                'documento_empleado': documento,
                'nombre_empleado': nombre,
                'correo_empleado': email,
                'telefono_empleado': telefono,
                'estado_vinculo': (
                    VinculoLaboralEmpresa.EstadoVinculo.INACTIVO
                    if options['inactivo']
                    else VinculoLaboralEmpresa.EstadoVinculo.ACTIVO
                ),
                'fecha_alta_aprobado': fecha_alta,
                'salario_base_mensual': salario,
                'validado_por_pagador': True,
                'observaciones': 'Escenario preparado por comando de prueba de adelanto de nomina.',
            },
        )

        self.stdout.write(self.style.SUCCESS('Escenario de adelanto preparado correctamente.'))
        self.stdout.write(f'Usuario: {user.email} (id={user.id})')
        self.stdout.write(f'Empresa: {empresa.nombre} | convenio_activo={empresa.convenio_activo}')
        self.stdout.write(
            f'Vinculo: {"creado" if vinculo_creado else "actualizado"} | estado={vinculo.estado_vinculo} | alta={vinculo.fecha_alta_aprobado}'
        )
        self.stdout.write(f'Salario base: {vinculo.salario_base_mensual}')
        self.stdout.write(f'Adelanto maximo estimado: {vinculo.adelanto_maximo}')

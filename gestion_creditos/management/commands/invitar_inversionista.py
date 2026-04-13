from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from gestion_creditos.models import InvestorAccount
from gestion_creditos.services.name_normalization import normalize_name_upper
from usuarios.investor_activation_service import enviar_invitacion_inversionista
from usuarios.models import ProductAccessProfile
from usuarios.product_flow import assign_user_flow


class Command(BaseCommand):
    help = 'Crea o reutiliza un usuario inversionista y envia el enlace seguro de activacion.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--nombre', default='')
        parser.add_argument('--apellido', default='')

    def handle(self, *args, **options):
        User = get_user_model()
        email = options['email'].strip().lower()
        nombre = normalize_name_upper(options['nombre'])
        apellido = normalize_name_upper(options['apellido'])

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if not user:
            user = User.objects.create(
                username=email,
                email=email,
                first_name=nombre[:150],
                last_name=apellido[:150],
                is_active=True,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            created = True

        InvestorAccount.objects.get_or_create(usuario=user)
        try:
            assign_user_flow(user, ProductAccessProfile.ProductFlow.INVERSIONISTA)
        except Exception:
            pass

        enviar_invitacion_inversionista(user)
        accion = 'creado' if created else 'reutilizado'
        self.stdout.write(self.style.SUCCESS(f'Usuario {accion} e invitacion enviada a {email}.'))

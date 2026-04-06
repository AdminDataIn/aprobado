from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gestion_creditos.models import (
    InvestorAccount,
    InvestmentCashflow,
    InvestmentEvent,
    InvestmentPosition,
    InvestmentReturnSnapshot,
)


User = get_user_model()


class Command(BaseCommand):
    help = 'Crea o limpia un portafolio demo para validar visualmente el dashboard del inversionista.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='Correo del usuario inversionista.')
        parser.add_argument('--reset-demo', action='store_true', help='Borra datos demo previos del inversionista y crea nuevos.')
        parser.add_argument('--clear-demo', action='store_true', help='Borra el portafolio demo y sale sin recrearlo.')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        reset_demo = options['reset_demo']
        clear_demo = options['clear_demo']

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            raise CommandError(f'No existe un usuario con el correo {email}.') from exc

        account, _ = InvestorAccount.objects.get_or_create(usuario=user)

        with transaction.atomic():
            if reset_demo or clear_demo:
                account.positions.all().delete()
                account.snapshots.all().delete()
                account.events.all().delete()

            if clear_demo:
                self.stdout.write(self.style.SUCCESS(f'Se limpió el portafolio demo de {email}.'))
                return

            if account.positions.exists():
                raise CommandError(
                    'La cuenta ya tiene posiciones cargadas. Usa --reset-demo para reemplazarlas '
                    'o --clear-demo para limpiarlas.'
                )

            today = timezone.localdate()

            posiciones = [
                {
                    'titulo': 'Cartera libranza corporativa norte',
                    'estado': InvestmentPosition.EstadoPosicion.ACTIVA,
                    'aporte_inicial': Decimal('12400000'),
                    'capital_activo': Decimal('12400000'),
                    'capital_recuperado': Decimal('1900000'),
                    'tasa': Decimal('15.80'),
                    'fecha_inicio': today - timedelta(days=210),
                    'fecha_cierre': None,
                    'descripcion': '[DEMO] Flujo mensual de libranza corporativa.',
                    'cashflows': [
                        ('retorno', Decimal('498000'), today + timedelta(days=12), '[DEMO] Retorno mensual'),
                        ('retorno', Decimal('498000'), today + timedelta(days=42), '[DEMO] Retorno mensual'),
                    ],
                },
                {
                    'titulo': 'Vehículo agroindustrial senior',
                    'estado': InvestmentPosition.EstadoPosicion.ACTIVA,
                    'aporte_inicial': Decimal('8500000'),
                    'capital_activo': Decimal('8500000'),
                    'capital_recuperado': Decimal('1250000'),
                    'tasa': Decimal('14.20'),
                    'fecha_inicio': today - timedelta(days=160),
                    'fecha_cierre': None,
                    'descripcion': '[DEMO] Posición senior con salida parcial programada.',
                    'cashflows': [
                        ('retorno', Decimal('742000'), today + timedelta(days=27), '[DEMO] Capital + retorno'),
                    ],
                },
                {
                    'titulo': 'Reserva táctica de liquidez',
                    'estado': InvestmentPosition.EstadoPosicion.CERRADA,
                    'aporte_inicial': Decimal('3200000'),
                    'capital_activo': Decimal('0'),
                    'capital_recuperado': Decimal('3200000'),
                    'tasa': Decimal('9.10'),
                    'fecha_inicio': today - timedelta(days=120),
                    'fecha_cierre': today - timedelta(days=8),
                    'descripcion': '[DEMO] Vehículo de liquidez táctica cerrado.',
                    'cashflows': [
                        ('salida_capital', Decimal('3200000'), today - timedelta(days=8), '[DEMO] Capital recuperado'),
                    ],
                },
            ]

            for data in posiciones:
                position = InvestmentPosition.objects.create(
                    account=account,
                    titulo=data['titulo'],
                    estado=data['estado'],
                    aporte_inicial=data['aporte_inicial'],
                    capital_activo=data['capital_activo'],
                    capital_recuperado=data['capital_recuperado'],
                    tasa_proyectada_anual=data['tasa'],
                    fecha_inicio=data['fecha_inicio'],
                    fecha_cierre=data['fecha_cierre'],
                    descripcion=data['descripcion'],
                )
                for tipo, monto, fecha, descripcion in data['cashflows']:
                    InvestmentCashflow.objects.create(
                        position=position,
                        tipo=tipo,
                        monto=monto,
                        fecha_efectiva=fecha,
                        descripcion=descripcion,
                    )

            snapshots = [
                (today - timedelta(days=150), Decimal('4.10'), Decimal('0.88'), Decimal('14.20'), Decimal('9200000'), Decimal('800000'), 167),
                (today - timedelta(days=120), Decimal('4.95'), Decimal('0.91'), Decimal('14.40'), Decimal('11100000'), Decimal('1150000'), 160),
                (today - timedelta(days=90), Decimal('5.80'), Decimal('1.02'), Decimal('14.50'), Decimal('14750000'), Decimal('1680000'), 154),
                (today - timedelta(days=60), Decimal('6.72'), Decimal('1.18'), Decimal('14.60'), Decimal('18100000'), Decimal('2440000'), 148),
                (today - timedelta(days=30), Decimal('7.54'), Decimal('1.26'), Decimal('14.65'), Decimal('20900000'), Decimal('3920000'), 144),
                (today, Decimal('8.40'), Decimal('1.34'), Decimal('14.70'), Decimal('20900000'), Decimal('6350000'), 142),
            ]

            for fecha_corte, roi_acumulado, roi_mensual, tasa, capital_activo, capital_recuperado, dias in snapshots:
                InvestmentReturnSnapshot.objects.create(
                    account=account,
                    fecha_corte=fecha_corte,
                    roi_acumulado=roi_acumulado,
                    roi_mensual=roi_mensual,
                    tasa_retorno_proyectada=tasa,
                    capital_activo=capital_activo,
                    capital_recuperado=capital_recuperado,
                    tiempo_promedio_retorno_dias=dias,
                )

            InvestmentEvent.objects.create(
                account=account,
                titulo='[DEMO] Portafolio demo cargado',
                descripcion='Se cargó un portafolio demo para revisión visual del dashboard.',
            )

        self.stdout.write(self.style.SUCCESS(f'Se creó el portafolio demo para {email}.'))

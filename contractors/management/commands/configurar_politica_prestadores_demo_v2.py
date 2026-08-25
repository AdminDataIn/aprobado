from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from contractors.management.commands.configurar_politica_prestadores_demo import (
    BANDAS_DEMO,
    VERSION_FINANCIERA_DEMO,
)
from contractors.models import (
    BandaScorePrestador,
    ConfiguracionScorePrestador,
    ConfiguracionSimuladorPrestador,
    PredecisionPrestadorAudit,
)


VERSION_SCORE_DEMO_V2 = 'prestadores-score-demo-v2'
VERSION_SCORE_DEMO_V3 = 'prestadores-score-demo-v3'
VERSION_POLITICA_DEMO_V2 = 'politica-prestadores-demo-v2'
VERSION_POLITICA_DEMO_V3 = 'politica-prestadores-demo-v3'


class Command(BaseCommand):
    help = 'Crea la politica dual MiDecisor + HDCPlus DEMO v2 sin activarla por defecto.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--activar',
            action='store_true',
            help='No disponible: la politica DEMO debe activarse por control administrativo.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['activar']:
            raise CommandError(
                'Este comando no activa politicas DEMO. Activa la politica mediante control administrativo.'
            )
        financiera = ConfiguracionSimuladorPrestador.objects.filter(
            version=VERSION_FINANCIERA_DEMO,
        ).first()
        if financiera is None:
            raise CommandError(
                'No existe la configuracion financiera DEMO v1. Configurala antes de crear V2.'
            )
        version, version_score, version_politica = self._version_objetivo()
        valores = {
            'nombre': f'Politica Score Prestadores DEMO {version.rsplit("-", 1)[-1]} dual',
            'activa': False,
            'fecha_vigencia_desde': timezone.localdate(),
            'configuracion_financiera': financiera,
            'peso_datacredito': Decimal('0.00000'),
            'peso_midecisor': Decimal('0.45000'),
            'peso_hdcplus': Decimal('0.00000'),
            'peso_capacidad': Decimal('0.30000'),
            'peso_comportamiento': Decimal('0.08000'),
            'peso_riesgo': Decimal('0.12000'),
            'peso_referencias': Decimal('0.05000'),
            'score_premium_min': 850,
            'score_alta_min': 750,
            'score_media_min': 680,
            'score_entrada_min': 600,
            'cuota_ingreso_maxima': Decimal('0.30000'),
            'tolerancia_ingreso_contractual': Decimal('0.15000'),
            'monto_maximo_politica': financiera.monto_maximo,
            'plazo_maximo_politica': financiera.plazo_maximo_meses,
            'tasa_mensual_referencia': financiera.tasa_mensual,
            'penalizacion_geolocalizacion': 80,
            'umbral_geolocalizacion': 600,
            'mora_bloqueo_dias': 90,
            'consultas_recientes_revision': 6,
            'requiere_referencias': False,
            # Referencias no son obligatorias en V2. Su peso se redistribuye de
            # forma explicita y auditable cuando no existe evidencia verificable.
            'permite_redistribuir_pesos_faltantes': True,
            'requiere_midecisor': True,
            'requiere_hdcplus': True,
            'permite_evaluar_sin_hdc': False,
            'permite_evaluar_sin_midecisor': False,
            'accion_sin_informacion_centrales': (
                ConfiguracionScorePrestador.AccionDisponibilidadCentrales.REVISION_MANUAL
            ),
            'accion_error_transitorio_centrales': (
                ConfiguracionScorePrestador.AccionDisponibilidadCentrales.REVISION_MANUAL
            ),
            'accion_error_permanente_centrales': (
                ConfiguracionScorePrestador.AccionDisponibilidadCentrales.NO_EVALUABLE
            ),
            'vigencia_midecisor_dias': 30,
            'vigencia_hdcplus_dias': 30,
            'accion_exceso_capacidad': (
                ConfiguracionScorePrestador.AccionExcesoCapacidad.REVISION
            ),
            'version_score': version_score,
            'version_politica': version_politica,
        }
        politica, creada = ConfiguracionScorePrestador.objects.get_or_create(
            version=version,
            defaults=valores,
        )
        if not creada:
            if politica.activa:
                raise CommandError(
                    'La politica DEMO existente esta activa y no puede corregirse automaticamente.'
                )
            if PredecisionPrestadorAudit.objects.filter(
                version_politica=politica.version_politica,
            ).exists():
                self._validar_existente(politica, financiera)
            else:
                self._actualizar_politica_no_usada(politica, valores)
        bandas_creadas = self._crear_bandas(politica)
        politica.full_clean()
        self.stdout.write(self.style.SUCCESS(
            f'Politica DEMO {version} {"creada" if creada else "reutilizada"}; '
            f'bandas_creadas={bandas_creadas}; activa={politica.activa}. '
            'DataCredito aporta variables al score y no aplica hard rules. '
            'No es politica productiva.'
        ))

    @staticmethod
    def _version_objetivo():
        v2_utilizada = PredecisionPrestadorAudit.objects.filter(
            version_politica=VERSION_POLITICA_DEMO_V2,
        ).exists()
        if v2_utilizada:
            return (
                VERSION_SCORE_DEMO_V3,
                'score-prestadores-demo-v3',
                VERSION_POLITICA_DEMO_V3,
            )
        return (
            VERSION_SCORE_DEMO_V2,
            'score-prestadores-demo-v2',
            VERSION_POLITICA_DEMO_V2,
        )

    def _crear_bandas(self, politica):
        creadas = 0
        for nombre, minimo, maximo, monto, plazo, resultado, orden in BANDAS_DEMO:
            banda, creada = BandaScorePrestador.objects.get_or_create(
                configuracion=politica,
                nombre=nombre,
                defaults={
                    'score_min': minimo,
                    'score_max': maximo,
                    'monto_maximo': monto,
                    'plazo_maximo': plazo,
                    'resultado': resultado,
                    'orden': orden,
                },
            )
            banda.full_clean()
            creadas += int(creada)
        return creadas

    @staticmethod
    def _validar_existente(politica, financiera):
        if politica.configuracion_financiera_id != financiera.id:
            raise CommandError('La politica V2 existente usa otra configuracion financiera.')
        esperados = {
            'peso_midecisor': Decimal('0.45000'),
            'peso_hdcplus': Decimal('0.00000'),
            'peso_capacidad': Decimal('0.30000'),
            'peso_comportamiento': Decimal('0.08000'),
            'peso_riesgo': Decimal('0.12000'),
            'peso_referencias': Decimal('0.05000'),
            'requiere_midecisor': True,
            'requiere_hdcplus': True,
            'permite_redistribuir_pesos_faltantes': True,
            'tolerancia_ingreso_contractual': Decimal('0.15000'),
        }
        diferencias = [
            campo for campo, valor in esperados.items()
            if getattr(politica, campo) != valor
        ]
        if diferencias:
            raise CommandError(
                'La politica V2 existe con valores diferentes: ' + ', '.join(diferencias)
            )

    @staticmethod
    def _actualizar_politica_no_usada(politica, valores):
        campos = tuple(
            campo for campo in valores
            if campo not in {'activa', 'fecha_vigencia_desde'}
        )
        for campo in campos:
            setattr(politica, campo, valores[campo])
        politica.full_clean()
        politica.save(update_fields=[*campos, 'updated_at'])

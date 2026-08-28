from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from contractors.models import (
    BandaScorePrestador,
    ConfiguracionScorePrestador,
    ConfiguracionSimuladorPrestador,
)
from contractors.score.politica import validar_politica_score_completa


VERSION_FINANCIERA_DEMO = 'prestadores-demo-v1'
VERSION_SCORE_DEMO = 'prestadores-score-demo-v1'

# Valor DEMO respaldado por las pruebas versionadas de score, aprobacion interna y
# originacion. No constituye una politica productiva ni se instala automaticamente.
TASA_MENSUAL_DEMO = Decimal('2.2000')

CONFIGURACION_FINANCIERA_DEMO = {
    'nombre': 'Simulador Prestadores DEMO v1',
    'monto_minimo': Decimal('1000000'),
    'monto_maximo': Decimal('10000000'),
    'plazo_minimo_meses': 3,
    'plazo_maximo_meses': 8,
    'tasa_mensual': TASA_MENSUAL_DEMO,
    'porcentaje_originacion': Decimal('10.0000'),
    'porcentaje_iva_originacion': Decimal('19.0000'),
    'porcentaje_fondo_garantia': Decimal('2.0000'),
    'porcentaje_seguro_vida_primera_cuota': Decimal('0.3711'),
    'texto_nota_simulacion': 'Configuracion informativa DEMO; no es politica productiva.',
}

POLITICA_SCORE_DEMO = {
    'nombre': 'Politica Score Prestadores DEMO v1',
    'peso_datacredito': Decimal('0.45000'),
    'peso_capacidad': Decimal('0.30000'),
    'peso_comportamiento': Decimal('0.08000'),
    'peso_riesgo': Decimal('0.12000'),
    'peso_referencias': Decimal('0.05000'),
    'score_premium_min': 850,
    'score_alta_min': 750,
    'score_media_min': 680,
    'score_entrada_min': 600,
    'cuota_ingreso_maxima': Decimal('0.30000'),
    'monto_maximo_politica': Decimal('10000000'),
    'plazo_maximo_politica': 8,
    'tasa_mensual_referencia': TASA_MENSUAL_DEMO,
    'penalizacion_geolocalizacion': 80,
    'umbral_geolocalizacion': 600,
    'mora_bloqueo_dias': 90,
    'consultas_recientes_revision': 6,
    'requiere_referencias': False,
    'permite_redistribuir_pesos_faltantes': True,
    'accion_exceso_capacidad': ConfiguracionScorePrestador.AccionExcesoCapacidad.REVISION,
    'version_score': 'score-prestadores-demo-v1',
    'version_politica': 'politica-prestadores-demo-v1',
}

BANDAS_DEMO = (
    ('REVISION', 0, 599, Decimal('0'), 0, 'REQUIERE_REVISION_MANUAL', 5),
    ('ENTRADA', 600, 679, Decimal('3000000'), 4, 'PREAPROBADO_READ_ONLY', 4),
    ('MEDIA', 680, 749, Decimal('5000000'), 6, 'PREAPROBADO_READ_ONLY', 3),
    ('ALTA', 750, 849, Decimal('8000000'), 8, 'PREAPROBADO_READ_ONLY', 2),
    ('PREMIUM', 850, 1000, Decimal('10000000'), 8, 'PREAPROBADO_READ_ONLY', 1),
)


class Command(BaseCommand):
    help = 'Crea o reutiliza la parametrizacion LOCAL/DEMO de Prestadores.'

    @transaction.atomic
    def handle(self, *args, **options):
        financiera, financiera_creada = self._configuracion_financiera()
        politica, politica_creada = self._politica(financiera)
        bandas_creadas = self._bandas(politica)

        if not politica.activa:
            politica.activa = True
            politica.full_clean()
            politica.save(update_fields=['activa', 'updated_at'])
        validar_politica_score_completa(politica)

        self.stdout.write(self.style.SUCCESS(
            'Configuracion DEMO lista. '
            f'Financiera={"creada" if financiera_creada else "reutilizada"}; '
            f'score={"creada" if politica_creada else "reutilizada"}; '
            f'bandas_creadas={bandas_creadas}; tasa_demo={TASA_MENSUAL_DEMO}%.'
        ))

    def _configuracion_financiera(self):
        conflicto = ConfiguracionSimuladorPrestador.objects.select_for_update().filter(
            activo=True,
        ).exclude(version=VERSION_FINANCIERA_DEMO).first()
        if conflicto:
            raise CommandError(
                'Existe otra configuracion financiera activa. Desactivala de forma '
                'administrativa antes de instalar la DEMO.'
            )
        configuracion, creada = ConfiguracionSimuladorPrestador.objects.get_or_create(
            version=VERSION_FINANCIERA_DEMO,
            defaults={**CONFIGURACION_FINANCIERA_DEMO, 'activo': True},
        )
        self._exigir_valores(
            configuracion,
            CONFIGURACION_FINANCIERA_DEMO,
            'configuracion financiera DEMO',
        )
        if not configuracion.activo:
            configuracion.activo = True
            configuracion.full_clean()
            configuracion.save(update_fields=['activo', 'updated_at'])
        else:
            configuracion.full_clean()
        return configuracion, creada

    def _politica(self, financiera):
        conflicto = ConfiguracionScorePrestador.objects.select_for_update().filter(
            activa=True,
        ).exclude(version=VERSION_SCORE_DEMO).first()
        if conflicto:
            raise CommandError(
                'Existe otra politica de score activa. Desactivala de forma administrativa '
                'antes de instalar la DEMO.'
            )
        politica, creada = ConfiguracionScorePrestador.objects.get_or_create(
            version=VERSION_SCORE_DEMO,
            defaults={
                **POLITICA_SCORE_DEMO,
                'activa': False,
                'fecha_vigencia_desde': timezone.localdate(),
                'configuracion_financiera': financiera,
            },
        )
        self._exigir_valores(politica, POLITICA_SCORE_DEMO, 'politica score DEMO')
        if politica.configuracion_financiera_id != financiera.id:
            raise CommandError(
                'La politica DEMO existente referencia otra configuracion financiera; '
                'no se modifico.'
            )
        return politica, creada

    def _bandas(self, politica):
        creadas = 0
        for nombre, minimo, maximo, monto, plazo, resultado, orden in BANDAS_DEMO:
            esperados = {
                'score_min': minimo,
                'score_max': maximo,
                'monto_maximo': monto,
                'plazo_maximo': plazo,
                'resultado': resultado,
                'orden': orden,
            }
            banda, creada = BandaScorePrestador.objects.get_or_create(
                configuracion=politica,
                nombre=nombre,
                defaults=esperados,
            )
            self._exigir_valores(banda, esperados, f'banda DEMO {nombre}')
            banda.full_clean()
            creadas += int(creada)
        return creadas

    @staticmethod
    def _exigir_valores(instancia, esperados, etiqueta):
        diferencias = [
            campo for campo, esperado in esperados.items()
            if getattr(instancia, campo) != esperado
        ]
        if diferencias:
            raise CommandError(
                f'La {etiqueta} ya existe con valores diferentes en: '
                f'{", ".join(diferencias)}. No se modifico.'
            )

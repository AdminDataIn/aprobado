import json

from django.core.management.base import BaseCommand, CommandError

from contractors.models import ContractorApplication
from contractors.services.datacredito_evaluacion import (
    REUTILIZAR_SI_VIGENTE,
    SOLO_CACHE,
    obtener_evaluacion_datacredito_prestador,
)
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot


class Command(BaseCommand):
    help = 'Prueba HDCPlus UAT para una solicitud y muestra solo una allowlist sanitizada.'

    def add_arguments(self, parser):
        parser.add_argument('--solicitud-id', type=int, required=True)
        parser.add_argument('--confirmar-consumo-real', action='store_true')
        parser.add_argument(
            '--solo-cache',
            action='store_true',
            help='No consume proveedor; exige un snapshot vigente.',
        )

    def handle(self, *args, **options):
        configuracion = obtener_configuracion_datacredito()
        if str(configuracion.environment).lower() != 'uat':
            raise CommandError('Este comando solo esta permitido en UAT.')
        if not options['solo_cache']:
            if not options['confirmar_consumo_real']:
                self.stdout.write(
                    'Consumo real no ejecutado. Use --confirmar-consumo-real para confirmar.'
                )
                return
            if not configuracion.real_enabled:
                raise CommandError('DATACREDITO_REAL_ENABLED debe estar activo temporalmente.')

        solicitud = ContractorApplication.objects.get(pk=options['solicitud_id'])
        resultado = obtener_evaluacion_datacredito_prestador(
            solicitud,
            servicio=ConsultaDatacreditoSnapshot.Servicio.HISTORIAL,
            modo=SOLO_CACHE if options['solo_cache'] else REUTILIZAR_SI_VIGENTE,
        )
        snapshot = None
        if resultado.snapshot_id:
            snapshot = ConsultaDatacreditoSnapshot.objects.filter(
                pk=resultado.snapshot_id,
            ).only('codigo_http', 'codigo_funcional').first()
        normalizado = resultado.resultado_normalizado
        salida = {
            'servicio': 'historial',
            'estado': resultado.estado,
            'http': getattr(snapshot, 'codigo_http', None),
            'codigo_funcional': getattr(snapshot, 'codigo_funcional', '') or '',
            'snapshot_id': resultado.snapshot_id,
            'reutilizado': resultado.reutilizado,
            'consultado_en': resultado.consultado_en,
            'error_codigo': resultado.error_codigo,
            'normalizado': {
                'obligaciones_vigentes': getattr(normalizado, 'obligaciones_vigentes', None),
                'obligaciones_cerradas': getattr(normalizado, 'obligaciones_cerradas', None),
                'obligaciones_en_mora': getattr(normalizado, 'obligaciones_en_mora', None),
                'saldo_total': getattr(normalizado, 'saldo_total', None),
                'saldo_mora': getattr(normalizado, 'saldo_mora', None),
                'cuota_mensual_total': getattr(normalizado, 'cuota_mensual_total', None),
                'mora_actual': getattr(normalizado, 'mora_actual', None),
                'mora_severa': getattr(normalizado, 'mora_severa', None),
                'mora_maxima_dias': getattr(normalizado, 'mora_maxima_dias', None),
                'consultas_recientes': getattr(normalizado, 'consultas_recientes', None),
            },
        }
        self.stdout.write(json.dumps(salida, ensure_ascii=True, indent=2))

from decimal import Decimal

from django.core.management.base import BaseCommand

from contractors.models import ConfiguracionPortalContratistas
from gestion_creditos.models import Empresa


class Command(BaseCommand):
    help = 'Crea o actualiza datos demo para probar el portal unico de contratistas en local.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            default='contratistas.localhost',
            help='Host local o productivo del portal contratistas.',
        )
        parser.add_argument(
            '--crear-solicitud-demo',
            action='store_true',
            help='Reservado para una fase posterior; el flujo manual crea la pre-solicitud desde /solicitar/.',
        )

    def handle(self, *args, **options):
        host = ConfiguracionPortalContratistas.normalizar_host(options['host'])
        configuracion, creada = ConfiguracionPortalContratistas.objects.update_or_create(
            host=host,
            defaults={
                'nombre_visible': 'Contratistas Aprobado',
                'slug': 'contratistas',
                'activo': True,
                'color_primario': '#0d6efd',
                'color_secundario': '#1f2937',
                'correo_soporte': 'soporte@aprobado.com.co',
                'texto_landing': (
                    'Solicita un adelanto respaldado por tu contrato vigente, registra tus datos '
                    'y carga los documentos necesarios para iniciar la revision.'
                ),
                'monto_minimo': Decimal('1000000.00'),
                'monto_maximo': Decimal('10000000.00'),
                'plazo_minimo_meses': 3,
                'plazo_maximo_meses': 8,
                'tasa_mensual': Decimal('2.2000'),
                'tasa_comision': Decimal('5.0000'),
                'comision_fija': Decimal('100000.00'),
                'tasa_iva': Decimal('19.0000'),
            },
        )
        empresa, empresa_creada = Empresa.objects.update_or_create(
            nombre='Empresa Convenio Contratistas Demo',
            defaults={
                'convenio_activo': True,
                'tipo_empresa': Empresa.TipoEmpresa.CONVENIO,
                'razon_social': 'Empresa Convenio Contratistas Demo SAS',
                'nit': '900123456',
                'correo_contacto': 'pagador.demo@aprobado.com.co',
                'telefono_contacto': '6011234567',
            },
        )

        estado = 'creada' if creada else 'actualizada'
        estado_empresa = 'creada' if empresa_creada else 'actualizada'
        self.stdout.write(self.style.SUCCESS(f'Configuracion portal contratistas {estado}: {configuracion.host}'))
        self.stdout.write(self.style.SUCCESS(f'Empresa convenio {estado_empresa}: {empresa.nombre}'))
        self.stdout.write(f'Inicio redirige a solicitud: http://{configuracion.host}:8000/')
        self.stdout.write(f'Solicitud: http://{configuracion.host}:8000/solicitar/')
        self.stdout.write(f'Simulador: http://{configuracion.host}:8000/simular/?solicitud_id=<id>')
        self.stdout.write(
            'Documentos adicionales o reemplazos: cree una pre-solicitud y abra '
            f'http://{configuracion.host}:8000/solicitud/<id>/documentos/'
        )

        if options['crear_solicitud_demo']:
            self.stdout.write(
                self.style.WARNING(
                    'No se crea solicitud demo automaticamente para mantener el flujo manual desde /solicitar/.',
                ),
            )

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from contractors.models import (
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ConfiguracionPortalContratistas,
)
from gestion_creditos.models import Empresa


class Command(BaseCommand):
    help = 'Crea o actualiza datos locales de QA para probar Prestadores de Servicios.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            default='contratistas.localhost:8000',
            help='Host local del portal, por ejemplo contratistas.localhost:8000.',
        )
        parser.add_argument(
            '--password',
            default='Aprobado12345',
            help='Contrasena local para los usuarios demo.',
        )

    def handle(self, *args, **options):
        host = self._normalizar_host_recibido(options['host'])
        password = options['password']

        organizacion = self._crear_organizacion_legacy()
        branding = self._crear_branding_legacy(organizacion)
        producto = self._crear_producto_legacy(organizacion)
        portal = self._crear_portal(host)
        empresa = self._crear_empresa_demo()
        usuarios = self._crear_usuarios_demo(password)

        self.stdout.write(self.style.SUCCESS('Seed local de Prestadores de Servicios listo.'))
        self.stdout.write(f'Portal: {portal.nombre_visible} | host={portal.host} | slug={portal.slug}')
        self.stdout.write(f'Marca legacy: {branding.display_name}')
        self.stdout.write(f'Producto legacy: {producto.min_amount} - {producto.max_amount} | plazo maximo {producto.max_term_months}')
        self.stdout.write(f'Empresa demo: {empresa.nombre} | convenio_activo={empresa.convenio_activo}')
        self.stdout.write('Usuarios demo:')
        for usuario in usuarios:
            self.stdout.write(f'- {usuario.email}')

        if getattr(settings, 'DEBUG', False):
            self.stdout.write(self.style.WARNING(f'Contrasena local demo: {password}'))

        self.stdout.write('')
        self.stdout.write('Como probar local:')
        self.stdout.write(f'1. Abrir http://{host}/login/')
        self.stdout.write('2. Entrar con solicitante@aprobado.local y la contrasena demo.')
        self.stdout.write(f'3. Abrir http://{host}/solicitar/')
        self.stdout.write('4. Seleccionar Empresa Demo Prestadores SAS desde el buscador.')
        self.stdout.write('')
        self.stdout.write('No cree manualmente un Perfil contratista para el solicitante final.')
        self.stdout.write('Los perfiles contratistas son roles internos de operacion.')

    @staticmethod
    def _normalizar_host_recibido(host):
        host = (host or '').strip().lower()
        host = host.removeprefix('https://').removeprefix('http://')
        return host.split('/', 1)[0]

    def _crear_organizacion_legacy(self):
        organizacion = (
            ContractorOrganization.objects.filter(subdomain='contratistas').first()
            or ContractorOrganization.objects.filter(slug='aprobado-prestadores').first()
        )
        valores = {
            'name': 'Aprobado Prestadores',
            'slug': 'aprobado-prestadores',
            'subdomain': 'contratistas',
            'is_active': True,
        }
        if organizacion is None:
            return ContractorOrganization.objects.create(**valores)

        for campo, valor in valores.items():
            setattr(organizacion, campo, valor)
        organizacion.save()
        return organizacion

    def _crear_branding_legacy(self, organizacion):
        branding, _ = ContractorBranding.objects.update_or_create(
            organization=organizacion,
            defaults={
                'display_name': 'Aprobado',
                'primary_color': '#16b8d8',
                'secondary_color': '#07172b',
                'support_email': 'info@aprobado.com.co',
                'landing_copy': 'Prestadores de Servicios Aprobado.',
                'is_active': True,
            },
        )
        return branding

    def _crear_producto_legacy(self, organizacion):
        producto, _ = ContractorProductConfig.objects.update_or_create(
            organization=organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            defaults={
                'min_amount': Decimal('300000.00'),
                'max_amount': Decimal('10000000.00'),
                'min_term_months': 1,
                'max_term_months': 8,
                'monthly_rate': Decimal('2.2000'),
                'commission_rate': Decimal('10.0000'),
                'commission_amount': Decimal('0.00'),
                'vat_rate': Decimal('19.0000'),
                'allows_second_credit': True,
                'allows_portfolio_takeover': True,
                'is_active': True,
            },
        )
        return producto

    def _crear_portal(self, host):
        portal = (
            ConfiguracionPortalContratistas.objects.filter(host=host).first()
            or ConfiguracionPortalContratistas.objects.filter(slug='prestadores').first()
        )
        valores = {
            'nombre_visible': 'Prestadores de Servicios',
            'host': host,
            'slug': 'prestadores',
            'activo': True,
            'color_primario': '#16b8d8',
            'color_secundario': '#07172b',
            'correo_soporte': 'info@aprobado.com.co',
            'texto_landing': 'Prestadores de Servicios Aprobado.',
            'monto_minimo': Decimal('300000.00'),
            'monto_maximo': Decimal('10000000.00'),
            'plazo_minimo_meses': 1,
            'plazo_maximo_meses': 8,
            'tasa_mensual': Decimal('2.2000'),
            'tasa_comision': Decimal('10.0000'),
            'comision_fija': Decimal('0.00'),
            'tasa_iva': Decimal('19.0000'),
            'tasa_fondo_garantia': Decimal('2.0000'),
            'iva_fondo_garantia': Decimal('19.0000'),
            'fondo_garantia_incluye_iva': True,
            'factor_seguro_vida': Decimal('0.003711'),
            'seguro_vida_financiado': True,
        }
        if portal is None:
            return ConfiguracionPortalContratistas.objects.create(**valores)

        for campo, valor in valores.items():
            setattr(portal, campo, valor)
        portal.save()
        return portal

    def _crear_empresa_demo(self):
        empresa, _ = Empresa.objects.update_or_create(
            nombre='Empresa Demo Prestadores SAS',
            defaults={
                'convenio_activo': True,
                'tipo_empresa': Empresa.TipoEmpresa.CONVENIO,
                'razon_social': 'Empresa Demo Prestadores SAS',
                'nit': '901555777',
                'correo_contacto': 'pagador.demo@aprobado.local',
                'telefono_contacto': '3158562162',
                'pais': 'Colombia',
                'departamento': 'Meta',
                'municipio': 'Villavicencio',
                'ciudad': 'Villavicencio',
            },
        )
        return empresa

    def _crear_usuarios_demo(self, password):
        User = get_user_model()
        datos = [
            ('admin@aprobado.local', True, True),
            ('solicitante@aprobado.local', False, False),
            ('pagador@aprobado.local', False, False),
        ]
        usuarios = []
        for email, is_staff, is_superuser in datos:
            usuario, creado = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email,
                    'is_staff': is_staff,
                    'is_superuser': is_superuser,
                },
            )
            if creado:
                usuario.set_password(password)
            usuario.username = usuario.username or email
            usuario.is_staff = usuario.is_staff or is_staff
            usuario.is_superuser = usuario.is_superuser or is_superuser
            usuario.save()
            usuarios.append(usuario)
        return usuarios

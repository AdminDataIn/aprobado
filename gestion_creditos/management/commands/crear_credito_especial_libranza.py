import base64
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from gestion_creditos import credit_services
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, HistorialEstado


MINIMAL_PNG_BYTES = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sN1QLsAAAAASUVORK5CYII='
)

MINIMAL_PDF_BYTES = (
    b'%PDF-1.4\n'
    b'1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n'
    b'2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n'
    b'3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n'
    b'4 0 obj<< /Length 44 >>stream\n'
    b'BT /F1 12 Tf 40 96 Td (Documento legacy) Tj ET\n'
    b'endstream endobj\n'
    b'5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n'
    b'trailer<< /Root 1 0 R >>\n%%EOF'
)


class Command(BaseCommand):
    help = (
        "Crea un credito especial de libranza directamente en backend, "
        "sin depender de las validaciones del formulario publico."
    )

    def add_arguments(self, parser):
        parser.add_argument('--empresa-id', type=int, help='ID de la empresa')
        parser.add_argument('--empresa-slug', help='Slug de la empresa')
        parser.add_argument('--empresa-nombre', help='Nombre exacto de la empresa')
        parser.add_argument('--cedula', required=True, help='Cedula del titular')
        parser.add_argument('--correo', required=True, help='Correo del titular')
        parser.add_argument('--telefono', required=True, help='Celular del titular')
        parser.add_argument('--direccion', default='NO REGISTRADA', help='Direccion del titular')
        parser.add_argument('--nombre-completo', help='Nombre completo legacy si no se separa en nombres/apellidos')
        parser.add_argument('--nombres', help='Nombres del titular')
        parser.add_argument('--apellidos', default='', help='Apellidos del titular')
        parser.add_argument('--username', help='Username opcional para el usuario')
        parser.add_argument('--monto', required=True, help='Monto aprobado y solicitado')
        parser.add_argument('--plazo', required=True, type=int, help='Plazo especial en meses')
        parser.add_argument('--tasa', required=True, help='Tasa efectiva mensual')
        parser.add_argument(
            '--administracion',
            default='5',
            help='Porcentaje de administracion/comision. Default 5',
        )
        parser.add_argument(
            '--fecha-primera-cuota',
            required=True,
            dest='fecha_primera_cuota',
            help='Fecha exacta de la primera cuota en formato YYYY-MM-DD',
        )
        parser.add_argument(
            '--ingresos-mensuales',
            dest='ingresos_mensuales',
            help='Ingresos mensuales del titular. Default: mismo monto solicitado',
        )
        parser.add_argument(
            '--observacion',
            default='Credito legacy creado por backend con regla especial.',
            help='Observacion operativa del caso especial',
        )
        parser.add_argument(
            '--estado',
            choices=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA],
            default=Credito.EstadoCredito.ACTIVO,
            help='Estado inicial. Default: ACTIVO',
        )

    def handle(self, *args, **options):
        empresa = self._resolver_empresa(options)
        monto = self._to_decimal(options['monto'], 'monto')
        tasa = self._to_decimal(options['tasa'], 'tasa')
        porcentaje_admin = self._to_decimal(options['administracion'], 'administracion')
        ingresos_mensuales = self._to_decimal(
            options.get('ingresos_mensuales') or options['monto'],
            'ingresos_mensuales'
        )
        fecha_primera_cuota = self._parse_date(options['fecha_primera_cuota'])
        nombres, apellidos = self._resolver_nombre(options)
        user = self._obtener_o_crear_usuario(
            email=options['correo'].strip().lower(),
            username=options.get('username'),
            cedula=options['cedula'].strip(),
            nombres=nombres,
            apellidos=apellidos,
        )

        comision = (monto * porcentaje_admin) / Decimal('100')
        iva_comision = comision * Decimal('0.19')

        credito = Credito.objects.create(
            usuario=user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=options['estado'],
            monto_solicitado=monto,
            plazo_solicitado=options['plazo'],
            monto_aprobado=monto,
            plazo=options['plazo'],
            tasa_interes=tasa,
            comision=comision,
            iva_comision=iva_comision,
            tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL,
            fecha_primera_cuota_forzada=fecha_primera_cuota,
            plazo_forzado=options['plazo'],
            tasa_forzada=tasa,
            observacion_regla_especial=options['observacion'],
            documento_enviado=False,
        )

        detalle = CreditoLibranza(
            credito=credito,
            nombres=nombres,
            apellidos=apellidos,
            cedula=options['cedula'].strip(),
            direccion=options['direccion'].strip(),
            telefono=options['telefono'].strip(),
            correo_electronico=options['correo'].strip().lower(),
            empresa=empresa,
            ingresos_mensuales=ingresos_mensuales,
        )
        self._adjuntar_placeholder(detalle, 'cedula_frontal', f'{credito.numero_credito}_cedula_frontal.png', MINIMAL_PNG_BYTES)
        self._adjuntar_placeholder(detalle, 'cedula_trasera', f'{credito.numero_credito}_cedula_trasera.png', MINIMAL_PNG_BYTES)
        self._adjuntar_placeholder(
            detalle,
            'certificado_bancario',
            f'{credito.numero_credito}_certificado_bancario.pdf',
            MINIMAL_PDF_BYTES,
        )
        detalle.certificado_bancario_metadata = {
            'estado': 'legacy_backend',
            'mensaje': 'Documento placeholder generado por el comando de credito especial.',
        }
        detalle.certificado_bancario_estado_extraccion = 'pendiente'
        detalle.save()

        if credito.estado == Credito.EstadoCredito.ACTIVO:
            credit_services.activar_credito(credito)

        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=None,
            estado_nuevo=credito.estado,
            motivo=options['observacion'],
            usuario_modificacion=None,
        )

        credito.refresh_from_db()
        capital_financiado = (credito.monto_aprobado or Decimal('0')) + (credito.comision or Decimal('0')) + (credito.iva_comision or Decimal('0'))
        self.stdout.write(
            self.style.SUCCESS(
                f'Credito especial creado: {credito.numero_credito} | '
                f'Empresa={empresa.nombre} | Estado={credito.estado} | '
                f'Comision={credito.comision or "-"} | '
                f'IVA comision={credito.iva_comision or "-"} | '
                f'Capital financiado={capital_financiado} | '
                f'Cuota={credito.valor_cuota or "-"} | '
                f'Primera cuota={credito.fecha_proximo_pago or credito.fecha_primera_cuota_forzada}'
            )
        )

    def _resolver_empresa(self, options):
        try:
            if options.get('empresa_id'):
                return Empresa.objects.get(pk=options['empresa_id'])
            if options.get('empresa_slug'):
                return Empresa.objects.get(slug=options['empresa_slug'])
            if options.get('empresa_nombre'):
                return Empresa.objects.get(nombre=options['empresa_nombre'])
        except Empresa.DoesNotExist as exc:
            raise CommandError('No se encontró la empresa indicada para el crédito especial.') from exc

        empresas = Empresa.objects.order_by('nombre')
        if empresas.count() == 1:
            return empresas.first()

        raise CommandError(
            'Debes indicar --empresa-id, --empresa-slug o --empresa-nombre '
            'cuando exista mas de una empresa.'
        )

    def _resolver_nombre(self, options):
        nombres = (options.get('nombres') or '').strip()
        apellidos = (options.get('apellidos') or '').strip()
        nombre_completo = (options.get('nombre_completo') or '').strip()

        if nombres:
            return nombres, apellidos
        if nombre_completo:
            return nombre_completo, apellidos
        raise CommandError('Debes indicar --nombres o --nombre-completo.')

    def _obtener_o_crear_usuario(self, email, username, cedula, nombres, apellidos):
        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        if user:
            cambios = []
            if hasattr(user, 'first_name') and not user.first_name and nombres:
                user.first_name = nombres[:150]
                cambios.append('first_name')
            if hasattr(user, 'last_name') and not user.last_name and apellidos:
                user.last_name = apellidos[:150]
                cambios.append('last_name')
            if cambios:
                user.save(update_fields=cambios)
            return user

        username_final = username or slugify(email.split('@')[0]) or cedula
        base_username = username_final
        suffix = 2
        while User.objects.filter(username=username_final).exists():
            username_final = f'{base_username}-{suffix}'
            suffix += 1

        user = User.objects.create(
            username=username_final,
            email=email,
            first_name=nombres[:150],
            last_name=apellidos[:150],
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])
        return user

    def _adjuntar_placeholder(self, instance, field_name, filename, payload):
        getattr(instance, field_name).save(filename, ContentFile(payload), save=False)

    def _parse_date(self, value):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError as exc:
            raise CommandError('La fecha debe tener formato YYYY-MM-DD.') from exc

    def _to_decimal(self, value, field_name):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CommandError(f'Valor invalido para {field_name}: {value}') from exc

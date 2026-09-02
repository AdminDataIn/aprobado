from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_creditos.models import (
    ConfiguracionPagoBREB,
    calcular_hash_archivo,
)


class Command(BaseCommand):
    help = 'Crea o reutiliza de forma idempotente la configuracion privada de pago BRE-B.'

    def add_arguments(self, parser):
        parser.add_argument('--qr', required=True, help='Ruta local segura al QR oficial PNG/JPG.')
        parser.add_argument('--receptor', required=True)
        parser.add_argument('--entidad', required=True)
        parser.add_argument(
            '--tipo-llave',
            required=True,
            choices=ConfiguracionPagoBREB.TipoLlave.values,
        )
        parser.add_argument('--llave', required=True)
        parser.add_argument('--instrucciones', default='')
        parser.add_argument('--monto-minimo')
        estado = parser.add_mutually_exclusive_group(required=True)
        estado.add_argument('--activar', action='store_true')
        estado.add_argument('--inactivo', action='store_true')

    def handle(self, *args, **options):
        ruta_qr = Path(options['qr']).expanduser().resolve()
        if not ruta_qr.is_file():
            raise CommandError('La ruta indicada no contiene un archivo QR legible.')
        if ruta_qr.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
            raise CommandError('El QR debe ser un archivo PNG, JPG o JPEG.')

        try:
            monto_minimo = (
                Decimal(options['monto_minimo']).quantize(Decimal('0.01'))
                if options.get('monto_minimo')
                else None
            )
        except (InvalidOperation, ValueError) as exc:
            raise CommandError('El monto minimo no es valido.') from exc
        if monto_minimo is not None and monto_minimo <= 0:
            raise CommandError('El monto minimo debe ser mayor a cero.')

        contenido = ruta_qr.read_bytes()
        archivo = SimpleUploadedFile(
            ruta_qr.name,
            contenido,
            content_type='image/png' if ruta_qr.suffix.lower() == '.png' else 'image/jpeg',
        )
        hash_qr = calcular_hash_archivo(archivo)
        datos = {
            'nombre_receptor': options['receptor'].strip(),
            'entidad_financiera': options['entidad'].strip(),
            'tipo_llave': options['tipo_llave'],
            'llave_mostrable': options['llave'].strip(),
            'instrucciones': options['instrucciones'].strip(),
            'monto_minimo': monto_minimo,
        }
        if not datos['nombre_receptor'] or not datos['entidad_financiera'] or not datos['llave_mostrable']:
            raise CommandError('Receptor, entidad y llave no pueden estar vacios.')

        nombre_guardado = None
        try:
            with transaction.atomic():
                configuraciones = ConfiguracionPagoBREB.objects.select_for_update()
                configuracion = configuraciones.filter(hash_qr=hash_qr, **datos).first()
                creada = configuracion is None
                if creada:
                    configuracion = ConfiguracionPagoBREB(activo=False, **datos)
                    configuracion.qr = archivo
                    configuracion.full_clean()
                    configuracion.save()
                    nombre_guardado = configuracion.qr.name

                debe_activar = bool(options['activar'])
                if debe_activar:
                    configuraciones.filter(activo=True).exclude(pk=configuracion.pk).update(activo=False)
                if configuracion.activo != debe_activar:
                    ConfiguracionPagoBREB.objects.filter(pk=configuracion.pk).update(
                        activo=debe_activar,
                    )
                    configuracion.activo = debe_activar
        except ValidationError as exc:
            if nombre_guardado:
                ConfiguracionPagoBREB._meta.get_field('qr').storage.delete(nombre_guardado)
            raise CommandError(' '.join(exc.messages)) from exc
        except Exception:
            if nombre_guardado:
                ConfiguracionPagoBREB._meta.get_field('qr').storage.delete(nombre_guardado)
            raise

        accion = 'creada' if creada else 'reutilizada'
        estado_texto = 'activa' if configuracion.activo else 'inactiva'
        self.stdout.write(self.style.SUCCESS(
            f'Configuracion BRE-B {accion}: id={configuracion.pk}, estado={estado_texto}.'
        ))

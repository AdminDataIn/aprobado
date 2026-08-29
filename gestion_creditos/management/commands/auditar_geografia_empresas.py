from collections import Counter

from django.core.management.base import BaseCommand

from gestion_creditos.models import Empresa
from gestion_creditos.services.empresa_geografia import (
    describir_geografia_empresa,
)


class Command(BaseCommand):
    help = 'Audita la geografia usada por la presencia nacional de empresas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--incluir-inactivas',
            action='store_true',
            help='Incluye empresas sin convenio activo.',
        )

    def handle(self, *args, **options):
        empresas = Empresa.objects.order_by('id')
        if not options['incluir_inactivas']:
            empresas = empresas.filter(convenio_activo=True)

        filas = []
        municipios = Counter()
        for empresa in empresas:
            geografia = describir_geografia_empresa(empresa)
            clave = (
                geografia['departamento'] or '',
                geografia['municipio'] or '',
            )
            if geografia['municipio']:
                municipios[clave] += 1
            filas.append((empresa, geografia))

        encabezado = (
            'ID | EMPRESA | ACTIVA | PAIS | DEPARTAMENTO | MUNICIPIO | '
            'CIUDAD LEGACY | LAT EXPLICITA | LON EXPLICITA | REGISTRADA | '
            'REPRESENTABLE | ORIGEN | LAT FINAL | LON FINAL'
        )
        self.stdout.write(encabezado)
        for empresa, geografia in filas:
            self.stdout.write(' | '.join([
                str(empresa.id),
                empresa.nombre,
                'SI' if empresa.convenio_activo else 'NO',
                geografia['pais'] or '-',
                geografia['departamento'] or '-',
                geografia['municipio'] or '-',
                empresa.ciudad or '-',
                str(empresa.latitud) if empresa.latitud is not None else '-',
                str(empresa.longitud) if empresa.longitud is not None else '-',
                'SI' if geografia['ubicacion_registrada'] else 'NO',
                'SI' if geografia['ubicacion_representable'] else 'NO',
                geografia['fuente_coordenadas'] or 'NO_ENCONTRADA',
                str(geografia['latitud']) if geografia['latitud'] is not None else '-',
                str(geografia['longitud']) if geografia['longitud'] is not None else '-',
            ]))

        representables = {
            (geografia['departamento'], geografia['municipio'])
            for _, geografia in filas
            if geografia['ubicacion_representable']
        }
        sin_coordenadas = {
            (geografia['departamento'], geografia['municipio'])
            for _, geografia in filas
            if geografia['municipio'] and not geografia['ubicacion_representable']
        }
        duplicados = {clave: total for clave, total in municipios.items() if total > 1}

        self.stdout.write('')
        self.stdout.write(f'Empresas: {len(filas)}')
        self.stdout.write(f'Municipios unicos: {len(municipios)}')
        self.stdout.write(f'Representables: {len(representables)}')
        self.stdout.write(f'Sin coordenadas: {len(sin_coordenadas)}')
        self.stdout.write(f'Duplicados: {len(duplicados)}')
        for departamento, municipio in sorted(sin_coordenadas):
            self.stdout.write(f'NO REPRESENTABLE | {departamento} | {municipio}')
        for (departamento, municipio), total in sorted(duplicados.items()):
            self.stdout.write(
                f'DUPLICADO | {departamento} | {municipio} | {total} empresas'
            )

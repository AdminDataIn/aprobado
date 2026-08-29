import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from gestion_creditos.models import Empresa


logger = logging.getLogger(__name__)

DATASET_CENTROIDES_DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent / 'data' / 'municipios_colombia_centroides.json'
)
FUENTE_COORDENADAS_DATASET = 'DATASET_LOCAL_DANE_MGN_2024'
_CAMPOS_MUNICIPIO_REQUERIDOS = {
    'codigo_departamento',
    'departamento',
    'codigo_municipio',
    'municipio',
    'latitud',
    'longitud',
}
_CAMPOS_METADATA_REQUERIDOS = {'fuente', 'version_fuente', 'fecha_fuente'}

UBICACIONES_PLACEHOLDER = {
    'n a',
    'no aplica',
    'no registra',
    'sin departamento registrado',
    'sin municipio registrado',
    'sin ubicacion registrada',
}

_ALIAS_DEPARTAMENTOS = {
    'bogota dc': 'bogota d c',
    'bogota distrito capital': 'bogota d c',
    'distrito capital': 'bogota d c',
    'san andres providencia y santa catalina': (
        'archipielago de san andres providencia y santa catalina'
    ),
}

_ALIAS_MUNICIPIOS = {
    ('bogota d c', 'bogota'): 'bogota d c',
    ('bogota d c', 'bogota dc'): 'bogota d c',
    ('bolivar', 'cartagena'): 'cartagena de indias',
}

def _normalizar_clave(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r'[^a-zA-Z0-9\s]', ' ', texto).lower()
    return ' '.join(texto.split())


def _clave_departamento(valor):
    clave = _normalizar_clave(valor)
    return _ALIAS_DEPARTAMENTOS.get(clave, clave)


def _clave_municipio(departamento, municipio):
    departamento_clave = _clave_departamento(departamento)
    municipio_clave = _normalizar_clave(municipio)
    return _ALIAS_MUNICIPIOS.get(
        (departamento_clave, municipio_clave),
        municipio_clave,
    )


def _normalizar_texto_geografico(valor):
    texto = re.sub(r'\s+', ' ', str(valor or '').strip())
    if not texto or _normalizar_clave(texto) in UBICACIONES_PLACEHOLDER:
        return None
    return texto.title()


def normalizar_pais(valor):
    return _normalizar_texto_geografico(valor)


def normalizar_departamento(valor):
    return _normalizar_texto_geografico(valor)


def normalizar_municipio(valor):
    return _normalizar_texto_geografico(valor)


def _validar_coordenadas(latitud, longitud):
    try:
        latitud = float(latitud)
        longitud = float(longitud)
    except (TypeError, ValueError) as exc:
        raise ValueError('El dataset contiene coordenadas no numericas.') from exc
    if not -90 <= latitud <= 90 or not -180 <= longitud <= 180:
        raise ValueError('El dataset contiene coordenadas fuera de rango.')
    return latitud, longitud


@lru_cache(maxsize=4)
def _cargar_dataset_centroides(ruta_dataset):
    contenido = json.loads(Path(ruta_dataset).read_text(encoding='utf-8'))
    metadata = contenido.get('metadata')
    municipios = contenido.get('municipios')
    if not isinstance(metadata, dict) or not _CAMPOS_METADATA_REQUERIDOS.issubset(metadata):
        raise ValueError('El dataset no contiene metadata de fuente completa.')
    if not isinstance(municipios, list):
        raise ValueError('El dataset no contiene una lista de municipios valida.')

    centroides = {}
    for registro in municipios:
        if not isinstance(registro, dict) or not _CAMPOS_MUNICIPIO_REQUERIDOS.issubset(registro):
            raise ValueError('El dataset contiene un municipio incompleto.')
        departamento = normalizar_departamento(registro['departamento'])
        municipio = normalizar_municipio(registro['municipio'])
        codigo_departamento = str(registro['codigo_departamento'] or '').strip()
        codigo_municipio = str(registro['codigo_municipio'] or '').strip()
        if not departamento or not municipio or not codigo_departamento or not codigo_municipio:
            raise ValueError('El dataset contiene identificadores territoriales vacios.')
        latitud, longitud = _validar_coordenadas(registro['latitud'], registro['longitud'])
        clave = (
            _clave_departamento(departamento),
            _clave_municipio(departamento, municipio),
        )
        if clave in centroides:
            raise ValueError('El dataset contiene municipios duplicados por departamento y nombre.')
        centroides[clave] = (latitud, longitud)

    cantidad_declarada = metadata.get('cantidad_municipios')
    if cantidad_declarada is not None and int(cantidad_declarada) != len(centroides):
        raise ValueError('La cantidad declarada no coincide con los municipios del dataset.')
    return {
        'metadata': metadata,
        'centroides': centroides,
    }


def obtener_catalogo_centroides_municipales():
    ruta = str(getattr(
        settings,
        'EMPRESA_GEOGRAFIA_CENTROIDES_PATH',
        DATASET_CENTROIDES_DEFAULT_PATH,
    ))
    try:
        return _cargar_dataset_centroides(ruta)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning('Dataset municipal no disponible o invalido: %s', exc)
        return {'metadata': None, 'centroides': {}}


def _resolver_centroide(departamento, municipio):
    departamento_clave = _clave_departamento(departamento)
    municipio_clave = _clave_municipio(departamento, municipio)
    if not municipio_clave:
        return None
    catalogo = obtener_catalogo_centroides_municipales()
    return catalogo['centroides'].get((departamento_clave, municipio_clave))


def describir_geografia_empresa(empresa):
    pais = normalizar_pais(empresa.pais)
    departamento = normalizar_departamento(empresa.departamento)
    municipio = normalizar_municipio(empresa.municipio)
    ubicacion_registrada = bool(departamento or municipio)
    ubicacion_completa = bool(departamento and municipio)

    latitud = empresa.latitud
    longitud = empresa.longitud
    fuente_coordenadas = None
    if latitud is not None and longitud is not None:
        fuente_coordenadas = 'REGISTRADAS'
    elif ubicacion_registrada:
        centroide = _resolver_centroide(departamento, municipio)
        if centroide:
            latitud, longitud = centroide
            fuente_coordenadas = FUENTE_COORDENADAS_DATASET

    ubicacion_representable = bool(
        ubicacion_registrada and latitud is not None and longitud is not None
    )
    return {
        'pais': pais,
        'departamento': departamento,
        'municipio': municipio,
        'latitud': float(latitud) if latitud is not None else None,
        'longitud': float(longitud) if longitud is not None else None,
        'ubicacion_registrada': ubicacion_registrada,
        'ubicacion_completa': ubicacion_completa,
        'ubicacion_representable': ubicacion_representable,
        'fuente_coordenadas': fuente_coordenadas,
    }


def obtener_presencia_empresas(queryset=None, incluir_nombres_empresas=False):
    empresas = queryset if queryset is not None else Empresa.objects.all()
    grupos_registrados = {}
    con_ubicacion = 0
    sin_ubicacion = 0
    representables = 0
    departamentos = set()
    municipios = set()

    for empresa in empresas.order_by('id'):
        geografia = describir_geografia_empresa(empresa)
        if not geografia['ubicacion_registrada']:
            sin_ubicacion += 1
            continue

        con_ubicacion += 1
        departamento = geografia['departamento']
        municipio = geografia['municipio']
        if departamento:
            departamentos.add(departamento)
        if municipio:
            municipios.add(municipio)

        clave_registrada = (
            _clave_departamento(departamento),
            _clave_municipio(departamento, municipio),
        )
        grupo = grupos_registrados.setdefault(clave_registrada, {
            'departamento': departamento,
            'municipio': municipio,
            'latitud': geografia['latitud'],
            'longitud': geografia['longitud'],
            'empresas': 0,
            'ubicacion_representable': geografia['ubicacion_representable'],
            'fuente_coordenadas': geografia['fuente_coordenadas'],
            'nombres_empresas': [],
        })
        grupo['empresas'] += 1
        if incluir_nombres_empresas:
            grupo['nombres_empresas'].append(empresa.nombre)

        if not geografia['ubicacion_representable']:
            continue
        representables += 1
        if (
            not grupo['ubicacion_representable']
            or (
                geografia['fuente_coordenadas'] == 'REGISTRADAS'
                and grupo['fuente_coordenadas'] != 'REGISTRADAS'
            )
        ):
            grupo['latitud'] = geografia['latitud']
            grupo['longitud'] = geografia['longitud']
            grupo['fuente_coordenadas'] = geografia['fuente_coordenadas']
        grupo['ubicacion_representable'] = True

    def clave_orden(item):
        return (-item['empresas'], item['departamento'] or '', item['municipio'] or '')

    ubicaciones = sorted(grupos_registrados.values(), key=clave_orden)
    mapa_ubicaciones = []
    for grupo in ubicaciones:
        if not grupo['ubicacion_representable']:
            continue
        punto = {
            'departamento': grupo['departamento'],
            'municipio': grupo['municipio'],
            'latitud': grupo['latitud'],
            'longitud': grupo['longitud'],
            'empresas': grupo['empresas'],
        }
        if incluir_nombres_empresas:
            punto['nombres_empresas'] = sorted(set(grupo['nombres_empresas']))
        mapa_ubicaciones.append(punto)
    return {
        'ubicaciones': ubicaciones,
        'mapa_ubicaciones': mapa_ubicaciones,
        'top_zonas': ubicaciones[:8],
        'con_ubicacion_registrada': con_ubicacion,
        'con_ubicacion_representable': representables,
        'sin_ubicacion_registrada': sin_ubicacion,
        'departamentos_con_presencia': sorted(departamentos),
        'municipios_con_presencia': sorted(municipios),
        'total_empresas': con_ubicacion + sin_ubicacion,
    }

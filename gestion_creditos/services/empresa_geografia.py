import re
import unicodedata

from django.db.models import Count, Q

from gestion_creditos.models import Credito, Empresa


SUFIJOS_EMPRESARIALES = (
    'SAS',
    'S A S',
    'SA',
    'S A',
    'LTDA',
    'L T D A',
)

UBICACIONES_PLACEHOLDER = {
    'sin departamento registrado',
    'sin municipio registrado',
    'sin ciudad registrada',
    'sin ubicacion registrada',
    'sin ubicación registrada',
    'no registra',
    'n/a',
}

CENTROIDES_COLOMBIA = {
    'antioquia': (29, 36),
    'medellin': (29, 35),
    'bogota d.c.': (49, 55),
    'bogota': (49, 55),
    'cundinamarca': (49, 54),
    'casanare': (62, 51),
    'yopal': (63, 51),
    'meta': (56, 61),
    'villavicencio': (53, 58),
    'valle del cauca': (29, 60),
    'cali': (29, 61),
    'santander': (48, 40),
    'bucaramanga': (48, 41),
    'atlantico': (48, 15),
    'barranquilla': (48, 14),
    'bolivar': (42, 22),
    'cartagena': (39, 17),
}

CENTROIDES_COORDENADAS_COLOMBIA = {
    'antioquia': (6.2442, -75.5812),
    'medellin': (6.2442, -75.5812),
    'bogota d.c.': (4.7110, -74.0721),
    'bogota': (4.7110, -74.0721),
    'cundinamarca': (4.7110, -74.0721),
    'casanare': (5.3378, -72.3959),
    'yopal': (5.3378, -72.3959),
    'meta': (4.1420, -73.6266),
    'villavicencio': (4.1420, -73.6266),
    'valle del cauca': (3.4516, -76.5320),
    'cali': (3.4516, -76.5320),
    'santander': (7.1193, -73.1227),
    'bucaramanga': (7.1193, -73.1227),
    'atlantico': (10.9685, -74.7813),
    'barranquilla': (10.9685, -74.7813),
    'bolivar': (10.3910, -75.4794),
    'cartagena': (10.3910, -75.4794),
}

MAPA_SVG_VIEWBOX = {
    'min_lon': -79.1,
    'max_lon': -66.2,
    'min_lat': -4.2,
    'max_lat': 12.4,
    'width': 620,
    'height': 520,
    'scale': 29.156626506024093,
    'offset_x': 121.93524096385543,
    'offset_y': 18.0,
}

MAPA_LABEL_OFFSETS = {
    'villavicencio': (12, -10),
    'medellin': (12, -10),
    'yopal': (12, -10),
}


def _normalizar_texto_geografico(valor):
    texto = re.sub(r'\s+', ' ', (valor or '').strip())
    if not texto:
        return None
    if texto.lower() in UBICACIONES_PLACEHOLDER:
        return None
    return texto.title()


def normalizar_departamento(valor):
    return _normalizar_texto_geografico(valor)


def normalizar_municipio(valor):
    return _normalizar_texto_geografico(valor)


def normalizar_ciudad(valor):
    return _normalizar_texto_geografico(valor)


def normalizar_departamento_mapa(valor):
    texto = unicodedata.normalize('NFKD', valor or '')
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r'[^A-Za-z0-9]+', '-', texto.lower()).strip('-')
    equivalencias = {
        'bogota': 'bogota-d-c',
        'bogota-dc': 'bogota-d-c',
        'distrito-capital': 'bogota-d-c',
    }
    return equivalencias.get(texto, texto)


def normalizar_nombre_empresa_para_busqueda(valor):
    texto = unicodedata.normalize('NFKD', valor or '')
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r'[^A-Za-z0-9\s]', ' ', texto).upper()
    partes = [parte for parte in re.sub(r'\s+', ' ', texto).strip().split(' ') if parte]
    while partes and partes[-1] in SUFIJOS_EMPRESARIALES:
        partes.pop()
    return ' '.join(partes) or None


def _empresa_tiene_ubicacion_real(empresa):
    return bool(
        normalizar_departamento(empresa.departamento)
        or normalizar_municipio(empresa.municipio)
        or normalizar_ciudad(empresa.ciudad)
    )


def _iniciales_empresa(nombre):
    palabras = [
        palabra[0]
        for palabra in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+', nombre or '')
        if palabra
    ]
    return ''.join(palabras[:2]).upper() or 'AP'


def _ubicacion_empresa(empresa):
    partes = [
        normalizar_ciudad(empresa.ciudad) or normalizar_municipio(empresa.municipio),
        normalizar_departamento(empresa.departamento),
    ]
    return ', '.join(parte for parte in partes if parte) or None


def _clave_geografica(valor):
    texto = unicodedata.normalize('NFKD', valor or '')
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r'[^A-Za-z0-9\s\.]', ' ', texto).lower()
    return re.sub(r'\s+', ' ', texto).strip()


def _posicion_por_latitud_longitud(latitud, longitud):
    if latitud is None or longitud is None:
        return None
    try:
        latitud = float(latitud)
        longitud = float(longitud)
    except (TypeError, ValueError):
        return None

    # Rango aproximado de Colombia. No geocodifica; solo proyecta coordenadas ya registradas.
    min_lon, max_lon = -79.5, -66.5
    min_lat, max_lat = -4.5, 13.5
    x = ((longitud - min_lon) / (max_lon - min_lon)) * 100
    y = 100 - ((latitud - min_lat) / (max_lat - min_lat)) * 100
    return {
        'x': max(6, min(94, round(x, 1))),
        'y': max(6, min(94, round(y, 1))),
        'fuente': 'coordenadas_registradas',
    }


def _posicion_mapa_ubicacion(ubicacion):
    posicion = _posicion_por_latitud_longitud(ubicacion.get('latitud'), ubicacion.get('longitud'))
    if posicion:
        return posicion

    candidatos = [
        ubicacion.get('ciudad'),
        ubicacion.get('municipio'),
        ubicacion.get('departamento'),
    ]
    for candidato in candidatos:
        centroide = CENTROIDES_COLOMBIA.get(_clave_geografica(candidato))
        if centroide:
            return {
                'x': centroide[0],
                'y': centroide[1],
                'fuente': 'centroide_interno',
            }
    return None


def _coordenadas_mapa_ubicacion(ubicacion):
    if ubicacion.get('latitud') is not None and ubicacion.get('longitud') is not None:
        try:
            return {
                'latitud': float(ubicacion['latitud']),
                'longitud': float(ubicacion['longitud']),
                'fuente': 'coordenadas_registradas',
            }
        except (TypeError, ValueError):
            pass

    for candidato in (ubicacion.get('ciudad'), ubicacion.get('municipio'), ubicacion.get('departamento')):
        centroide = CENTROIDES_COORDENADAS_COLOMBIA.get(_clave_geografica(candidato))
        if centroide:
            return {
                'latitud': centroide[0],
                'longitud': centroide[1],
                'fuente': 'centroide_interno',
            }
    return None


def _posicion_svg_por_latitud_longitud(latitud, longitud):
    try:
        latitud = float(latitud)
        longitud = float(longitud)
    except (TypeError, ValueError):
        return None
    mapa = MAPA_SVG_VIEWBOX
    x = mapa['offset_x'] + (longitud - mapa['min_lon']) * mapa['scale']
    y = mapa['offset_y'] + (mapa['max_lat'] - latitud) * mapa['scale']
    return {
        'x': round(max(12, min(mapa['width'] - 12, x)), 1),
        'y': round(max(12, min(mapa['height'] - 12, y)), 1),
    }


def _punto_svg_ubicacion(ubicacion):
    coordenadas = ubicacion.get('coordenadas_mapa')
    if not coordenadas:
        return None
    posicion = _posicion_svg_por_latitud_longitud(coordenadas.get('latitud'), coordenadas.get('longitud'))
    if not posicion:
        return None
    ciudad = ubicacion.get('ciudad') or ubicacion.get('municipio') or ''
    departamento = ubicacion.get('departamento') or ''
    offset = MAPA_LABEL_OFFSETS.get(_clave_geografica(ciudad), (12, -10))
    empresas = ubicacion.get('empresas') or 1
    etiqueta = ', '.join(parte for parte in (ciudad or ubicacion.get('municipio'), departamento) if parte)
    return {
        'departamento': departamento,
        'municipio': ubicacion.get('municipio'),
        'ciudad': ciudad,
        'empresas': empresas,
        'creditos_activos': ubicacion.get('creditos_activos') or 0,
        'x': posicion['x'],
        'y': posicion['y'],
        'label_x': round(max(16, min(MAPA_SVG_VIEWBOX['width'] - 110, posicion['x'] + offset[0])), 1),
        'label_y': round(max(16, min(MAPA_SVG_VIEWBOX['height'] - 16, posicion['y'] + offset[1])), 1),
        'radio': round(max(5.5, min(12, 5.5 + empresas ** 0.5)), 1),
        'radio_glow': round(max(14, min(26, 14 + empresas ** 0.5)), 1),
        'etiqueta': etiqueta or departamento or 'Ubicacion registrada',
    }


def obtener_presencia_empresas(queryset=None):
    """
    Devuelve agregados geograficos listos para dashboards o mapa futuro.

    No inventa ubicaciones, no usa datos personales y no expone montos
    individuales. Las empresas sin ubicacion real se reportan aparte.
    """
    empresas = queryset if queryset is not None else Empresa.objects.all()
    empresas = empresas.order_by('id')

    ids_con_ubicacion = []
    ids_sin_ubicacion = []
    grupos = {}

    for empresa in empresas:
        if not _empresa_tiene_ubicacion_real(empresa):
            ids_sin_ubicacion.append(empresa.id)
            continue

        departamento = normalizar_departamento(empresa.departamento)
        municipio = normalizar_municipio(empresa.municipio)
        ciudad = normalizar_ciudad(empresa.ciudad)
        clave = (departamento or '', municipio or '', ciudad or '')
        grupos.setdefault(clave, {
            'departamento': departamento,
            'municipio': municipio,
            'ciudad': ciudad,
            'empresas': 0,
            'creditos_activos': 0,
            'latitud': empresa.latitud,
            'longitud': empresa.longitud,
            'empresa_ids': [],
        })
        if grupos[clave]['latitud'] is None and empresa.latitud is not None:
            grupos[clave]['latitud'] = empresa.latitud
        if grupos[clave]['longitud'] is None and empresa.longitud is not None:
            grupos[clave]['longitud'] = empresa.longitud
        grupos[clave]['empresas'] += 1
        grupos[clave]['empresa_ids'].append(empresa.id)
        ids_con_ubicacion.append(empresa.id)

    creditos_por_empresa = dict(
        Empresa.objects.filter(id__in=ids_con_ubicacion)
        .annotate(
            creditos_activos=Count(
                'creditolibranza__credito',
                filter=Q(creditolibranza__credito__estado__in=[
                    Credito.EstadoCredito.ACTIVO,
                    Credito.EstadoCredito.EN_MORA,
                ]),
            )
        )
        .values_list('id', 'creditos_activos')
    )

    ubicaciones = []
    for grupo in grupos.values():
        grupo['creditos_activos'] = sum(
            creditos_por_empresa.get(empresa_id, 0)
            for empresa_id in grupo.pop('empresa_ids')
        )
        grupo['posicion_mapa'] = _posicion_mapa_ubicacion(grupo)
        grupo['coordenadas_mapa'] = _coordenadas_mapa_ubicacion(grupo)
        ubicaciones.append(grupo)

    ubicaciones.sort(key=lambda item: (
        item['departamento'] or '',
        item['municipio'] or '',
        item['ciudad'] or '',
    ))
    departamentos = {}
    ciudades = {}
    for ubicacion in ubicaciones:
        departamento = ubicacion['departamento']
        ciudad = ubicacion['ciudad'] or ubicacion['municipio']
        if departamento:
            departamentos.setdefault(departamento, {'nombre': departamento, 'empresas': 0, 'creditos_activos': 0})
            departamentos[departamento]['empresas'] += ubicacion['empresas']
            departamentos[departamento]['creditos_activos'] += ubicacion['creditos_activos']
        if ciudad:
            clave_ciudad = (departamento or '', ciudad)
            ciudades.setdefault(clave_ciudad, {
                'nombre': ciudad,
                'departamento': departamento,
                'empresas': 0,
                'creditos_activos': 0,
            })
            ciudades[clave_ciudad]['empresas'] += ubicacion['empresas']
            ciudades[clave_ciudad]['creditos_activos'] += ubicacion['creditos_activos']

    departamentos_con_presencia = sorted(
        departamentos.values(),
        key=lambda item: (-item['empresas'], item['nombre']),
    )
    ciudades_con_presencia = sorted(
        ciudades.values(),
        key=lambda item: (-item['empresas'], item['departamento'] or '', item['nombre']),
    )
    top_zonas = sorted(
        ubicaciones,
        key=lambda item: (-item['empresas'], -item['creditos_activos'], item['departamento'] or ''),
    )[:8]
    mapa_ubicaciones = []
    mapa_puntos_svg = []
    for ubicacion in ubicaciones:
        coordenadas = ubicacion.get('coordenadas_mapa')
        if not coordenadas:
            continue
        mapa_ubicaciones.append({
            'departamento': ubicacion['departamento'],
            'municipio': ubicacion['municipio'],
            'ciudad': ubicacion['ciudad'],
            'empresas': ubicacion['empresas'],
            'creditos_activos': ubicacion['creditos_activos'],
            'latitud': coordenadas['latitud'],
            'longitud': coordenadas['longitud'],
            'fuente': coordenadas['fuente'],
        })
        punto_svg = _punto_svg_ubicacion(ubicacion)
        if punto_svg:
            mapa_puntos_svg.append(punto_svg)

    departamentos_mapa = [item['nombre'] for item in departamentos_con_presencia]

    return {
        'ubicaciones': ubicaciones,
        'mapa_ubicaciones': mapa_ubicaciones,
        'mapa_puntos_svg': mapa_puntos_svg,
        'departamentos_mapa': departamentos_mapa,
        'departamentos_mapa_slugs': [normalizar_departamento_mapa(nombre) for nombre in departamentos_mapa],
        'departamentos_con_presencia': departamentos_con_presencia,
        'ciudades_con_presencia': ciudades_con_presencia,
        'top_zonas': top_zonas,
        'con_ubicacion_registrada': len(ids_con_ubicacion),
        'sin_ubicacion_registrada': len(ids_sin_ubicacion),
        'total_empresas': len(ids_con_ubicacion) + len(ids_sin_ubicacion),
    }


def obtener_empresas_aliadas_visibles(queryset=None, limite=12):
    """
    Devuelve empresas activas para secciones visuales publicas.

    No expone NIT, contactos, correos, telefonos ni documentos. Si la empresa
    no tiene logo, entrega iniciales para una tarjeta institucional.
    """
    empresas = queryset if queryset is not None else Empresa.objects.filter(convenio_activo=True)
    empresas = (
        empresas
        .filter(convenio_activo=True)
        .only('nombre', 'logo', 'departamento', 'municipio', 'ciudad')
        .order_by('nombre')[:limite]
    )

    aliadas = []
    for empresa in empresas:
        logo_url = None
        if empresa.logo:
            try:
                logo_url = empresa.logo.url
            except Exception:
                logo_url = None
        aliadas.append({
            'nombre': empresa.nombre,
            'logo_url': logo_url,
            'iniciales': _iniciales_empresa(empresa.nombre),
            'ubicacion': _ubicacion_empresa(empresa),
        })
    return aliadas

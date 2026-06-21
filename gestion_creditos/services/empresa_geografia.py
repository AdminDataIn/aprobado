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

    return {
        'ubicaciones': ubicaciones,
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

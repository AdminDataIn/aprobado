"""Prepare a copy of the known ID-00 vhost. Never overwrites the source."""
import argparse
import hashlib
from pathlib import Path
import re


HOSTS = {b'aprobado.com.co', b'emprender.aprobado.com.co', b'contratistas.aprobado.com.co', b'market.aprobado.com.co'}
ALIAS = b'www.aprobado.com.co'
INCLUDE = b'include /etc/nginx/snippets/aprobado_documental_log_server.conf;'
BLOCK = re.compile(rb'(?m)^server[ \t]*\{')
NAMES = re.compile(rb'(?m)^([ \t]+)server_name[ \t]+([^;\r\n]+);[ \t]*(?=\r?$)')
TLS = re.compile(rb'(?m)^[ \t]*listen[ \t]+(?:[^\s;]+:)?443\b[^;\r\n]*\bssl\b')


def preparar(data):
    if INCLUDE in data:
        raise ValueError('El include documental ya esta presente')
    limites = [m.start() for m in BLOCK.finditer(data)] + [len(data)]
    formas = {(False, frozenset(HOSTS | {ALIAS})), (True, frozenset({ALIAS})),
              *((True, frozenset({h})) for h in HOSTS)}
    comprobadas = set()
    antes, despues = [data[:limites[0]]], [data[:limites[0]]]
    saltos = b'\r\n' if b'\r\n' in data else b'\n'
    for ini, fin in zip(limites, limites[1:]):
        bloque = data[ini:fin]
        nombres = list(NAMES.finditer(bloque))
        if len(nombres) != 1:
            raise ValueError('server_name inesperado')
        marca = nombres[0]
        tls = bool(TLS.search(bloque))
        hosts = frozenset(marca[2].split())
        forma = (tls, hosts)
        if forma not in formas - comprobadas:
            raise ValueError('Bloque fuera de la topologia autorizada')
        comprobadas.add(forma)
        if hosts == {ALIAS}:
            despues.append(bloque)
            antes.append(bloque)
            continue
        if re.search(rb'(?m)^[ \t]*access_log\b', bloque):
            raise ValueError('Existe un override de access_log; revisar antes de instalar')
        if tls and b'aprobado_protected_legacy.conf;' not in bloque:
            raise ValueError('Falta el include de proteccion documental')
        if not tls and not re.search(rb'(?m)^[ \t]*return[ \t]+30[18][ \t]+https://', bloque):
            raise ValueError('Redirect HTTP no acreditado')
        corte = marca.end()
        agregado = saltos + marca[1] + INCLUDE
        nuevo = bloque[:corte] + agregado + bloque[corte:]
        despues.append(nuevo)
        antes.append(nuevo[:corte] + nuevo[corte + len(agregado):])
    resultado = b''.join(despues)
    if comprobadas != formas or b''.join(antes) != data or resultado.count(INCLUDE) != 5:
        raise ValueError('Diff no limitado a cinco includes de logging')
    return resultado


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('fuente', type=Path)
    p.add_argument('destino', type=Path)
    p.add_argument('--sha256-base', required=True)
    a = p.parse_args()
    data = a.fuente.read_bytes()
    if hashlib.sha256(data).hexdigest() != a.sha256_base:
        p.error('La huella de la configuracion de origen cambio')
    try:
        salida = preparar(data)
        with a.destino.open('xb') as f:
            f.write(salida)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    print('VHOST_DIFF=5_INCLUDES_SOLO_LOGGING')

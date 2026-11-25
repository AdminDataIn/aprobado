"""Read official XLS metadata without executing Excel, VBA or XLM."""
import argparse
from collections import Counter
from getpass import getpass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from struct import unpack
import sys

import olefile


class AperturaBloqueada(Exception):
    """Expose only a stage code; library errors may contain workbook data."""


def registros(stream, limit):
    while stream.tell() < limit:
        header = stream.read(4)
        if len(header) != 4:
            raise ValueError('BIFF_TRUNCADO')
        code, size = unpack('<HH', header)
        data = stream.read(size)
        if len(data) != size or stream.tell() > limit:
            raise ValueError('BIFF_TRUNCADO')
        yield code, data


def inspeccionar(archivo):
    with olefile.OleFileIO(archivo) as ole:
        libro = next(name for name in ('Workbook', 'Book') if ole.exists(name))
        with ole.openstream(libro) as stream:
            code, bof = next(registros(stream, ole.get_size(libro)))
            if code != 0x0809 or bof[:4] != b'\x00\x06\x05\x00':
                raise ValueError('NO_BIFF8')
            info = {'contenedor': 'OLE/BIFF8', 'sha256': sha256(archivo.read_bytes()).hexdigest(),
                    'vba': any(part.casefold() in ('vba', '_vba_project_cur')
                               for path in ole.listdir(streams=True, storages=True) for part in path),
                    'filepass': False}
            for code, data in registros(stream, ole.get_size(libro)):
                if code == 0x002F:
                    info.update(filepass=True, tipo_cifrado=data[:6].hex())
                    break
                if code == 0x000A:
                    break
            return info


def inventariar(archivo, info, salida):
    import msoffcrypto
    from msoffcrypto.exceptions import InvalidKeyError
    import xlrd

    claro = BytesIO()
    if info['filepass']:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError('FALTA_CONSOLA_LOCAL_INTERACTIVA')
        with archivo.open('rb') as f:
            paquete = msoffcrypto.OfficeFile(f)
            clave = getpass('Contraseña de apertura XLS (entrada oculta): ')
            if not clave:
                raise AperturaBloqueada('CLAVE_NO_SUMINISTRADA')
            try:
                paquete.load_key(password=clave)
            except InvalidKeyError:
                raise AperturaBloqueada('CLAVE_NO_ACEPTADA') from None
            except Exception:
                raise AperturaBloqueada('VERIFICACION_XLS_NO_COMPLETADA') from None
            finally:
                del clave
            try:
                paquete.decrypt(claro)
            except Exception:
                raise AperturaBloqueada('DESCIFRADO_XLS_NO_COMPLETADO') from None
            finally:
                del paquete
    else:
        claro.write(archivo.read_bytes())

    # xlrd reads cached cells, not the formula or VBA execution engines.
    try:
        libro = xlrd.open_workbook(file_contents=claro.getvalue(), formatting_info=True, on_demand=True)
    except Exception:
        raise AperturaBloqueada('LECTURA_BIFF8_NO_COMPLETADA') from None
    info['hojas'] = []
    for indice in range(libro.nsheets):
        hoja = libro.sheet_by_index(indice)
        info['hojas'].append({
            'nombre': hoja.name, 'visibilidad': hoja.visibility, 'filas': hoja.nrows,
            'columnas': hoja.ncols, 'rangos_combinados': len(hoja.merged_cells),
            'tipos_celda': dict(Counter(c for n in range(hoja.nrows) for c in hoja.row_types(n) if c)),
        })
        libro.unload_sheet(indice)
    libro.release_resources()
    # Only structure, never decrypted workbook bytes or sheet data.
    with salida.open('x', encoding='utf-8') as destino:
        json.dump(info, destino, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archivo', type=Path)
    parser.add_argument('--estructura', type=Path, help='Ruta nueva protegida del inventario local JSON')
    args = parser.parse_args()
    try:
        info = inspeccionar(args.archivo)
        print(json.dumps(info))
        if args.estructura is not None:
            if args.estructura.exists():
                raise ValueError('DESTINO_EXISTENTE')
            inventariar(args.archivo, info, args.estructura)
            print('INVENTARIO_LOCAL=PASS; NO_MACROS=PASS')
    except AperturaBloqueada as exc:
        sys.exit(f'PLANTILLA=BLOCKED; CAUSA={exc}')
    except (OSError, ValueError, StopIteration):
        sys.exit('PLANTILLA=BLOCKED; falta una entrada válida o acceso local')
    except Exception:
        sys.exit('PLANTILLA=BLOCKED; autenticación o lectura autorizada no completada')


if __name__ == '__main__':
    main()

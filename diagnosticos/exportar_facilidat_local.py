"""Generate private, local workbooks without a database or Excel installation."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from gestion_creditos.services.facilidat_xlsx import xlsx_excepciones, xlsx_sintetico_facilidat


def exportar_pendientes(cierre_bytes, pendientes, trazas_bytes):
    cierre = json.loads(cierre_bytes)
    huella = sha256(cierre_bytes).hexdigest()
    if (pendientes['huella_cierre'] != huella or pendientes['corte'] != cierre['corte']
            or pendientes['huella_soportes'] != sha256(trazas_bytes).hexdigest()
            or pendientes['total'] != len(pendientes['filas'])):
        raise ValueError('HUELLAS_DE_ORIGEN_NO_COINCIDEN')
    trazas = json.loads(trazas_bytes)
    if trazas['corte'] != cierre['corte']:
        raise ValueError('CORTE_DE_TRAZAS_NO_COINCIDE')
    por_id = {row['credito_id_interno']: row for row in trazas['casos']}
    if len(por_id) != len(trazas['casos']):
        raise ValueError('TRAZAS_DUPLICADAS')
    for evidencia in pendientes['filas']:
        traza = por_id.get(evidencia['credito_id_interno'], {})
        if (evidencia['diferencias'] != traza.get('diferencias_pago')
                or evidencia['cierres_con_residuo'] != traza.get('cierres_con_residuo')):
            raise ValueError('IMPORTES_NO_COINCIDEN_CON_TRAZA')
    return xlsx_excepciones(cierre, huella, pendientes['filas'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destino', type=Path)
    parser.add_argument('--cierre', type=Path)
    parser.add_argument('--pendientes', type=Path)
    parser.add_argument('--trazas', type=Path)
    args = parser.parse_args()
    fuentes = (args.cierre, args.pendientes, args.trazas)
    if not args.destino.name.endswith('_NO_REMITIR.xlsx'):
        parser.error('Se requiere un destino nuevo con sufijo _NO_REMITIR.xlsx.')
    if any(fuentes):
        if not all(fuentes):
            parser.error('El informe requiere cierre, pendientes y trazas del mismo corte.')
        if any(f.resolve().parent != args.destino.resolve().parent for f in fuentes):
            parser.error('El informe debe permanecer en la carpeta custodiada de las fuentes.')
        contenido = exportar_pendientes(
            args.cierre.read_bytes(), json.loads(args.pendientes.read_bytes()), args.trazas.read_bytes(),
        )
    else:
        contenido = xlsx_sintetico_facilidat()
    with args.destino.open('xb') as destino:
        destino.write(contenido)
    print(f'XLSX_LOCAL=PASS SHA256={sha256(contenido).hexdigest()} REMISION=NO')


if __name__ == '__main__':
    main()

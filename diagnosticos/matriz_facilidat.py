"""Build a local mapping from the Excel COM dictionary, without reading customers."""
import argparse
from pathlib import Path
import json

from gestion_creditos.services.facilidat_homologacion import (
    AUTORRELLENO_EMPRESAS_EN_SME_POR_NOMBRE, NOVEDADES, PERFIL, PRESENTACION, REGLAS_COLUMNA,
)

# The template comments are joined at runtime. These are our source and release decisions.
MAPEO = {
    'A': ('CreditoLibranza.tipo_documento y soporte del titular', 'Convertir por catálogo, no asumir CC si falta evidencia.', 'POR_OBLIGACION'),
    'B': ('CreditoLibranza.cedula o identificación contractual', 'Texto; NIT/RUT sin DV ni signos; verificar titular y regla de pasaporte.', 'POR_OBLIGACION'),
    'C': ('Credito.numero_credito', 'Texto alfanumérico, verificar identidad, unicidad y límite publicado antes de remitir.', 'FUENTE'),
    'D': ('CreditoLibranza.nombres y apellidos', 'Nombre completo en mayúsculas, máximo 45; no abreviar ni truncar; resolver caracteres con instructivo.', 'POR_OBLIGACION'),
    'E': ('Soporte jurídico o de cartera de la situación', 'Usar catálogo; 0 solo conforme a la semántica documentada de normal/no conocimiento.', 'SIN_FUENTE_CONCILIADA'),
    'F': ('Cierre al corte: fecha_desembolso_registrada y desembolso_acreditado', 'Fecha de Bogotá con el DATE_FORMAT de la copia de trabajo.', 'FUENTE'),
    'G': ('Cierre: fecha_vencimiento_final', 'Fecha contractual final del plan válido al corte, no el siguiente vencimiento.', 'FUENTE'),
    'H': ('Titular/obligado del contrato firmado', '00 solo para obligado principal acreditado; no inferir ausencia de codeudores.', 'POR_OBLIGACION'),
    'I': ('HistorialEstado y documento de cancelación', '0 si sigue vigente; cierre voluntario 1 requiere acreditar la causa.', 'PARCIAL'),
    'J': ('Cierre: categoria, dias_mora e historial previo del operador', '01 al día, 05 pago total previamente reportado; 06/07/08/09 son bandas. No inferir límites, castigos ni comunicaciones.', 'PARCIAL'),
    'K': ('Condición de originación y ReestructuracionCredito', '0 solo con cartera normal acreditada; cotejar fecha y novedad cuando no sea normal.', 'PARCIAL'),
    'L': ('Fecha de originación o de la novedad K', 'Con K=0 reutilizar F. En los demás casos fecha efectiva de la novedad.', 'PARCIAL'),
    'M': ('Cierre: categoria y revisión de novedad', '1 al día, 2 mora, 3 pago total. Coherencia con I/J, sin asignar significado a 17.', 'PARCIAL'),
    'N': ('Fecha del fin del reporte en Facilidat', 'Debe igualar END_DATE de la sesión de reporte autorizada y el corte solicitado.', 'CONFIGURACION'),
    'O': ('Eventos jurídicos/cobranza con evidencia', 'Catalogar adjetivo solo si corresponde. 00 no equivale a ausencia de reclamo.', 'POR_OBLIGACION'),
    'P': ('Fecha efectiva de la evidencia del adjetivo', '00000000 cuando O=00; si aplica una novedad, usar su fecha acreditada.', 'PARCIAL'),
    'Q': ('Política vigente de calificación de cartera', 'No reutilizar scoring. La lista permite AA/BB/CC que la nota no describe; no inferir bandas.', 'SIN_FUENTE_CONCILIADA'),
    'R': ('Cierre: dias_mora', '000 para J=01; confirmar manejo >999 y consistencia de bandas antes de remitir.', 'PARCIAL'),
    'S': ('Giro y contrato: capital original desembolsado', 'Sin intereses; no sustituir automáticamente por aprobado, capital financiado ni total del plan.', 'POR_OBLIGACION'),
    'T': ('Capital pendiente del desembolso inicial y aplicación acreditada', 'Sin intereses. No usar saldo_pendiente del cierre ni equiparar capital del plan con el principal original mientras incluya cargos.', 'POR_OBLIGACION'),
    'U': ('Plan amortizable de Libranza no rotativa; cupo contractual en otras líneas', '0 en crédito no rotativo. No homologar automáticamente Prestadores o rotativos.', 'FUENTE_LIBRANZA'),
    'V': ('Cierre: cuota_pactada', 'Valor mensual contractual al corte; convertir con unidad certificada.', 'FUENTE'),
    'W': ('Cierre: saldo_vencido', 'Capital e intereses de cuotas vencidas; sin mora adicional ni reporte por inferencia.', 'FUENTE'),
    'X': ('Cierre: cuotas_pactadas', 'Entero del plan original o versión pertinente acreditada.', 'FUENTE'),
    'Y': ('Cierre: cuotas_pagadas', 'Cuotas satisfechas al corte; un abono parcial no es cuota cancelada.', 'FUENTE'),
    'Z': ('Cierre: cuotas_vencidas', 'Cuotas pendientes con vencimiento anterior al corte, sujetas a soporte del pagador.', 'FUENTE'),
    'AA': ('Cláusula contractual y producto', 'La nota prescribe 0 si no aplica. No deducir la inexistencia si el contrato no se verificó.', 'POR_OBLIGACION'),
    'AB': ('Vigencia de cláusula contractual', '00000000 solo si no hay cláusula, según la nota.', 'POR_OBLIGACION'),
    'AC': ('Cierre: fecha_cuota_mes_corte y dias_mora', 'Cuota contractual del mes si al día; fecha impagada más antigua si está vencida. No inventar fecha ausente.', 'PARCIAL'),
    'AD': ('Cierre: ultima_aplicacion y fecha real del soporte de pago', 'Último pago/abono al corte. 0 solo si la fuente acredita ausencia de pagos; no reemplaza un campo desconocido.', 'FUENTE'),
    'AE': ('Correspondencia del titular verificada', 'No usar municipio del pagador ni inferir por dirección libre.', 'SIN_FUENTE_CONCILIADA'),
    'AF': ('Municipio verificado del titular + DIVIPOLA', 'Relacionar código DANE en versión vigente; conservar ceros iniciales.', 'SIN_FUENTE_CONCILIADA'),
    'AG': ('Departamento de correspondencia del titular verificado', 'Validar correspondencia con AE y AF.', 'SIN_FUENTE_CONCILIADA'),
    'AH': ('CreditoLibranza.direccion', 'Verificar correspondencia vigente, transformar según comentario sin inventar datos.', 'POR_OBLIGACION'),
    'AI': ('CreditoLibranza.correo_electronico', 'Verificar que sea correo actual y que su entrega proceda.', 'POR_OBLIGACION'),
    'AJ': ('CreditoLibranza.telefono', 'Verificar que sea el celular; conservar representación textual.', 'POR_OBLIGACION'),
}

DOCUMENTOS = {
    PRESENTACION: 'a1f8cee48db69fe7e0dc92f5559ff70c08a74a2607dff077c632c4c5c7c3ff28',
    'Ejemplos Diligenciamiento Planilla': 'e7abb0a5599398321b9e9f70c71d7b5f05046665c1334a5a9b3edda01ad94d21',
    'Descripcion Tipologias de Cuenta o Cartera, v2.0': 'ccf35471b389973c2173a1132f88f2be8fe822ac75aef280ebe533b3f9d0c7df',
}


def referencias_documentales(letra):
    refs = [{'documento': 'Facilidat SME Resumida', 'referencia': f'Hoja1!{letra}4; catalogo de celda si existe'}]
    if letra in REGLAS_COLUMNA:
        documento, paginas, evidencia = REGLAS_COLUMNA[letra]
        refs.append({'documento': documento, 'paginas': paginas, 'evidencia': evidencia})
    if letra in AUTORRELLENO_EMPRESAS_EN_SME_POR_NOMBRE:
        refs.append({'documento': PRESENTACION, 'paginas': [27, 28, 29] if letra == 'Q' else [22, 28, 29],
                     'evidencia': 'Campo automatico por nombre en Empresas; no se acredita ejecucion en SME.'})
    if letra in ('A', 'B', 'C', 'D', 'F', 'G', 'H'):
        refs.append({'documento': PRESENTACION, 'paginas': [22, 23], 'evidencia': 'Datos esenciales en Empresas.'})
    if letra in ('AE', 'AF', 'AG', 'AH', 'AI', 'AJ'):
        refs.append({'documento': PRESENTACION, 'paginas': [22, 25],
                     'evidencia': 'Demografia de Empresas: no copiar letras ni asumir obligatoriedad en SME.'})
    return refs


def grupo(letra):
    if letra in AUTORRELLENO_EMPRESAS_EN_SME_POR_NOMBRE:
        return 'AUTOMATICO_EMPRESAS_PENDIENTE_PRUEBA_SME'
    if letra in ('AE', 'AF', 'AG', 'AH', 'AI', 'AJ'):
        return 'SUMINISTRO_CONDICIONADO_A_SOPORTE'
    return 'SUMINISTRADO_POR_LA_FUENTE'


def enriquecer_diccionario(libro):
    return {
        **libro, 'documentos_sha256': DOCUMENTOS, 'perfil_propuesto': PERFIL,
        'novedades_documentadas': NOVEDADES,
        'campos': [{**c, 'grupo': grupo(c['columna']), 'fuentes': referencias_documentales(c['columna'])}
                   for c in libro['campos']],
    }

def construir_matriz(libro):
    if [c['columna'] for c in libro['campos']] != list(MAPEO):
        raise ValueError('Orden Facilidat no conciliado con la estructura leída')
    campos, catalogos = [], {}
    for campo in libro['campos']:
        letra = campo['columna']
        fuente, transformacion, cobertura = MAPEO[letra]
        reglas = [v['regla'] for v in campo['validaciones'] if v['regla']['tipo_excel'] == 3]
        if reglas:
            firmas = {(r['formula1'], r['mensaje_entrada']) for r in reglas}
            if len(firmas) != 1:
                raise ValueError(f'Listas divergentes de Excel: {letra}')
            lista, significado = firmas.pop()
            catalogos[letra] = {'valores': [v.strip() for v in lista.split(';')], 'descripciones': significado}
        campos.append({
            'columna': letra, 'encabezado': campo['encabezado'], 'comentario': campo['comentario'],
            'catalogo': letra if reglas else None, 'fuente_django': fuente, 'transformacion': transformacion,
            'validacion': campo['validaciones'], 'cobertura': cobertura,
            'tipo_formato_excel': campo['formato_excel'], 'formulas': campo['formulas'],
            'longitud_documentada': {'D': 45, 'R': 3}.get(letra), 'obligatoriedad': 'NO_DETERMINADA_POR_COM',
            'grupo': grupo(letra), 'fuentes': referencias_documentales(letra),
        })
    return {
        'sha256_plantilla': libro['sha256'], 'ultima_fila_inspeccionada': libro['ultima_fila_inspeccionada'],
        'estado': 'PARCIAL_NO_REMITIR', 'columnas': campos, 'catalogos': catalogos,
        'documentos_sha256': DOCUMENTOS, 'perfil_propuesto': PERFIL, 'novedades_documentadas': NOVEDADES,
        'saldos_y_elegibilidad': 'Reutilizar el cierre SME; sin enviar ni homologar esta salida como remisión.',
        'reclamos': 'Sin columna independiente en A:AJ. Falta confirmar cómo registrar y conciliar el dato en disputa.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diccionario', type=Path)
    parser.add_argument('salida', type=Path)
    parser.add_argument('--diccionario-homologado', type=Path)
    args = parser.parse_args()
    if args.salida.resolve().parent != args.diccionario.resolve().parent:
        parser.error('La matriz debe permanecer al lado del diccionario custodiado.')
    libro = json.loads(args.diccionario.read_text(encoding='utf-8-sig'))
    matriz = construir_matriz(libro)
    if args.diccionario_homologado:
        destino_diccionario = args.diccionario_homologado.resolve()
        if destino_diccionario.parent != args.diccionario.resolve().parent or destino_diccionario == args.salida.resolve():
            parser.error('El nuevo diccionario debe quedar separado y bajo la misma custodia.')
        with destino_diccionario.open('x', encoding='utf-8') as dst:
            json.dump(enriquecer_diccionario(libro), dst, ensure_ascii=False, indent=2)
    with args.salida.open('x', encoding='utf-8') as dst:
        json.dump(matriz, dst, ensure_ascii=False, indent=2)
    print(f"MATRIZ=PARCIAL | COLUMNAS={len(matriz['columnas'])} | CATALOGOS={len(matriz['catalogos'])}")

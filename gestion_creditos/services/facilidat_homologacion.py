"""Documented SME preparation rules. Internal projections never grant remittance."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal


PRESENTACION = 'Presentacion Primera Entrega de Informacion'
EJEMPLOS = 'Ejemplos Diligenciamiento Planilla'
TIPOLOGIAS = 'Descripcion Tipologias de Cuenta o Cartera, v2.0'
PERFIL = {
    'version': 'facilidat-sme-preparacion-2026-11-25-v1',
    'suscriptor_referencia': '287922',   # Filename; not the operative subscriber configuration.
    'tipologia_propuesta_libranza': 'LBD',
    'tipologia_asignada': None,
    'nombre_abreviado_asignado': None,
    'tipo_obligacion_asignado': None,
    'garantia_asignada': None,
    'primera_entrega_acreditada': None,
    'proceso_referencia_empresas': 'Actualizacion Mensual',
    'entrega_referencia_empresas': 'Actualizacion Total',
    'formato_fecha_referencia': 'AAAAMMDD',
    'valores_referencia_empresas': 'Pesos',
    'termino_referencia_empresas': 'Definido',
    'moneda_referencia_empresas': 'Legal',
}
NOVEDADES = {
    '01': 'Al dia', '05': 'Pago voluntario o total', '06': 'Mora 30', '07': 'Mora 60',
    '08': 'Mora 90', '09': 'Mora 120 o mas', '12': 'Dudoso recaudo', '13': 'Cartera castigada',
}
AUTORRELLENO_EMPRESAS_EN_SME_POR_NOMBRE = frozenset('E R I L M N O P AA AB Q'.split())
REGLAS_COLUMNA = {
    'J': (PRESENTACION, (24,), '01 al dia; 05 pago total conciliado y previamente reportado. No fijar limites de bandas.'),
    'S': (EJEMPLOS, (1,), 'Capital original desembolsado sin intereses; no usar aprobado ni capital con cargos.'),
    'T': (EJEMPLOS, (1,), 'Capital pendiente del desembolso original, sin intereses; no sustituir por saldo bruto del plan.'),
    'U': (EJEMPLOS, (1,), 'Cero en credito no rotativo acreditado.'),
    'V': (EJEMPLOS, (1,), 'Cuota pactada: capital mas intereses.'),
    'W': (EJEMPLOS, (1,), 'Cuotas vencidas impagadas y cargos acreditados. No generar nuevos cargos.'),
    'X': (EJEMPLOS, (1,), 'Numero original de cuotas pactadas.'),
    'Y': (EJEMPLOS, (1,), 'Cuotas pagadas al corte; no contar parciales.'),
    'Z': (EJEMPLOS, (1,), 'Cuotas vencidas pendientes.'),
    'AC': (EJEMPLOS, (1,), 'Cuota del mes si al dia; cuota impagada mas antigua si vencida.'),
    'AD': (EJEMPLOS, (1,), 'Ultimo pago efectivo al corte; 0 cuando se acredita que no hubo pagos.'),
}


def fecha_corte_facilidat(valor):
    corte = valor if isinstance(valor, date) else date.fromisoformat(valor)
    if corte.day != monthrange(corte.year, corte.month)[1]:
        raise ValueError('FACILIDAT_REQUIERE_ULTIMO_DIA_DEL_MES')
    return corte


def candidato_positivo(fila):
    return (fila.get('desembolso_acreditado') is True and fila.get('categoria') == 'AL_DIA'
            and not fila.get('incidencia_financiera')
            and Decimal(fila.get('saldo_pendiente') or '0') > 0)


def novedad_preparacion(fila, *, actualizacion=False):
    if candidato_positivo(fila):
        return '01'
    if (actualizacion and fila.get('desembolso_acreditado') is True and not fila.get('incidencia_financiera')
            and fila.get('categoria') == 'PAGADA' and fila.get('saldo_pendiente') is not None
            and Decimal(fila['saldo_pendiente']) == 0 and fila.get('reporte_previo_acreditado') is True):
        return '05'
    return None


def ventana_operativa_mora(dias):
    if type(dias) is not int or dias <= 0:
        return 'EDAD_NO_ACREDITADA'
    if dias < 30:
        return 'MENOS_DE_30_DIAS'
    if dias >= 540:
        return 'FUERA_DE_VENTANA_OPERATIVA_540'
    return 'EDAD_OPERATIVA_30_A_539'


def escenarios_primera_entrega(filas):
    positivos = [f for f in filas if candidato_positivo(f)]
    mora = [f for f in filas if f.get('desembolso_acreditado') is True and f.get('categoria') == 'VENCIDA'
            and not f.get('incidencia_financiera')]
    # The ratio never selects a subset of negatives, and cannot replace individual evidence.
    return positivos, mora, len(positivos) // 2


def _codigo_fecha(valor):
    return date.fromisoformat(str(valor)).strftime('%Y%m%d') if valor else None


def _numero(valor):
    return format(Decimal(valor), 'f') if valor is not None else None


def campos_preparacion(fila, corte, *, actualizacion=False):
    """Project only sourced values, keeping unsupported fields absent."""
    if fila.get('desembolso_acreditado') is not True or fila.get('incidencia_financiera'):
        return {}
    categoria = fila.get('categoria')
    if categoria not in ('AL_DIA', 'VENCIDA', 'PAGADA'):
        return {}
    dias = fila.get('dias_mora')
    limite = None
    if categoria == 'VENCIDA' and type(dias) is int and dias > 0:
        limite = corte - timedelta(days=dias)
    elif categoria == 'AL_DIA':
        limite = fila.get('fecha_cuota_mes_corte')
    ultimo_pago = fila.get('ultima_aplicacion')
    pago = _codigo_fecha(ultimo_pago) if ultimo_pago else ('0' if 'ultima_aplicacion' in fila else None)
    return {
        'C': fila.get('numero_obligacion_interno'),
        'F': _codigo_fecha(fila.get('fecha_desembolso_registrada')),
        'G': _codigo_fecha(fila.get('fecha_vencimiento_final')),
        'J': novedad_preparacion(fila, actualizacion=actualizacion),
        'S': _numero(fila.get('capital_original_desembolsado_acreditado')),
        'T': _numero(fila.get('saldo_capital_original_acreditado')),
        'U': '0' if fila.get('producto') == 'LIBRANZA' and fila.get('cuotas_pactadas') else None,
        'V': _numero(fila.get('cuota_pactada')),
        'W': _numero(fila.get('saldo_vencido')),
        'X': _numero(fila.get('cuotas_pactadas')),
        'Y': _numero(fila.get('cuotas_pagadas')),
        'Z': _numero(fila.get('cuotas_vencidas')),
        'AC': _codigo_fecha(limite),
        'AD': pago,
    }

import hashlib


def normalizar_numero_documento(numero_documento):
    return ''.join(caracter for caracter in str(numero_documento or '') if caracter.isdigit())


def enmascarar_documento(numero_documento):
    normalizado = normalizar_numero_documento(numero_documento)
    if not normalizado:
        return ''
    if len(normalizado) <= 4:
        return '*' * len(normalizado)
    return f"{'*' * (len(normalizado) - 4)}{normalizado[-4:]}"


def hash_documento(tipo_documento, numero_documento):
    tipo = str(tipo_documento or '').strip().upper()
    normalizado = normalizar_numero_documento(numero_documento)
    base = f'{tipo}:{normalizado}'
    return hashlib.sha256(base.encode('utf-8')).hexdigest()


def normalizar_score_0_1000(score, *, minimo=0, maximo=1000):
    if score is None:
        return None
    score = int(score)
    if maximo <= minimo:
        return None
    normalizado = round(((score - minimo) / (maximo - minimo)) * 1000)
    return max(0, min(1000, normalizado))


def construir_metadata_segura(entrada, *, fuente, proveedor=None, escenario=None):
    metadata = {
        'solicitud_id': entrada.solicitud_id,
        'documento_hash': hash_documento(entrada.tipo_documento, entrada.numero_documento),
        'documento_enmascarado': enmascarar_documento(entrada.numero_documento),
        'fuente': fuente,
    }
    if proveedor:
        metadata['proveedor'] = proveedor
    if escenario:
        metadata['escenario_mock'] = escenario
    return metadata

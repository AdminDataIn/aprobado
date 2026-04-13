import re


def normalize_name_upper(value):
    """
    Normaliza un nombre para persistirlo y mostrarlo en MAYUSCULA.
    """
    normalized = re.sub(r'\s+', ' ', str(value or '').strip())
    return normalized.upper()


def build_full_name_upper(*parts):
    tokens = [normalize_name_upper(part) for part in parts if normalize_name_upper(part)]
    return ' '.join(tokens).strip()

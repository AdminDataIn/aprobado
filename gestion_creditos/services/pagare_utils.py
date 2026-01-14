"""
Utilidades para generación de pagarés.
Incluye conversión de números a letras (español colombiano).
"""

from decimal import Decimal, ROUND_HALF_UP


def formatear_cop(valor):
    """
    Formatea un valor numerico a formato COP: miles con '.' y decimales con ','.
    """
    if valor is None:
        return "0,00"

    try:
        valor_decimal = Decimal(str(valor))
    except Exception:
        return "0,00"

    valor_decimal = valor_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    signo = "-" if valor_decimal < 0 else ""
    valor_abs = abs(valor_decimal)
    parte_entera = int(valor_abs)
    parte_decimal = int((valor_abs - Decimal(parte_entera)) * 100)
    entero_formateado = f"{parte_entera:,}".replace(",", ".")
    return f"{signo}{entero_formateado},{parte_decimal:02d}"


def numero_a_letras(numero):
    """
    Convierte un número decimal a su representación en letras (español colombiano).

    Args:
        numero (float o int): Número a convertir (ej: 1500000.50)

    Returns:
        str: Número en letras (ej: "UN MILLÓN QUINIENTOS MIL PESOS CON CINCUENTA CENTAVOS M/CTE")

    Ejemplos:
        >>> numero_a_letras(1500000)
        'UN MILLÓN QUINIENTOS MIL PESOS CON CERO CENTAVOS M/CTE'

        >>> numero_a_letras(2450000.75)
        'DOS MILLONES CUATROCIENTOS CINCUENTA MIL PESOS CON SETENTA Y CINCO CENTAVOS M/CTE'
    """

    # Separar parte entera y decimal
    numero_float = float(numero)
    parte_entera = int(numero_float)
    parte_decimal = int(round((numero_float - parte_entera) * 100))

    # Convertir parte entera
    if parte_entera == 0:
        letras_entero = "CERO"
    else:
        letras_entero = _convertir_numero_a_letras(parte_entera)

    # Convertir parte decimal (centavos)
    if parte_decimal == 0:
        letras_decimal = "CERO CENTAVOS"
    else:
        letras_decimal = _convertir_numero_a_letras(parte_decimal) + " CENTAVOS"

    return f"{letras_entero} PESOS CON {letras_decimal} M/CTE"


def _convertir_numero_a_letras(numero):
    """
    Función auxiliar para convertir un número entero a letras.

    Args:
        numero (int): Número entero a convertir

    Returns:
        str: Número en letras
    """

    # Casos especiales
    if numero == 0:
        return "CERO"

    # Arrays de conversión
    UNIDADES = [
        "", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"
    ]

    ESPECIALES = [
        "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE",
        "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE"
    ]

    DECENAS = [
        "", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA",
        "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"
    ]

    CENTENAS = [
        "", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS",
        "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"
    ]

    # Procesar número por grupos de 3 dígitos (miles, millones, etc.)
    if numero < 10:
        return UNIDADES[numero]

    elif numero < 20:
        return ESPECIALES[numero - 10]

    elif numero < 100:
        decena = numero // 10
        unidad = numero % 10

        if decena == 2 and unidad != 0:
            # Caso especial: VEINTIUNO, VEINTIDÓS, etc.
            return f"VEINTI{UNIDADES[unidad]}"
        elif unidad == 0:
            return DECENAS[decena]
        else:
            return f"{DECENAS[decena]} Y {UNIDADES[unidad]}"

    elif numero < 1000:
        centena = numero // 100
        resto = numero % 100

        # Caso especial: 100 = "CIEN" (no "CIENTO")
        if numero == 100:
            return "CIEN"

        if resto == 0:
            return CENTENAS[centena]
        else:
            return f"{CENTENAS[centena]} {_convertir_numero_a_letras(resto)}"

    elif numero < 1_000_000:
        miles = numero // 1000
        resto = numero % 1000

        # Caso especial: 1000 = "MIL" (no "UNO MIL")
        if miles == 1:
            texto_miles = "MIL"
        else:
            texto_miles = f"{_convertir_numero_a_letras(miles)} MIL"

        if resto == 0:
            return texto_miles
        else:
            return f"{texto_miles} {_convertir_numero_a_letras(resto)}"

    elif numero < 1_000_000_000:
        millones = numero // 1_000_000
        resto = numero % 1_000_000

        # Caso especial: 1 millón = "UN MILLÓN"
        if millones == 1:
            texto_millones = "UN MILLÓN"
        else:
            texto_millones = f"{_convertir_numero_a_letras(millones)} MILLONES"

        if resto == 0:
            return texto_millones
        else:
            return f"{texto_millones} {_convertir_numero_a_letras(resto)}"

    else:
        # Números mayores a mil millones
        miles_millones = numero // 1_000_000_000
        resto = numero % 1_000_000_000

        if miles_millones == 1:
            texto_miles_millones = "MIL MILLONES"
        else:
            texto_miles_millones = f"{_convertir_numero_a_letras(miles_millones)} MIL MILLONES"

        if resto == 0:
            return texto_miles_millones
        else:
            return f"{texto_miles_millones} {_convertir_numero_a_letras(resto)}"


def numero_a_letras_simple(numero):
    """
    Convierte un número entero a letras (sin la parte de pesos y centavos).
    Útil para representar cantidades de cuotas, días, etc.

    Args:
        numero (int): Número entero a convertir

    Returns:
        str: Número en letras en minúsculas (ej: "doce")

    Ejemplos:
        >>> numero_a_letras_simple(12)
        'doce'

        >>> numero_a_letras_simple(30)
        'treinta'
    """
    return _convertir_numero_a_letras(int(numero)).lower()

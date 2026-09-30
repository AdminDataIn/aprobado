"""Canonical input validation shared by application and correction forms."""
import re
import unicodedata

from django.core.exceptions import ValidationError


PLACEHOLDERS = {'prueba', 'test', 'xxx', 'asdf', 'sin nombre', 'sin direccion'}


def texto_normalizado(value):
    return ' '.join(unicodedata.normalize('NFC', str(value or '')).split())


def clave_texto(value):
    return ''.join(c for c in unicodedata.normalize('NFD', texto_normalizado(value).casefold())
                   if not unicodedata.combining(c))


def documento_numerico(value, tipo='CC'):
    value = str(value or '').strip()
    if tipo not in {'CC', 'CE'} or not re.fullmatch(r'[0-9]{6,12}', value):
        raise ValidationError('Ingresa un documento de 6 a 12 digitos, sin separadores ni letras.')
    return value


def celular_colombiano(value):
    value = texto_normalizado(value)
    # Same ten-digit contract used by Libranza, but never silently discard letters.
    if not re.fullmatch(r'[0-9 ()-]+', value):
        raise ValidationError('Ingresa un celular de 10 digitos, sin letras.')
    value = re.sub(r'[ ()-]', '', value)
    if not re.fullmatch(r'[0-9]{10}', value):
        raise ValidationError('El celular debe contener exactamente 10 numeros.')
    return value


def nombre_persona(value):
    value = texto_normalizado(value)
    if len(value) < 2 or not all(c.isalpha() or c in " '-’" for c in value) or not any(c.isalpha() for c in value):
        raise ValidationError('Ingresa un nombre sin numeros ni simbolos; puedes usar espacios, guion y apostrofe.')
    if clave_texto(value) in PLACEHOLDERS:
        raise ValidationError('Reemplaza el texto de prueba por tu nombre real.')
    return value


def direccion_personal(value):
    value = texto_normalizado(value)
    if len(value) < 5 or not any(c.isalpha() for c in value) or clave_texto(value) in PLACEHOLDERS:
        raise ValidationError('Ingresa una direccion urbana o rural con contenido significativo.')
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValidationError('La direccion contiene caracteres no permitidos.')
    return value


def digito_verificacion_nit(base):
    # DIAN Orden Administrativa 4/1989, anexo 3: weighted modulo 11.
    if not re.fullmatch(r'[0-9]{1,15}', base):
        raise ValidationError('NIT invalido.')
    pesos = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
    residuo = sum(int(d) * p for d, p in zip(reversed(base), pesos)) % 11
    return str(residuo if residuo < 2 else 11 - residuo)


def normalizar_nit(value):
    value = re.sub(r'\s', '', str(value or ''))
    if not re.fullmatch(r'(?:[0-9]{1,15}|[0-9]{1,3}(?:\.[0-9]{3}){1,4})(?:-[0-9])?', value):
        raise ValidationError('La estructura del NIT no es valida.')
    base, _, dv = value.replace('.', '').partition('-')
    if dv and dv != digito_verificacion_nit(base):
        raise ValidationError('El digito de verificacion del NIT no coincide.')
    return base + ('-' + dv if dv else '')


VALIDADORES_PERSONALES = {
    'nombres': nombre_persona, 'apellidos': nombre_persona,
    'celular': celular_colombiano, 'direccion': direccion_personal,
}


def validar_campos_personales(form, cleaned):
    for nombre, validar in VALIDADORES_PERSONALES.items():
        if nombre in cleaned and (cleaned[nombre] or form.fields[nombre].required):
            try:
                cleaned[nombre] = validar(cleaned[nombre])
            except ValidationError as error:
                form.add_error(nombre, error)
    if 'numero_documento' in cleaned:
        try:
            cleaned['numero_documento'] = documento_numerico(cleaned['numero_documento'], cleaned.get('tipo_documento'))
        except ValidationError as error:
            form.add_error('numero_documento', error)

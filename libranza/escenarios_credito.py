from django.core.exceptions import ValidationError
from django.db import models


NUEVO_CREDITO = 'NUEVO_CREDITO'
SEGUNDO_CREDITO = 'SEGUNDO_CREDITO'
RECOGIDA_CARTERA = 'RECOGIDA_CARTERA'

LABEL_NUEVO_CREDITO = 'Nuevo credito'
LABEL_SEGUNDO_CREDITO = 'Segundo credito'
LABEL_RECOGIDA_CARTERA = 'Recogida de cartera'


class EscenarioCreditoLibranza(models.TextChoices):
    NUEVO_CREDITO = NUEVO_CREDITO, LABEL_NUEVO_CREDITO
    SEGUNDO_CREDITO = SEGUNDO_CREDITO, LABEL_SEGUNDO_CREDITO
    RECOGIDA_CARTERA = RECOGIDA_CARTERA, LABEL_RECOGIDA_CARTERA


ESCENARIOS_CREDITO_LIBRANZA = (
    NUEVO_CREDITO,
    SEGUNDO_CREDITO,
    RECOGIDA_CARTERA,
)

ESCENARIOS_CREDITO_LABELS = {
    NUEVO_CREDITO: LABEL_NUEVO_CREDITO,
    SEGUNDO_CREDITO: LABEL_SEGUNDO_CREDITO,
    RECOGIDA_CARTERA: LABEL_RECOGIDA_CARTERA,
}


def normalizar_escenario_credito(valor):
    escenario = str(valor or '').strip().upper()
    if escenario not in ESCENARIOS_CREDITO_LIBRANZA:
        raise ValidationError('El escenario de credito no es valido.')
    return escenario


def validar_escenario_credito(valor):
    normalizar_escenario_credito(valor)
    return True


def es_nuevo_credito(valor):
    try:
        return normalizar_escenario_credito(valor) == NUEVO_CREDITO
    except ValidationError:
        return False


def es_segundo_credito(valor):
    try:
        return normalizar_escenario_credito(valor) == SEGUNDO_CREDITO
    except ValidationError:
        return False


def es_recogida_cartera(valor):
    try:
        return normalizar_escenario_credito(valor) == RECOGIDA_CARTERA
    except ValidationError:
        return False


__all__ = [
    'ESCENARIOS_CREDITO_LABELS',
    'ESCENARIOS_CREDITO_LIBRANZA',
    'EscenarioCreditoLibranza',
    'LABEL_NUEVO_CREDITO',
    'LABEL_RECOGIDA_CARTERA',
    'LABEL_SEGUNDO_CREDITO',
    'NUEVO_CREDITO',
    'RECOGIDA_CARTERA',
    'SEGUNDO_CREDITO',
    'es_nuevo_credito',
    'es_recogida_cartera',
    'es_segundo_credito',
    'normalizar_escenario_credito',
    'validar_escenario_credito',
]

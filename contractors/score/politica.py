from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from contractors.models import BandaScorePrestador, ConfiguracionScorePrestador


class PoliticaScoreNoDisponible(ValidationError):
    pass


def obtener_politica_score_activa(fecha=None):
    fecha = fecha or timezone.localdate()
    politicas = list(
        ConfiguracionScorePrestador.objects.filter(
            activa=True,
            fecha_vigencia_desde__lte=fecha,
        ).filter(
            Q(fecha_vigencia_hasta__isnull=True) | Q(fecha_vigencia_hasta__gte=fecha)
        )[:2]
    )
    if not politicas:
        return None
    if len(politicas) > 1:
        raise PoliticaScoreNoDisponible('Existe mas de una politica de score aplicable.')
    try:
        validar_politica_score_completa(politicas[0])
    except ValidationError as exc:
        if isinstance(exc, PoliticaScoreNoDisponible):
            raise
        raise PoliticaScoreNoDisponible(
            'La politica activa no supera sus validaciones de integridad.'
        ) from exc
    return politicas[0]


def validar_politica_score_completa(configuracion):
    configuracion.full_clean()
    bandas = list(configuracion.bandas.order_by('score_min'))
    if len(bandas) != len(BandaScorePrestador.Nombre.values):
        raise PoliticaScoreNoDisponible('La politica debe tener exactamente cinco bandas.')
    nombres = {banda.nombre for banda in bandas}
    if nombres != set(BandaScorePrestador.Nombre.values):
        raise PoliticaScoreNoDisponible('La politica no contiene todas las bandas requeridas.')
    cursor = 0
    for banda in bandas:
        banda.full_clean()
        if banda.score_min != cursor:
            raise PoliticaScoreNoDisponible('Las bandas deben cubrir 0 a 1000 sin vacios.')
        cursor = (1000 if banda.score_max is None else banda.score_max) + 1
    if cursor != 1001:
        raise PoliticaScoreNoDisponible('Las bandas deben terminar en 1000.')

    esperados = {
        BandaScorePrestador.Nombre.PREMIUM: configuracion.score_premium_min,
        BandaScorePrestador.Nombre.ALTA: configuracion.score_alta_min,
        BandaScorePrestador.Nombre.MEDIA: configuracion.score_media_min,
        BandaScorePrestador.Nombre.ENTRADA: configuracion.score_entrada_min,
        BandaScorePrestador.Nombre.REVISION: 0,
    }
    for banda in bandas:
        if banda.score_min != esperados[banda.nombre]:
            raise PoliticaScoreNoDisponible(
                f'La banda {banda.nombre} no coincide con el umbral configurado.'
            )
    return configuracion


def buscar_banda(configuracion, score):
    if score is None:
        return None
    entero = int(score)
    return configuracion.bandas.filter(
        score_min__lte=entero,
    ).filter(Q(score_max__isnull=True) | Q(score_max__gte=entero)).first()

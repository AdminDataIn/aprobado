"""Versioned final-decision mandate, separate from score and income formulae."""
import hashlib
import json
from pathlib import Path

from django.core.exceptions import ValidationError

VERSION_POLITICA = 'prestadores-score-prod-v3-decision'
ARCHIVO = Path(__file__).resolve().parents[1] / 'docs' / 'politica_decision_prestadores_v3.json'
CAUSALES_IMPLEMENTADAS = {'BANDA_REVISION', 'CAPACIDAD_EXCEDIDA', 'MORA_SEVERA'}


def definicion_decision():
    try:
        datos = json.loads(ARCHIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValidationError('La definicion de decision no esta disponible.') from exc
    if (not isinstance(datos, dict)
            or set(datos) != {'version_politica', 'version_decision', 'fuente_normativa',
                      'aprobacion_automatica', 'exclusiones'}
            or datos['version_politica'] != VERSION_POLITICA
            or not isinstance(datos['version_decision'], str) or not datos['version_decision']
            or not isinstance(datos['fuente_normativa'], str) or not datos['fuente_normativa']
            or type(datos['aprobacion_automatica']) is not bool
            or not isinstance(datos['exclusiones'], list)
            or any(not isinstance(c, str) or c not in CAUSALES_IMPLEMENTADAS for c in datos['exclusiones'])
            or len(set(datos['exclusiones'])) != len(datos['exclusiones'])):
        raise ValidationError('La definicion de decision no es interpretable.')
    return datos


def huella_reglas(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reglas_publicadas(politica):
    from contractors.models import CambioPoliticaScorePrestadorAudit
    if politica.version_politica != VERSION_POLITICA:
        return None
    datos = definicion_decision()
    return datos if CambioPoliticaScorePrestadorAudit.objects.filter(
        politica_nueva=politica, snapshot_nuevo__decision_sha256=huella_reglas(datos),
        snapshot_nuevo__ingreso_publicado=True,
    ).exists() else None

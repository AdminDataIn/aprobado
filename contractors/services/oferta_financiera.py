"""Offer viability over persisted risk evidence; never queries providers or scores again."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from contractors.models import ContractorApplication, PredecisionPrestadorAudit
from contractors.score.dto import ResultadoScorePrestador
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
from contractors.services.politica_financiera_prestador import OfertaCalculada, preparar_oferta


def calcular_oferta_evaluada(solicitud, auditoria, *, politica, monto, plazo):
    """Project the existing score into the offer API, without reconstructing its calculation."""
    try:
        datos = (auditoria.snapshot_salida or {}).get('score_resultado') or {}
        variables = datos.get('variables_calculadas') or {}
        if (
            auditoria.solicitud_id != solicitud.pk
            or auditoria.estado_ejecucion != PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA
            or auditoria.resultado != PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
            or datos.get('version_score') != auditoria.version_score
            or datos.get('version_politica') != auditoria.version_politica
            or datos.get('requiere_revision_manual') is not False
            or datos.get('bloqueos') != []
            or auditoria.score is None
            or Decimal(str(datos.get('score_final'))) != auditoria.score
            or auditoria.cuota_mensual_hdc is None
            or Decimal(str(variables.get('obligaciones_mensuales'))) != auditoria.cuota_mensual_hdc
        ):
            return OfertaCalculada(banda='', estado='NO_EVALUABLE',
                                  motivo='Falta evidencia de riesgo o carga HDC evaluable para la oferta.')
        # Only the fields consumed by preparar_oferta are projected. Components and
        # penalties remain in the immutable audit and are not recalculated here.
        score = ResultadoScorePrestador(
            score_base=None, score_final=auditoria.score,
            banda=datos.get('banda'), variables_calculadas=variables,
            version_score=datos['version_score'], version_politica=datos['version_politica'],
            requiere_revision_manual=False, bloqueos=(), componentes=(), penalizaciones=(),
            razones=(), alertas=(),
        )
        corte = timezone.localdate()
        configuracion = politica.configuracion_financiera
        horizonte = horizonte_para_solicitud(solicitud, configuracion, corte=corte)
        if horizonte is None or not horizonte.disponible:
            return OfertaCalculada(banda=score.banda or '', estado='NO_EVALUABLE',
                                  motivo='El horizonte contractual no permite evaluar una oferta vigente.')
        return preparar_oferta(
            score=score, politica=politica,
            ingreso_neto=obtener_ingreso_neto_vigente(solicitud, corte=corte),
            obligaciones_mensuales=auditoria.cuota_mensual_hdc,
            monto_solicitado=monto, plazo_solicitado=plazo,
            horizonte=horizonte, configuracion=configuracion, corte=corte,
            solicitud_id=solicitud.pk,
        )
    except DatabaseError:
        raise
    except (ValidationError, ArithmeticError, ValueError, TypeError):
        return OfertaCalculada(banda='', estado='NO_EVALUABLE',
                              motivo='La evidencia o configuracion de la oferta requiere revision manual.')
    except Exception:
        # Do not expose exception messages, documents or internal provider data.
        return OfertaCalculada(banda='', estado='ERROR_CONTROLADO',
                              motivo='No fue posible validar tecnicamente la oferta financiera.')


def exigir_oferta_viable(oferta, *, monto=None, plazo=None):
    codigos = {
        'SIN_OFERTA': 'sin_oferta',
        'NO_EVALUABLE': 'oferta_no_evaluable',
        'ERROR_CONTROLADO': 'oferta_error_controlado',
    }
    if oferta.estado != 'OFERTA_CALCULADA':
        raise ValidationError(
            f'Oferta financiera {oferta.estado}: {oferta.motivo}',
            code=codigos.get(oferta.estado, 'oferta_error_controlado'),
        )
    if monto is not None and (oferta.monto != monto or oferta.plazo != plazo):
        raise ValidationError(
            'El monto y plazo autorizados no tienen una oferta financiera viable vigente.',
            code='condiciones_sin_oferta',
        )
    return oferta


@transaction.atomic
def validar_oferta_financiera_gate(gate):
    """Mandatory pre-origination fence, including direct payer/expediente callers."""
    from contractors.services.aprobacion_interna import _validar_gate

    # Income/document services lock this same application before changing evidence.
    solicitud = ContractorApplication.objects.select_for_update().get(pk=gate.solicitud_id)
    gate.solicitud = solicitud
    auditoria = gate.auditoria_predecision
    validacion = _validar_gate(solicitud, auditoria)
    if (
        gate.version_datos != auditoria.version_datos
        or gate.monto_autorizado > gate.monto_maximo_evaluado
        or gate.plazo_autorizado > gate.plazo_maximo_evaluado
    ):
        raise ValidationError('Los datos o la politica de la oferta cambiaron; requiere revision manual.',
                              code='oferta_no_evaluable')
    oferta = calcular_oferta_evaluada(
        solicitud, auditoria, politica=validacion.politica,
        monto=gate.monto_autorizado, plazo=gate.plazo_autorizado,
    )
    return exigir_oferta_viable(oferta, monto=gate.monto_autorizado, plazo=gate.plazo_autorizado)

from dataclasses import dataclass

from django.db import transaction

from contractors.services.datacredito_evaluacion import (
    MODO_NO_CONSULTAR,
    construir_metadata_timeline_datacredito,
    resolver_datacredito_para_solicitud_prestador,
)
from contractors.services.predecision import evaluar_predecision_contratista
from contractors.services.predecision_audit import crear_auditoria_predecision_prestador
from contractors.services.timeline import registrar_evento_timeline_prestador


@dataclass(frozen=True)
class ResultadoEvaluacionFormalPrestador:
    resultado: object
    auditoria: object

    @property
    def decision(self):
        return self.resultado.decision

    @property
    def elegible(self):
        return self.resultado.elegible


def evaluar_formalmente_solicitud_prestador(
    solicitud,
    usuario=None,
    request=None,
    modo_datacredito=MODO_NO_CONSULTAR,
    forzar_consulta=False,
    justificacion=None,
):
    modo_datacredito = 'FORZAR_CONSULTA' if forzar_consulta else modo_datacredito
    try:
        datacredito_contexto = resolver_datacredito_para_solicitud_prestador(
            solicitud=solicitud,
            modo=modo_datacredito,
            usuario=usuario,
            request=request,
            justificacion=justificacion,
        )
    except Exception:
        if modo_datacredito != MODO_NO_CONSULTAR:
            registrar_evento_timeline_prestador(
                solicitud=solicitud,
                tipo_evento='DATACREDITO_EVALUACION_FALLIDA',
                titulo='Evaluacion DataCredito fallida',
                descripcion='No fue posible resolver DataCredito de forma controlada.',
                metadata={'modo': modo_datacredito},
                usuario=usuario,
                request=request,
            )
        raise
    if datacredito_contexto.modo != MODO_NO_CONSULTAR:
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            tipo_evento='DATACREDITO_EVALUACION_INICIADA',
            titulo='Evaluacion DataCredito iniciada',
            descripcion='Se inicio resolucion controlada de snapshots DataCredito.',
            metadata=construir_metadata_timeline_datacredito(datacredito_contexto),
            usuario=usuario,
            request=request,
        )
        _registrar_eventos_datacredito_servicios(solicitud, datacredito_contexto, usuario=usuario, request=request)

    resultado = evaluar_predecision_contratista(
        solicitud,
        datacredito_resuelto=datacredito_contexto if datacredito_contexto.modo != MODO_NO_CONSULTAR else None,
    )
    with transaction.atomic():
        auditoria = crear_auditoria_predecision_prestador(
            solicitud,
            resultado,
            usuario=usuario,
            request=request,
            datacredito_contexto=datacredito_contexto if datacredito_contexto.modo != MODO_NO_CONSULTAR else None,
        )
    if datacredito_contexto.modo != MODO_NO_CONSULTAR:
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            tipo_evento='DATACREDITO_EVALUACION_COMPLETADA',
            titulo='Evaluacion DataCredito completada',
            descripcion='Se completo evaluacion formal con resolucion DataCredito.',
            estado_resultante=getattr(resultado, 'decision', ''),
            metadata=construir_metadata_timeline_datacredito(datacredito_contexto),
            usuario=usuario,
            request=request,
        )
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento='PREDECISION_EJECUTADA',
        titulo='Predecisión formal ejecutada',
        descripcion='Se ejecutó evaluación formal read-only de prestador.',
        estado_resultante=getattr(resultado, 'decision', ''),
        metadata={
            'decision': getattr(resultado, 'decision', ''),
            'eligible': getattr(resultado, 'eligible', None),
            'auditoria_id': auditoria.id,
        },
        usuario=usuario,
        request=request,
    )
    return ResultadoEvaluacionFormalPrestador(
        resultado=resultado,
        auditoria=auditoria,
    )


def _registrar_eventos_datacredito_servicios(solicitud, contexto, *, usuario=None, request=None):
    for servicio, snapshot, reutilizado, consultado in (
        ('decisor', contexto.snapshot_decisor, contexto.reutilizado_decisor, contexto.consultado_decisor),
        ('historial', contexto.snapshot_historial, contexto.reutilizado_historial, contexto.consultado_historial),
    ):
        if reutilizado:
            tipo_evento = 'DATACREDITO_SNAPSHOT_REUTILIZADO'
            titulo = 'Snapshot DataCredito reutilizado'
        elif consultado:
            tipo_evento = (
                'DATACREDITO_CONSULTA_FORZADA'
                if contexto.modo == 'FORZAR_CONSULTA'
                else 'DATACREDITO_CONSULTA_REALIZADA'
            )
            titulo = 'Consulta DataCredito realizada'
        else:
            continue
        registrar_evento_timeline_prestador(
            solicitud=solicitud,
            tipo_evento=tipo_evento,
            titulo=titulo,
            descripcion=f'Servicio {servicio}.',
            metadata={
                'servicio': servicio,
                'snapshot_id': str(snapshot.id) if snapshot else None,
                'reutilizado': bool(reutilizado),
                'consultado_proveedor': bool(consultado),
                'estado_normalizado': snapshot.estado_normalizado if snapshot else None,
                'http_status': snapshot.http_status if snapshot else None,
                'codigo_funcional': snapshot.codigo_funcional if snapshot else None,
            },
            usuario=usuario,
            request=request,
        )

"""Append-only manual evidence; no inferred income or provider calls."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import ContractorApplication, IngresoNetoVerificadoPrestador, TimelinePrestador
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from gestion_creditos.models import calcular_hash_archivo


PERMISO = 'contractors.can_verify_contractor_net_income'
FUENTE = 'MANUAL_VERIFICADA'


def exigir_verificador(actor):
    if (not getattr(actor, 'is_authenticated', False) or not actor.is_staff
            or hasattr(actor, 'perfil_pagador') or not actor.has_perm(PERMISO)):
        raise PermissionDenied('Se requiere staff autorizado de riesgo, sin perfil pagador.')


@transaction.atomic
def registrar_ingreso_neto(*, solicitud, actor, monto=None, fecha_corte=None,
                          vigente_hasta=None, documentos=(), observacion='', invalidar=False):
    exigir_verificador(actor)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado not in {
        solicitud.Estado.BORRADOR, solicitud.Estado.DOCUMENTOS_PENDIENTES,
        solicitud.Estado.DOCUMENTOS_CARGADOS, solicitud.Estado.EVALUACION_PENDIENTE,
        solicitud.Estado.EN_EVALUACION, solicitud.Estado.EVALUACION_COMPLETADA,
        solicitud.Estado.EN_REVISION_MANUAL,
    } or solicitud.aprobaciones_internas.filter(estado='APROBADA_PARA_ORIGINAR').exists():
        raise ValidationError('La solicitud ya paso a una etapa incompatible con cambiar ingresos.')
    anterior = solicitud.ingresos_netos_verificados.order_by('-version').first()
    if invalidar and anterior is None:
        raise ValidationError('No existe un ingreso que invalidar.')
    evidencias = []
    if not invalidar:
        try:
            monto = Decimal(str(monto))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError('Monto neto invalido.') from exc
        if (not monto.is_finite() or not 0 < monto < Decimal('1000000000000')
                or monto != monto.quantize(Decimal('.01'))):
            raise ValidationError('El monto neto debe ser positivo y tener maximo dos decimales.')
        if (not isinstance(fecha_corte, date) or not isinstance(vigente_hasta, date)
                or fecha_corte > timezone.localdate() or vigente_hasta < timezone.localdate()
                or vigente_hasta < fecha_corte):
            raise ValidationError('Corte y vigencia de ingreso invalidos.')
        try:
            ids = {int(getattr(doc, 'pk', doc)) for doc in documentos}
        except (ValueError, TypeError) as exc:
            raise ValidationError('Referencias documentales invalidas.') from exc
        referencias = list(solicitud.documentos.filter(pk__in=ids).order_by('pk'))
        if not referencias or len(referencias) != len(ids):
            raise ValidationError('Selecciona evidencias documentales de esta solicitud.')
        for documento in referencias:
            try:
                with documento.archivo.open('rb'):
                    digest = calcular_hash_archivo(documento.archivo)
            except (OSError, ValueError) as exc:
                raise ValidationError('No se pudo verificar una evidencia documental.') from exc
            evidencias.append(dict(documento_id=documento.pk, tipo=documento.tipo_documento, sha256=digest))
    else:
        monto = fecha_corte = vigente_hasta = None
    valores = dict(accion='INVALIDADO' if invalidar else 'VERIFICADO', fuente=FUENTE,
                   monto=monto, fecha_corte=fecha_corte, vigente_hasta=vigente_hasta,
                   evidencias=evidencias, observacion=str(observacion).strip())
    if anterior and all(getattr(anterior, k) == v for k, v in valores.items()):
        return anterior
    registro = IngresoNetoVerificadoPrestador(
        solicitud=solicitud, version=(anterior.version + 1 if anterior else 1),
        verificado_por=actor, **valores,
    )
    registro.full_clean()
    registro._registro_por_servicio = True
    registro.save()
    # Historical audits are immutable. A changed input version fences in-flight work
    # and makes approval reject stale results using its existing version_datos check.
    if solicitud.estado in {solicitud.Estado.EN_EVALUACION, solicitud.Estado.EVALUACION_COMPLETADA}:
        solicitud.estado = solicitud.Estado.EN_REVISION_MANUAL
        solicitud.save(update_fields=['estado', 'updated_at'])
    registrar_evento_timeline_prestador(
        solicitud=solicitud, tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
        titulo='Verificacion interna de ingresos actualizada',
        metadata={'campos': ['ingreso_neto_verificado'], 'actor_id': actor.pk}, usuario=actor,
    )
    return registro


def snapshot_ingreso_neto(solicitud, *, corte=None):
    registro = solicitud.ingresos_netos_verificados.order_by('-version').first()
    if registro is None:
        return None
    corte = corte or timezone.localdate()
    return dict(registro_id=registro.pk, version=registro.version, fuente=registro.fuente,
                accion=registro.accion, monto=str(registro.monto) if registro.monto is not None else None,
                fecha_corte=registro.fecha_corte.isoformat() if registro.fecha_corte else None,
                vigente_hasta=registro.vigente_hasta.isoformat() if registro.vigente_hasta else None,
                vigente=(registro.accion == 'VERIFICADO' and registro.fecha_corte <= corte <= registro.vigente_hasta
                         and _evidencias_vigentes(registro, solicitud)))


def _evidencias_vigentes(registro, solicitud):
    documentos = solicitud.documentos.in_bulk([e['documento_id'] for e in registro.evidencias])
    for evidencia in registro.evidencias:
        documento = documentos.get(evidencia['documento_id'])
        try:
            if documento is None:
                return False
            with documento.archivo.open('rb'):
                if calcular_hash_archivo(documento.archivo) != evidencia['sha256']:
                    return False
        except (OSError, ValueError):
            return False
    return bool(registro.evidencias)


def obtener_ingreso_neto_vigente(solicitud, *, corte=None):
    from contractors.services.politica_financiera_prestador import IngresoNetoValido
    corte = corte or timezone.localdate()
    datos = snapshot_ingreso_neto(solicitud, corte=corte)
    if not datos or not datos['vigente']:
        return None
    registro = IngresoNetoVerificadoPrestador.objects.get(pk=datos['registro_id'])
    return IngresoNetoValido(registro.monto, FUENTE, registro.fecha_corte,
                            registro.vigente_hasta, registro.version, solicitud.pk, registro.pk)


def ingreso_para_oferta_vigente(ingreso, *, corte, solicitud_id):
    if ingreso.solicitud_id != solicitud_id or ingreso.registro_id is None:
        return False
    solicitud = ContractorApplication.objects.filter(pk=solicitud_id).first()
    if solicitud is None:
        return False
    return obtener_ingreso_neto_vigente(solicitud, corte=corte) == ingreso

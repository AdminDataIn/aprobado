"""One income resolver for score and preliminary offer. No writes or provider calls."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.utils import timezone

from contractors.models import ContractorApplication
from contractors.politica_ingresos import validar_politica_ingreso
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
from contractors.services.politica_financiera_prestador import IngresoNetoValido


def _positivo(valor):
    if isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor))
        return numero if numero.is_finite() and Decimal('.01') <= numero < Decimal('1000000000000') else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fuente_midecisor(solicitud, decisor):
    from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
    from contractors.services.datacredito_evaluacion import evidencia_uso_snapshot
    from integrations.models import ConsultaDatacreditoSnapshot

    if (getattr(decisor, 'estado', None) != 'EXITOSO'
            or getattr(decisor, 'servicio', None) != 'decisor'
            or not getattr(decisor, 'snapshot_id', None)):
        return None
    try:
        snap = ConsultaDatacreditoSnapshot.objects.filter(pk=decisor.snapshot_id, servicio='decisor', estado='EXITOSO').first()
    except (ValidationError, ValueError):
        return None
    auth = obtener_autorizacion_datacredito_vigente(solicitud)
    if not evidencia_uso_snapshot(snap, solicitud=solicitud, autorizacion=auth):
        return None
    datos = snap.resultado_normalizado or {}
    if datos.get('ingreso_estimado_modulo_valido') is not True:
        return None
    monto = _positivo(datos.get('ingreso_estimado_midecisor'))
    return (monto, snap) if monto is not None else None


def resolver_ingreso_evaluable(solicitud, politica, *, decisor=None, corte=None):
    from contractors.services.evaluacion_versionado import construir_version_datos, _hash_contrato_guardado
    from contractors.services.horizonte_simulacion import horizonte_para_solicitud, validar_plazo_contractual
    from contractors.services.validacion_contractual import validar_contrato_prestador

    corte = corte or timezone.localdate()
    documental = obtener_ingreso_neto_vigente(solicitud, corte=corte)
    if documental is not None:
        return documental
    from contractors.services.politica_ingreso_publicacion import ingreso_automatico_aplica
    if not ingreso_automatico_aplica(solicitud, politica):
        return None
    reglas = validar_politica_ingreso(politica.politica_ingreso_evaluable)
    contrato = validar_contrato_prestador(solicitud, fecha_corte=corte)
    mensual = _positivo(solicitud.valor_mensual_contractual)
    digest = _hash_contrato_guardado(solicitud)
    analisis = solicitud.metadata_analisis_contractual or {}
    if (not contrato.capacidad_automatica or contrato.requiere_revision_manual
            or solicitud.estado_analisis_contractual != 'COMPLETADO'
            or analisis.get('bloqueos')
            or not mensual or not solicitud.fecha_inicio_contrato or not solicitud.fecha_fin_contrato
            or not solicitud.fecha_inicio_contrato <= corte <= solicitud.fecha_fin_contrato
            or not digest or digest != analisis.get('archivo_hash_sha256')):
        return None
    horizonte = horizonte_para_solicitud(solicitud, politica.configuracion_financiera, corte=corte)
    if horizonte is None or not horizonte.disponible:
        return None
    try:
        validar_plazo_contractual(solicitud, politica.configuracion_financiera, solicitud.plazo_meses, corte=corte)
    except ValidationError:
        return None
    contexto, _ = construir_version_datos(solicitud)
    evidencia = {
        'tipo': 'ESTIMADO_NO_VERIFICADO', 'reglas': reglas,
        'version_politica': politica.version_politica, 'contexto': contexto,
        'contrato_sha256': digest, 'honorarios_mensuales': format(mensual, 'f'),
        'requiere_validacion_documental': True,
        'vigente_hasta': solicitud.fecha_fin_contrato.isoformat(),
    }
    fuente = _fuente_midecisor(solicitud, decisor)
    if fuente and reglas['semantica_midecisor'] == 'NETO_MENSUAL_ESTIMADO':
        estimado, snap = fuente
        monto = min(estimado, mensual)
        evidencia.update(snapshot_id=str(snap.pk), ingreso_proveedor=format(estimado, 'f'),
                         discrepancia=estimado != mensual, regla='MENOR_PROVEEDOR_CONTRATO')
        hasta = min(solicitud.fecha_fin_contrato, timezone.localtime(snap.vigente_hasta).date())
        nombre = 'MIDECISOR_ESTIMADO'
    else:
        descuento = Decimal(reglas['descuento_contractual_porcentaje'])
        monto = mensual * (1 - descuento / 100)
        hasta = solicitud.fecha_fin_contrato
        nombre = 'CONTRATO_ESTIMADO'
        evidencia.update(regla='HONORARIOS_MENOS_DESCUENTO',
                         descuento_porcentaje=format(descuento, 'f'),
                         midecisor_utilizable=False,
                         semantica_midecisor=reglas['semantica_midecisor'])
    monto = monto.quantize(Decimal('.01'), rounding=ROUND_DOWN)
    if monto <= 0:
        return None
    return IngresoNetoValido(monto, nombre,
                            corte, hasta, solicitud_id=solicitud.pk, evidencia=evidencia)


def ingreso_evaluable_vigente(ingreso, *, solicitud_id, politica, corte):
    from contractors.datacredito.dto import ResultadoConsultaDatacreditoPrestador
    if ingreso.solicitud_id != solicitud_id or ingreso.fecha_corte != corte:
        return False
    solicitud = ContractorApplication.objects.filter(pk=solicitud_id).first()
    if solicitud is None:
        return False
    sid = ingreso.evidencia.get('snapshot_id')
    decisor = ResultadoConsultaDatacreditoPrestador(estado='EXITOSO', servicio='decisor', snapshot_id=sid) if sid else None
    return resolver_ingreso_evaluable(solicitud, politica, decisor=decisor, corte=corte) == ingreso


def exigir_ingreso_documental_para_originacion(solicitud):
    if obtener_ingreso_neto_vigente(solicitud) is None:
        raise ValidationError('La oferta preliminar requiere ingreso documental validado para aprobacion y originacion.',
                              code='ingreso_documental_requerido')

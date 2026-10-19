"""Explicit publication/adoption; existing policies and evaluations are never rewritten."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import (
    BandaScorePrestador, CambioPoliticaScorePrestadorAudit, ConfiguracionScorePrestador,
    ContractorApplication, TimelinePrestador, ConfiguracionSimuladorPrestador,
)
from contractors.politica_ingresos import politica_ingreso_default, validar_politica_ingreso
from contractors.score.politica import PoliticaScoreNoDisponible, obtener_politica_score_activa, validar_politica_score_completa
from contractors.services.politica_score import _exigir_actor_autorizado, construir_snapshot_politica_score


VERSION_INGRESO = 'prestadores-score-prod-v2-ingreso'
EVENTO_ADOPCION = TimelinePrestador.TipoEvento.DATOS_MODIFICADOS


def _publicada(politica):
    return CambioPoliticaScorePrestadorAudit.objects.filter(
        politica_nueva=politica, snapshot_nuevo__ingreso_publicado=True,
    ).exists()


def ingreso_automatico_aplica(solicitud, politica):
    return bool(politica and politica.politica_ingreso_evaluable
                and politica.fecha_vigencia_desde <= timezone.localdate()
                and (not politica.fecha_vigencia_hasta or politica.fecha_vigencia_hasta >= timezone.localdate())
                and solicitud.version_politica_simulacion == politica.version_politica
                and _publicada(politica)
                and TimelinePrestador.objects.filter(
                    solicitud=solicitud, tipo_evento=EVENTO_ADOPCION,
                    metadata__tipo='ADOPCION_INGRESO',
                    metadata__version_politica=politica.version_politica,
                ).exists())


def politica_habilitada_para_solicitud(solicitud, politica):
    return ingreso_automatico_aplica(solicitud, politica) if politica.politica_ingreso_evaluable else politica.activa


def obtener_politica_para_solicitud(solicitud):
    asociada = ConfiguracionScorePrestador.objects.filter(
        version_politica=solicitud.version_politica_simulacion,
    ).first()
    if asociada and asociada.politica_ingreso_evaluable:
        hoy = timezone.localdate()
        if (not ingreso_automatico_aplica(solicitud, asociada)
                or asociada.fecha_vigencia_desde > hoy
                or (asociada.fecha_vigencia_hasta and asociada.fecha_vigencia_hasta < hoy)):
            raise PoliticaScoreNoDisponible('La politica de ingreso no esta publicada, adoptada o vigente.')
        validar_politica_score_completa(asociada)
        return asociada
    return obtener_politica_score_activa()


def _campos_clon(modelo, excluir):
    return [f.attname for f in modelo._meta.concrete_fields if f.name not in excluir]


@transaction.atomic
def publicar_politica_ingreso(*, origen_id, actor, motivo, vigente_desde,
                             descuento='25.00', version_destino=VERSION_INGRESO):
    _exigir_actor_autorizado(actor)
    from contractors.politica_decision import VERSION_POLITICA, definicion_decision, huella_reglas
    if version_destino not in {VERSION_INGRESO, VERSION_POLITICA}:
        raise ValidationError('La version de destino no esta definida en codigo.')
    decision = definicion_decision() if version_destino == VERSION_POLITICA else None
    if not isinstance(vigente_desde, date) or not str(motivo or '').strip():
        raise ValidationError('Publicacion requiere vigencia explicita y motivo.')
    politicas = list(ConfiguracionScorePrestador.objects.select_for_update().order_by('pk'))
    origen = next((p for p in politicas if p.pk == origen_id), None)
    if not origen or not origen.activa or origen.politica_ingreso_evaluable:
        raise ValidationError('El origen debe ser la politica legacy activa.')
    if (origen.fecha_vigencia_desde > timezone.localdate()
            or (origen.fecha_vigencia_hasta and origen.fecha_vigencia_hasta < timezone.localdate())):
        raise ValidationError('La politica de origen no esta vigente.')
    if not origen.configuracion_financiera_id:
        raise ValidationError('La politica de origen requiere configuracion financiera.')
    # Same policy -> financial lock order as the existing activation service.
    origen.configuracion_financiera = ConfiguracionSimuladorPrestador.objects.select_for_update().get(
        pk=origen.configuracion_financiera_id,
    )
    list(origen.bandas.select_for_update().order_by('pk'))
    validar_politica_score_completa(origen)
    from contractors.services.politica_financiera_prestador import validar_configuracion_prod
    validar_configuracion_prod(origen.configuracion_financiera)
    reglas = validar_politica_ingreso({**politica_ingreso_default(),
                                      'descuento_contractual_porcentaje': descuento})
    campos = _campos_clon(ConfiguracionScorePrestador, {'id', 'created_at', 'updated_at'})
    valores = {k: deepcopy(getattr(origen, k)) for k in campos}
    valores.update(version=version_destino, version_politica=version_destino,
                   nombre=('Prestadores decision V3' if decision else 'Prestadores ingreso evaluable v2'), activa=False,
                   fecha_vigencia_desde=vigente_desde, politica_ingreso_evaluable=reglas)
    # A fresh statement sees a concurrent publisher that committed while we waited.
    nueva = ConfiguracionScorePrestador.objects.filter(version=version_destino).first()
    if ConfiguracionScorePrestador.objects.filter(version_politica=version_destino).exclude(version=version_destino).exists():
        raise ValidationError('La identidad de politica ya pertenece a otra version.')
    if nueva:
        if any(getattr(nueva, k) != v for k, v in valores.items()):
            raise ValidationError('La version publicada difiere; no se sobrescribe.')
    else:
        nueva = ConfiguracionScorePrestador(**valores)
        nueva.full_clean()
        nueva.save()
        for banda in origen.bandas.order_by('pk'):
            datos = {k: deepcopy(getattr(banda, k)) for k in _campos_clon(
                BandaScorePrestador, {'id', 'configuracion'},
            )}
            BandaScorePrestador.objects.create(configuracion=nueva, **datos)
    esperadas = list(origen.bandas.order_by('orden').values(
        'nombre', 'score_min', 'score_max', 'monto_maximo', 'plazo_maximo', 'resultado', 'orden'))
    actuales = list(nueva.bandas.order_by('orden').values(*esperadas[0].keys()))
    if actuales != esperadas:
        raise ValidationError('Las bandas publicadas no corresponden al origen.')
    validar_politica_score_completa(nueva)
    snapshot = {'ingreso_publicado': True, 'politica': construir_snapshot_politica_score(nueva)}
    if decision:
        snapshot.update(decision_sha256=huella_reglas(decision), reglas_decision=decision)
        previa = CambioPoliticaScorePrestadorAudit.objects.filter(politica_nueva=nueva).first()
        if previa and previa.snapshot_nuevo.get('decision_sha256') != huella_reglas(decision):
            raise ValidationError('La normativa publicada cambio; requiere una nueva version.')
    clave = sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    CambioPoliticaScorePrestadorAudit.objects.get_or_create(
        clave_idempotencia=clave, defaults=dict(
            politica_anterior=origen, politica_nueva=nueva, actor=actor, motivo=motivo.strip(),
            accion=CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO,
            snapshot_anterior=construir_snapshot_politica_score(origen), snapshot_nuevo=snapshot,
        ),
    )
    return nueva


@transaction.atomic
def adoptar_politica_ingreso(*, solicitud_id, politica_id, actor, motivo):
    _exigir_actor_autorizado(actor)
    if not actor.has_perm('contractors.can_evaluate_contractor_application'):
        raise PermissionDenied('Se requiere permiso de evaluacion.')
    if not str(motivo or '').strip():
        raise ValidationError('La adopcion requiere motivo.')
    politica = ConfiguracionScorePrestador.objects.select_for_update().get(pk=politica_id)
    solicitud = ContractorApplication.objects.select_for_update().get(pk=solicitud_id)
    if ingreso_automatico_aplica(solicitud, politica):
        return solicitud
    if (not politica.politica_ingreso_evaluable or not _publicada(politica)
            or politica.fecha_vigencia_desde > timezone.localdate()
            or (politica.fecha_vigencia_hasta and politica.fecha_vigencia_hasta < timezone.localdate())):
        raise ValidationError('Politica no publicada o fuera de vigencia.')
    if (solicitud.estado != solicitud.Estado.EVALUACION_PENDIENTE
            or solicitud.auditorias_predecision.exists() or solicitud.evaluacion_jobs.exists()
            or solicitud.aprobaciones_internas.exists() or solicitud.aprobaciones_pagador.exists()):
        raise ValidationError('La solicitud tiene evaluaciones/jobs/gates o estado incompatible.')
    from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
    from contractors.services.datacredito_evaluacion import construir_fingerprint_datacredito, evidencia_uso_snapshot
    from contractors.services.solicitud import solicitud_tiene_documentos_obligatorios
    from contractors.services.horizonte_simulacion import validar_plazo_contractual
    from contractors.score.motor import detectar_incompatibilidades_simulador
    from integrations.models import ConsultaDatacreditoSnapshot
    from integrations.datacredito.settings import obtener_configuracion_datacredito
    auth = obtener_autorizacion_datacredito_vigente(solicitud)
    if (not auth or auth.usuario_id != solicitud.usuario_id or not solicitud.simulada_en
            or not solicitud_tiene_documentos_obligatorios(solicitud)
            or solicitud.estado_analisis_contractual != 'COMPLETADO'):
        raise ValidationError('Falta consentimiento, contrato, documentos o simulacion.')
    previos = ConsultaDatacreditoSnapshot.objects.filter(fingerprint__in=[
        construir_fingerprint_datacredito(solicitud=solicitud, servicio=s, autorizacion=auth,
                                         configuracion=obtener_configuracion_datacredito())
        for s in ('historial', 'decisor')
    ])
    if any(s.estado == 'EN_PROCESO' or not evidencia_uso_snapshot(s, solicitud=solicitud, autorizacion=auth)
           for s in previos):
        raise ValidationError('Existen consultas o reservas incompatibles; requiere revision independiente.')
    solicitud.version_politica_simulacion = politica.version_politica
    if detectar_incompatibilidades_simulador(solicitud, politica):
        raise ValidationError('La simulacion no corresponde a la configuracion publicada.')
    validar_plazo_contractual(solicitud, politica.configuracion_financiera, solicitud.plazo_meses)
    solicitud.save(update_fields=['version_politica_simulacion'])
    TimelinePrestador.objects.create(
        solicitud=solicitud, tipo_evento=EVENTO_ADOPCION, titulo='Adopcion explicita de politica de ingreso',
        descripcion=motivo.strip(), visible_cliente=False, creado_por=actor,
        metadata={'tipo': 'ADOPCION_INGRESO', 'version_politica': politica.version_politica, 'actor_id': actor.pk},
    )
    return solicitud

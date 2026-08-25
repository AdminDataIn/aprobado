from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from contractors.models import (
    CambioPoliticaScorePrestadorAudit,
    ConfiguracionScorePrestador,
    ConfiguracionSimuladorPrestador,
)
from contractors.score.politica import validar_politica_score_completa


PERMISO_ACTIVAR_POLITICA = 'contractors.can_activate_contractor_score_policy'
VERSION_SCORE_DEMO_V2 = 'prestadores-score-demo-v2'


@dataclass(frozen=True)
class ResultadoActivacionPoliticaScorePrestador:
    politica_anterior: ConfiguracionScorePrestador | None
    politica_nueva: ConfiguracionScorePrestador
    auditoria: CambioPoliticaScorePrestadorAudit
    cambio_realizado: bool


def construir_snapshot_politica_score(politica):
    if politica is None:
        return {}
    financiera = politica.configuracion_financiera
    return {
        'id': politica.pk,
        'version': politica.version,
        'version_score': politica.version_score,
        'version_politica': politica.version_politica,
        'activa': politica.activa,
        'vigencia': {
            'desde': _serializar(politica.fecha_vigencia_desde),
            'hasta': _serializar(politica.fecha_vigencia_hasta),
        },
        'pesos': {
            'datacredito': _serializar(politica.peso_datacredito),
            'midecisor': _serializar(politica.peso_midecisor),
            'hdcplus': _serializar(politica.peso_hdcplus),
            'capacidad': _serializar(politica.peso_capacidad),
            'comportamiento': _serializar(politica.peso_comportamiento),
            'riesgo': _serializar(politica.peso_riesgo),
            'referencias': _serializar(politica.peso_referencias),
        },
        'fuentes_requeridas': {
            'midecisor': politica.requiere_midecisor,
            'hdcplus': politica.requiere_hdcplus,
            'permite_sin_midecisor': politica.permite_evaluar_sin_midecisor,
            'permite_sin_hdcplus': politica.permite_evaluar_sin_hdc,
        },
        'configuracion_financiera': {
            'id': financiera.pk if financiera else None,
            'version': financiera.version if financiera else None,
            'activa': financiera.activo if financiera else False,
            'tasa_mensual': _serializar(financiera.tasa_mensual if financiera else None),
            'monto_maximo': _serializar(financiera.monto_maximo if financiera else None),
            'plazo_maximo_meses': (
                financiera.plazo_maximo_meses if financiera else None
            ),
        },
        'bandas': [
            {
                'nombre': banda.nombre,
                'score_min': banda.score_min,
                'score_max': banda.score_max,
                'monto_maximo': _serializar(banda.monto_maximo),
                'plazo_maximo': banda.plazo_maximo,
                'resultado': banda.resultado,
                'orden': banda.orden,
            }
            for banda in politica.bandas.order_by('score_min', 'id')
        ],
    }


def activar_politica_score_prestador(*, politica_id, actor, motivo):
    _exigir_actor_autorizado(actor)
    motivo_limpio = (motivo or '').strip()
    if not motivo_limpio:
        raise ValidationError({'motivo': 'Debes registrar el motivo de la activacion.'})

    try:
        with transaction.atomic():
            return _activar_bajo_bloqueo(
                politica_id=politica_id,
                actor=actor,
                motivo=motivo_limpio,
            )
    except IntegrityError as exc:
        raise ValidationError(
            'La politica no pudo activarse por una operacion concurrente. '
            'Verifica la politica activa e intenta nuevamente.'
        ) from exc


def _activar_bajo_bloqueo(*, politica_id, actor, motivo):
    politicas = list(
        ConfiguracionScorePrestador.objects.select_for_update()
        .order_by('pk')
    )
    objetivo = next((item for item in politicas if item.pk == politica_id), None)
    if objetivo is None:
        raise ValidationError({'politica_id': 'La politica seleccionada no existe.'})

    activas = [item for item in politicas if item.activa]
    if len(activas) > 1:
        raise ValidationError('Existe mas de una politica activa; no se realizo ningun cambio.')
    anterior = activas[0] if activas else None

    if objetivo.configuracion_financiera_id:
        objetivo.configuracion_financiera = (
            ConfiguracionSimuladorPrestador.objects.select_for_update().get(
                pk=objetivo.configuracion_financiera_id,
            )
        )

    _validar_politica_para_activacion(objetivo)

    if objetivo.activa:
        auditoria = _registrar_auditoria(
            anterior=objetivo,
            nueva=objetivo,
            actor=actor,
            motivo=motivo,
            accion=CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO,
            snapshot_anterior=construir_snapshot_politica_score(objetivo),
            snapshot_nuevo=construir_snapshot_politica_score(objetivo),
        )
        return ResultadoActivacionPoliticaScorePrestador(
            politica_anterior=objetivo,
            politica_nueva=objetivo,
            auditoria=auditoria,
            cambio_realizado=False,
        )

    snapshot_anterior = construir_snapshot_politica_score(anterior)
    accion = _accion_para_activacion(objetivo)

    if anterior is not None:
        anterior.activa = False
        anterior.full_clean()
        anterior.save(update_fields=['activa', 'updated_at'])

    objetivo.activa = True
    objetivo.full_clean()
    objetivo.save(update_fields=['activa', 'updated_at'])
    snapshot_nuevo = construir_snapshot_politica_score(objetivo)

    auditoria = _registrar_auditoria(
        anterior=anterior,
        nueva=objetivo,
        actor=actor,
        motivo=motivo,
        accion=accion,
        snapshot_anterior=snapshot_anterior,
        snapshot_nuevo=snapshot_nuevo,
    )
    return ResultadoActivacionPoliticaScorePrestador(
        politica_anterior=anterior,
        politica_nueva=objetivo,
        auditoria=auditoria,
        cambio_realizado=True,
    )


def _validar_politica_para_activacion(politica):
    hoy = timezone.localdate()
    if politica.fecha_vigencia_desde > hoy:
        raise ValidationError('La politica aun no inicia su vigencia.')
    if politica.fecha_vigencia_hasta and politica.fecha_vigencia_hasta < hoy:
        raise ValidationError('La politica seleccionada esta expirada.')
    if not politica.configuracion_financiera_id:
        raise ValidationError('La politica no tiene configuracion financiera vinculada.')

    financiera = politica.configuracion_financiera
    financiera.full_clean()
    if not financiera.activo or not financiera.version:
        raise ValidationError('La configuracion financiera debe estar activa y versionada.')

    estado_original = politica.activa
    politica.activa = True
    try:
        validar_politica_score_completa(
            politica,
            validar_restricciones_bd=False,
        )
        if politica.version == VERSION_SCORE_DEMO_V2:
            _validar_parametrizacion_demo_v2(politica)
    finally:
        politica.activa = estado_original


def _validar_parametrizacion_demo_v2(politica):
    financiera = politica.configuracion_financiera
    esperados = {
        'peso_datacredito': Decimal('0.00000'),
        'peso_midecisor': Decimal('0.45000'),
        'peso_hdcplus': Decimal('0.00000'),
        'peso_capacidad': Decimal('0.30000'),
        'peso_comportamiento': Decimal('0.08000'),
        'peso_riesgo': Decimal('0.12000'),
        'peso_referencias': Decimal('0.05000'),
        'monto_maximo_politica': Decimal('10000000.00'),
        'plazo_maximo_politica': 8,
        'tasa_mensual_referencia': Decimal('2.2000'),
        'requiere_midecisor': True,
        'requiere_hdcplus': True,
        'permite_evaluar_sin_hdc': False,
    }
    diferencias = [
        campo for campo, esperado in esperados.items()
        if getattr(politica, campo) != esperado
    ]
    if financiera.monto_maximo != Decimal('10000000.00'):
        diferencias.append('configuracion_financiera.monto_maximo')
    if financiera.plazo_maximo_meses != 8:
        diferencias.append('configuracion_financiera.plazo_maximo_meses')
    if financiera.tasa_mensual != Decimal('2.2000'):
        diferencias.append('configuracion_financiera.tasa_mensual')
    if diferencias:
        raise ValidationError(
            'La politica DEMO v2 no coincide con la parametrizacion aprobada: '
            + ', '.join(diferencias)
            + '.'
        )


def _accion_para_activacion(objetivo):
    usada_antes = CambioPoliticaScorePrestadorAudit.objects.filter(
        Q(politica_anterior=objetivo) | Q(politica_nueva=objetivo),
    ).exists()
    if usada_antes:
        return CambioPoliticaScorePrestadorAudit.Accion.REACTIVACION
    return CambioPoliticaScorePrestadorAudit.Accion.ACTIVACION


def _registrar_auditoria(
    *,
    anterior,
    nueva,
    actor,
    motivo,
    accion,
    snapshot_anterior,
    snapshot_nuevo,
):
    clave = _construir_clave_idempotencia(
        anterior=anterior,
        nueva=nueva,
        actor=actor,
        motivo=motivo,
        accion=accion,
    )
    auditoria, _ = CambioPoliticaScorePrestadorAudit.objects.get_or_create(
        clave_idempotencia=clave,
        defaults={
            'politica_anterior': anterior,
            'politica_nueva': nueva,
            'actor': actor,
            'motivo': motivo,
            'accion': accion,
            'snapshot_anterior': snapshot_anterior,
            'snapshot_nuevo': snapshot_nuevo,
        },
    )
    return auditoria


def _construir_clave_idempotencia(*, anterior, nueva, actor, motivo, accion):
    if accion == CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO:
        estado = _serializar(nueva.updated_at)
    else:
        estado = '|'.join((
            _serializar(anterior.updated_at if anterior else None) or '',
            _serializar(nueva.updated_at) or '',
        ))
    contenido = json.dumps(
        {
            'accion': accion,
            'anterior_id': anterior.pk if anterior else None,
            'nueva_id': nueva.pk,
            'actor_id': actor.pk,
            'motivo': motivo,
            'estado': estado,
        },
        sort_keys=True,
        separators=(',', ':'),
    )
    return sha256(contenido.encode('utf-8')).hexdigest()


def _exigir_actor_autorizado(actor):
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied('Se requiere un actor autenticado.')
    if not getattr(actor, 'is_active', False):
        raise PermissionDenied('El actor administrativo esta inactivo.')
    if not getattr(actor, 'is_staff', False):
        raise PermissionDenied('Se requiere un actor staff para activar politicas.')
    if hasattr(actor, 'perfil_pagador'):
        raise PermissionDenied('Un perfil pagador no puede activar politicas de score.')
    if not actor.has_perm(PERMISO_ACTIVAR_POLITICA):
        raise PermissionDenied('No tienes permiso para activar politicas de score.')


def _serializar(valor):
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return str(valor)
    if hasattr(valor, 'isoformat'):
        return valor.isoformat()
    return str(valor)

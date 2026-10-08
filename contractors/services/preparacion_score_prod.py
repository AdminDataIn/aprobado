"""Prepared definition only; no provider calls, activation, or financial effects."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from contractors.models import (
    BandaScorePrestador, CambioPoliticaScorePrestadorAudit,
    ConfiguracionScorePrestador, ConfiguracionSimuladorPrestador,
)
from contractors.score.politica import validar_politica_score_completa
from contractors.services.politica_financiera_prestador import validar_configuracion_prod
from contractors.services.politica_score import PERMISO_ACTIVAR_POLITICA


ARCHIVO_DEFINICION = Path(__file__).resolve().parents[2] / 'docs' / 'parametros_score_prestadores_prod_pendientes.json'


def definicion_score_prod():
    return json.loads(ARCHIVO_DEFINICION.read_text(encoding='utf-8'))


def extraer_parametros_score_prod(documento, *, definicion=None):
    """Accept the versioned envelope or legacy flat inputs, never CLI-defined bands."""
    definicion = definicion if definicion is not None else definicion_score_prod()
    if not isinstance(documento, dict):
        raise ValidationError('La definicion debe ser un objeto JSON.')
    if not set(documento).intersection(definicion):
        return dict(documento)
    if set(documento) != set(definicion):
        raise ValidationError('El documento oficial requiere su estructura completa, sin campos adicionales.')
    for campo in set(definicion) - {'parametros_aprobados', 'parametros_pendientes_aprobacion'}:
        if (type(documento[campo]) is not type(definicion[campo])
                or documento[campo] != definicion[campo]):
            raise ValidationError('Metadata inconsistente: ' + campo + '.')
    aprobados = documento['parametros_aprobados']
    pendientes = documento['parametros_pendientes_aprobacion']
    if (not isinstance(aprobados, dict) or not isinstance(pendientes, dict)
            or set(aprobados) != set(definicion['parametros_aprobados'])
            or set(pendientes) != set(definicion['parametros_pendientes_aprobacion'])):
        raise ValidationError('Los bloques de parametros no corresponden a la definicion versionada.')
    if documento['version'] != aprobados['version_politica']:
        raise ValidationError('La version del documento no coincide con version_politica.')
    return {**aprobados, **pendientes}


def preparar_politica_score_prod(*, parametros=None, persistir=False, actor=None, motivo=''):
    definicion = definicion_score_prod()
    recibidos = extraer_parametros_score_prod({} if parametros is None else parametros, definicion=definicion)
    aprobados = definicion['parametros_aprobados']
    requeridos = set(aprobados) | set(definicion['parametros_pendientes_aprobacion'])
    if set(recibidos) - requeridos:
        raise ValidationError('Parametros no permitidos en la definicion preparada.')
    for campo, aprobado in aprobados.items():
        if campo not in recibidos:
            continue
        if campo == 'fuente_ingreso_neto_valido_para_riesgo':
            coincide = recibidos[campo] == aprobado
        else:
            field = ConfiguracionScorePrestador._meta.get_field(campo)
            coincide = field.to_python(recibidos[campo]) == field.to_python(aprobado)
        if not coincide:
            raise ValidationError('El parametro ' + campo + ' difiere de la politica ratificada.')
    # Canonical approved values keep the audit key stable across equivalent JSON types.
    parametros = {**aprobados, **{k: v for k, v in recibidos.items() if k not in aprobados}}
    faltantes = sorted(campo for campo in requeridos if parametros.get(campo) in (None, ''))
    if faltantes:
        if persistir:
            raise ValidationError('Politica incompleta; faltan: ' + ', '.join(faltantes))
        return {**definicion, 'parametros': parametros, 'pendientes': faltantes, 'persistida': False}
    if parametros['version_politica'] != definicion['version']:
        raise ValidationError('La version de politica debe corresponder a la definicion PROD.')
    if not str(parametros['fuente_ingreso_neto_valido_para_riesgo']).strip():
        raise ValidationError('La fuente neta debe definirse explicitamente.')
    if persistir and (
        persistir is not True
        or not getattr(actor, 'is_authenticated', False)
        or not getattr(actor, 'is_active', False) or not getattr(actor, 'is_staff', False)
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm(PERMISO_ACTIVAR_POLITICA)
        or not isinstance(motivo, str) or not motivo.strip()
    ):
        raise PermissionDenied('La preparacion exige staff autorizado y motivo.')
    parametros['fecha_vigencia_desde'] = ConfiguracionScorePrestador._meta.get_field(
        'fecha_vigencia_desde',
    ).to_python(parametros['fecha_vigencia_desde']).isoformat()
    with transaction.atomic():
        financieras = ConfiguracionSimuladorPrestador.objects.all()
        if persistir:
            # Match activation's policy -> financial lock order when the policy exists.
            ConfiguracionScorePrestador.objects.select_for_update().filter(
                version=definicion['version'],
            ).first()
            # This existing row also serializes concurrent first preparations.
            financieras = financieras.select_for_update()
        financiera = financieras.get(
            version=definicion['configuracion_financiera_version'],
        )
        validar_configuracion_prod(financiera)
        valores = {
            key: ConfiguracionScorePrestador._meta.get_field(key).to_python(value)
            for key, value in parametros.items()
            if key != 'fuente_ingreso_neto_valido_para_riesgo'
        }
        valores.update(
            nombre='Score Prestadores PROD v1 (preparado, inactivo)',
            version=definicion['version'], activa=False, configuracion_financiera=financiera,
            monto_maximo_politica=financiera.monto_maximo,
            plazo_maximo_politica=financiera.plazo_maximo_meses,
            tasa_mensual_referencia=financiera.tasa_mensual,
        )
        politica = ConfiguracionScorePrestador(**valores)
        politica.full_clean(validate_unique=False)
        cursor = 0
        bandas = []
        for datos in sorted(definicion['bandas_aprobadas'], key=lambda banda: banda['score_min']):
            banda = BandaScorePrestador(**datos, resultado=(
                BandaScorePrestador.Resultado.REQUIERE_REVISION_MANUAL
                if datos['nombre'] == 'REVISION' else BandaScorePrestador.Resultado.PREAPROBADO_READ_ONLY
            ))
            banda.clean_fields(exclude=['configuracion'])
            if (banda.score_min != cursor or banda.score_max < banda.score_min
                    or banda.monto_maximo > politica.monto_maximo_politica
                    or banda.plazo_maximo > politica.plazo_maximo_politica):
                raise ValidationError('Bandas incompletas o inconsistentes con la politica financiera.')
            cursor = banda.score_max + 1
            bandas.append(banda)
        if cursor != 1001 or {b.nombre for b in bandas} != set(BandaScorePrestador.Nombre.values):
            raise ValidationError('Las bandas deben cubrir 0 a 1000 sin vacios.')
        if not persistir:
            return {**definicion, 'parametros': parametros, 'pendientes': [], 'persistida': False,
                    'configuracion_financiera_id': financiera.pk,
                    'configuracion_financiera_activa': financiera.activo,
                    'peso_total': str(sum((Decimal(str(parametros[k])) for k in aprobados
                                          if k.startswith('peso_')), Decimal('0')))}
        existente = ConfiguracionScorePrestador.objects.filter(version=politica.version).first()
        if existente:
            campos = [f.attname for f in politica._meta.concrete_fields
                      if f.name not in {'id', 'created_at', 'updated_at'}]
            if any(getattr(existente, campo) != getattr(politica, campo) for campo in campos):
                raise ValidationError('La version existente no coincide; no se reinterpreta ni activa.')
            politica = existente
            if set(politica.bandas.values_list('nombre', flat=True)) != {b.nombre for b in bandas}:
                raise ValidationError('Las bandas existentes estan incompletas; no se reparan implicitamente.')
        else:
            politica.save()
        for datos in definicion['bandas_aprobadas']:
            valores_banda = {**datos, 'resultado': (
                BandaScorePrestador.Resultado.REQUIERE_REVISION_MANUAL
                if datos['nombre'] == 'REVISION' else BandaScorePrestador.Resultado.PREAPROBADO_READ_ONLY
            )}
            banda = BandaScorePrestador(configuracion=politica, **valores_banda)
            banda.clean_fields()
            existente = politica.bandas.filter(nombre=banda.nombre).first()
            if existente:
                if any(getattr(existente, campo) != getattr(banda, campo) for campo in valores_banda):
                    raise ValidationError('La banda persistida no coincide con la definicion aprobada.')
            else:
                banda.full_clean()
                banda.save()
        validar_politica_score_completa(politica)
        snapshot = {'preparacion_inactiva': True, 'definicion': definicion, 'parametros': parametros}
        clave = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode('utf-8')).hexdigest()
        CambioPoliticaScorePrestadorAudit.objects.get_or_create(
            clave_idempotencia=clave,
            defaults=dict(politica_nueva=politica, actor=actor, motivo=motivo.strip(),
                          accion=CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO,
                          snapshot_nuevo=json.loads(json.dumps(snapshot, default=str))),
        )
        return politica

"""Prepared definition only; no provider calls, activation, or financial effects."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from contractors.models import (
    BandaScorePrestador, CambioPoliticaScorePrestadorAudit,
    ConfiguracionScorePrestador, ConfiguracionSimuladorPrestador,
)
from contractors.score.politica import validar_politica_score_completa
from contractors.services.politica_financiera_prestador import validar_configuracion_prod


ARCHIVO_DEFINICION = Path(__file__).resolve().parents[2] / 'docs' / 'parametros_score_prestadores_prod_pendientes.json'


def definicion_score_prod():
    return json.loads(ARCHIVO_DEFINICION.read_text(encoding='utf-8'))


def preparar_politica_score_prod(*, parametros=None, persistir=False, actor=None, motivo=''):
    definicion = definicion_score_prod()
    recibidos = dict(parametros or {})
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
    parametros = {**aprobados, **recibidos}
    faltantes = sorted(campo for campo in requeridos if parametros.get(campo) is None)
    if faltantes:
        if persistir:
            raise ValidationError('Politica incompleta; faltan: ' + ', '.join(faltantes))
        return {**definicion, 'parametros': parametros, 'pendientes': faltantes, 'persistida': False}
    if parametros['version_politica'] != definicion['version']:
        raise ValidationError('La version de politica debe corresponder a la definicion PROD.')
    if not str(parametros['fuente_ingreso_neto_valido_para_riesgo']).strip():
        raise ValidationError('La fuente neta debe definirse explicitamente.')
    if persistir and not getattr(settings, 'RUNNING_TESTS', False):
        raise PermissionDenied('P0-5B1 solo autoriza persistencia en bases de tests.')
    if persistir and (
        not getattr(actor, 'is_authenticated', False) or not actor.is_staff
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm('contractors.can_activate_contractor_score_policy')
        or not str(motivo).strip()
    ):
        raise PermissionDenied('La preparacion exige staff autorizado y motivo.')
    with transaction.atomic():
        financiera = ConfiguracionSimuladorPrestador.objects.select_for_update().get(
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
            if any(getattr(existente, campo) != getattr(politica, campo) for campo in valores):
                raise ValidationError('La version existente no coincide; no se reinterpreta ni activa.')
            politica = existente
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
            defaults=dict(politica_nueva=politica, actor=actor, motivo=motivo,
                          accion=CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO,
                          snapshot_nuevo=json.loads(json.dumps(snapshot, default=str))),
        )
        return politica

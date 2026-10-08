"""Prepared definition only; no provider calls, activation, or financial effects."""
import hashlib
import json
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
    parametros = dict(parametros or {})
    requeridos = set(definicion['parametros_pendientes_aprobacion']) | {
        'peso_datacredito', 'fecha_vigencia_desde',
    }
    if set(parametros) - requeridos:
        raise ValidationError('Parametros no permitidos en la definicion preparada.')
    faltantes = sorted(campo for campo in requeridos if parametros.get(campo) is None)
    if faltantes:
        if persistir:
            raise ValidationError('Politica incompleta; faltan: ' + ', '.join(faltantes))
        return {**definicion, 'pendientes': faltantes, 'persistida': False}
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
            key: value for key, value in parametros.items()
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
        if not persistir:
            return {**definicion, 'pendientes': [], 'persistida': False}
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

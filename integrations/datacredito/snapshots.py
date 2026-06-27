import hashlib
import hmac
import time
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from integrations.datacredito import decisor_client, historial_client
from integrations.datacredito.auth import SERVICIO_DECISOR, SERVICIO_HISTORIAL
from integrations.datacredito.dto import (
    ESTADO_APELLIDO_NO_COINCIDE,
    ESTADO_EXITOSA_CON_INFORMACION,
    ESTADO_EXITOSA_SIN_INFORMACION,
    ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    EntradaHistorialCredito,
    EntradaMiDecisor,
    ResultadoDatacreditoNormalizado,
    homologar_tipo_identificacion_midecisor,
)
from integrations.datacredito.exceptions import DatacreditoConfigError
from integrations.datacredito.normalizadores import normalizar_historial_credito, normalizar_midecisor_pn
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot


ESTADOS_REUTILIZABLES = {
    ESTADO_EXITOSA_CON_INFORMACION,
    ESTADO_EXITOSA_SIN_INFORMACION,
    ESTADO_IDENTIFICACION_NO_ENCONTRADA,
    ESTADO_APELLIDO_NO_COINCIDE,
}
TTL_LOCK_SEGUNDOS = 60
ESPERA_LOCK_SEGUNDOS = 0.2
INTENTOS_ESPERA_LOCK = 10


@dataclass(frozen=True)
class ResultadoConsultaDatacreditoPersistida:
    resultado_normalizado: ResultadoDatacreditoNormalizado
    snapshot: ConsultaDatacreditoSnapshot | None
    reutilizado: bool
    consultado_proveedor: bool

    @property
    def snapshot_id(self):
        return str(self.snapshot.id) if self.snapshot else None


def obtener_o_consultar_datacredito(
    *,
    servicio,
    tipo_documento,
    numero_documento,
    apellido,
    usuario=None,
    request=None,
    forzar_consulta=False,
    autorizacion_datacredito=None,
):
    servicio = _normalizar_servicio(servicio)
    configuracion = obtener_configuracion_datacredito()
    ambiente = configuracion.environment
    fingerprint = construir_request_fingerprint(
        ambiente=ambiente,
        servicio=servicio,
        tipo_documento=tipo_documento,
        numero_documento=numero_documento,
        apellido=apellido,
    )
    if not forzar_consulta:
        snapshot = _buscar_snapshot_vigente(ambiente=ambiente, servicio=servicio, fingerprint=fingerprint)
        if snapshot:
            return ResultadoConsultaDatacreditoPersistida(
                resultado_normalizado=resultado_desde_snapshot(snapshot),
                snapshot=snapshot,
                reutilizado=True,
                consultado_proveedor=False,
            )

    lock_key = f'datacredito:consulta:{ambiente}:{servicio}:{fingerprint}'
    lock_adquirido = cache.add(lock_key, '1', timeout=TTL_LOCK_SEGUNDOS)
    if not lock_adquirido and not forzar_consulta:
        snapshot = _esperar_snapshot_vigente(ambiente=ambiente, servicio=servicio, fingerprint=fingerprint)
        if snapshot:
            return ResultadoConsultaDatacreditoPersistida(
                resultado_normalizado=resultado_desde_snapshot(snapshot),
                snapshot=snapshot,
                reutilizado=True,
                consultado_proveedor=False,
            )

    try:
        raw, normalizado = _consultar_y_normalizar(
            servicio=servicio,
            tipo_documento=tipo_documento,
            numero_documento=numero_documento,
            apellido=apellido,
        )
        snapshot = None
        if es_resultado_reutilizable(normalizado):
            snapshot = crear_snapshot_datacredito(
                servicio=servicio,
                ambiente=ambiente,
                tipo_documento=tipo_documento,
                numero_documento=numero_documento,
                apellido=apellido,
                resultado=normalizado,
                raw=raw,
                usuario=usuario,
                request=request,
                autorizacion_datacredito=autorizacion_datacredito,
            )
        return ResultadoConsultaDatacreditoPersistida(
            resultado_normalizado=normalizado,
            snapshot=snapshot,
            reutilizado=False,
            consultado_proveedor=True,
        )
    finally:
        if lock_adquirido:
            cache.delete(lock_key)


def buscar_snapshot_datacredito_vigente(*, servicio, tipo_documento, numero_documento, apellido):
    servicio = _normalizar_servicio(servicio)
    configuracion = obtener_configuracion_datacredito()
    fingerprint = construir_request_fingerprint(
        ambiente=configuracion.environment,
        servicio=servicio,
        tipo_documento=tipo_documento,
        numero_documento=numero_documento,
        apellido=apellido,
    )
    return _buscar_snapshot_vigente(
        ambiente=configuracion.environment,
        servicio=servicio,
        fingerprint=fingerprint,
    )


def crear_snapshot_datacredito(
    *,
    servicio,
    ambiente,
    tipo_documento,
    numero_documento,
    apellido,
    resultado,
    raw,
    usuario=None,
    request=None,
    source=ConsultaDatacreditoSnapshot.SOURCE_CONSULTA_REAL,
    autorizacion_datacredito=None,
):
    ahora = timezone.now()
    vigente_hasta = ahora + timedelta(days=obtener_dias_reutilizacion())
    resultado_seguro = serializar_resultado_normalizado(resultado)
    return ConsultaDatacreditoSnapshot.objects.create(
        servicio=servicio,
        ambiente=ambiente,
        proveedor=ConsultaDatacreditoSnapshot.PROVEEDOR_DATACREDITO_REAL,
        tipo_documento=_normalizar_tipo_documento(tipo_documento),
        request_fingerprint=construir_request_fingerprint(
            ambiente=ambiente,
            servicio=servicio,
            tipo_documento=tipo_documento,
            numero_documento=numero_documento,
            apellido=apellido,
        ),
        documento_hash=construir_documento_hash(
            ambiente=ambiente,
            tipo_documento=tipo_documento,
            numero_documento=numero_documento,
        ),
        documento_enmascarado=_enmascarar_documento(numero_documento),
        estado_normalizado=resultado.estado,
        http_status=getattr(raw, 'status_code', None),
        codigo_funcional=getattr(raw, 'codigo_funcional', None) or resultado.codigo_respuesta or resultado.response_code or '',
        proveedor_respondio=bool(getattr(raw, 'status_code', None)),
        consulta_procesada=True,
        con_informacion=resultado.con_informacion,
        utilizable_para_score=_utilizable_para_score(resultado),
        requiere_revision_manual=resultado.requiere_revision_manual,
        requiere_revision_cumplimiento=resultado.requiere_revision_cumplimiento,
        resultado_normalizado=resultado_seguro,
        consulted_at=ahora,
        vigente_hasta=vigente_hasta,
        created_by=usuario if getattr(usuario, 'is_authenticated', False) else None,
        request_id=_obtener_request_id(request),
        source=source,
        autorizacion_id=str(getattr(autorizacion_datacredito, 'id', '') or ''),
        autorizacion_version_texto=getattr(autorizacion_datacredito, 'version_texto', '') or '',
        autorizacion_texto_hash=getattr(autorizacion_datacredito, 'texto_hash', '') or '',
        autorizacion_accepted_at=getattr(autorizacion_datacredito, 'accepted_at', None),
    )


def es_resultado_reutilizable(resultado):
    return resultado.estado in ESTADOS_REUTILIZABLES


def serializar_resultado_normalizado(resultado):
    return {
        'estado': resultado.estado,
        'score_midecisor': resultado.score_midecisor,
        'score': resultado.score,
        'scores_hdc': list(resultado.scores_hdc),
        'score_normalizado_0_1000': resultado.score_normalizado_0_1000,
        'saldo_actual': _valor_json_seguro(resultado.saldo_actual),
        'saldo_mora': _valor_json_seguro(resultado.saldo_mora),
        'valor_cuota_total': _valor_json_seguro(resultado.valor_cuota_total),
        'creditos_vigentes': resultado.creditos_vigentes,
        'creditos_cerrados': resultado.creditos_cerrados,
        'porcentaje_deuda': _valor_json_seguro(resultado.porcentaje_deuda),
        'ingreso_estimado': _valor_json_seguro(resultado.ingreso_estimado),
        'porcentaje_cuota_vs_ingreso': _valor_json_seguro(resultado.porcentaje_cuota_vs_ingreso),
        'mora_severa': resultado.mora_severa,
        'mora_actual': resultado.mora_actual,
        'viabilidad': resultado.viabilidad,
        'viable': resultado.viable,
        'rating_recaudo': resultado.rating_recaudo,
        'monto_sugerido': resultado.monto_sugerido,
        'cantidad_alertas': resultado.cantidad_alertas,
        'alertas_resumen': list(resultado.alertas_resumen),
        'requiere_revision_manual': resultado.requiere_revision_manual,
        'requiere_revision_cumplimiento': resultado.requiere_revision_cumplimiento,
        'hdc_estructura': (resultado.metadata_segura or {}).get('hdc_estructura'),
        'hdc_resumen': (resultado.metadata_segura or {}).get('hdc_resumen'),
        'codigo_respuesta': resultado.codigo_respuesta,
        'response_code': resultado.response_code,
        'con_informacion': resultado.con_informacion,
        'disponible': resultado.disponible,
        'fuente': resultado.fuente,
        'servicio': resultado.servicio,
        'nivel_riesgo': resultado.nivel_riesgo,
        'error_tipo': resultado.error_tipo,
    }


def resultado_desde_snapshot(snapshot):
    datos = snapshot.resultado_normalizado or {}
    return ResultadoDatacreditoNormalizado(
        disponible=bool(datos.get('disponible')),
        fuente=datos.get('fuente') or '',
        servicio=datos.get('servicio') or snapshot.servicio,
        estado=datos.get('estado') or snapshot.estado_normalizado,
        con_informacion=datos.get('con_informacion'),
        codigo_respuesta=datos.get('codigo_respuesta') or snapshot.codigo_funcional or None,
        score=datos.get('score'),
        score_midecisor=datos.get('score_midecisor'),
        scores_hdc=tuple(datos.get('scores_hdc') or ()),
        score_normalizado_0_1000=datos.get('score_normalizado_0_1000'),
        viable=datos.get('viable'),
        monto_sugerido=datos.get('monto_sugerido'),
        saldo_actual=_decimal_desde_json(datos.get('saldo_actual')),
        saldo_mora=_decimal_desde_json(datos.get('saldo_mora')),
        valor_cuota_total=_decimal_desde_json(datos.get('valor_cuota_total')),
        creditos_vigentes=datos.get('creditos_vigentes'),
        creditos_cerrados=datos.get('creditos_cerrados'),
        porcentaje_deuda=_decimal_desde_json(datos.get('porcentaje_deuda')),
        ingreso_estimado=_decimal_desde_json(datos.get('ingreso_estimado')),
        porcentaje_cuota_vs_ingreso=_decimal_desde_json(datos.get('porcentaje_cuota_vs_ingreso')),
        nivel_riesgo=datos.get('nivel_riesgo') or 'NO_DISPONIBLE',
        mora_severa=datos.get('mora_severa'),
        mora_actual=datos.get('mora_actual'),
        response_code=datos.get('response_code') or snapshot.codigo_funcional or None,
        viabilidad=datos.get('viabilidad'),
        rating_recaudo=datos.get('rating_recaudo'),
        cantidad_alertas=int(datos.get('cantidad_alertas') or 0),
        requiere_revision_manual=bool(datos.get('requiere_revision_manual')),
        requiere_revision_cumplimiento=bool(datos.get('requiere_revision_cumplimiento')),
        error_tipo=datos.get('error_tipo'),
        alertas_resumen=tuple(datos.get('alertas_resumen') or ()),
        metadata_segura={
            'snapshot_id': str(snapshot.id),
            'reutilizado': True,
            'servicio': snapshot.servicio,
            'ambiente': snapshot.ambiente,
            'hdc_estructura': datos.get('hdc_estructura'),
            'hdc_resumen': datos.get('hdc_resumen'),
        },
    )


def construir_request_fingerprint(*, ambiente, servicio, tipo_documento, numero_documento, apellido):
    mensaje = '|'.join(
        [
            _normalizar_texto(ambiente),
            _normalizar_servicio(servicio),
            _normalizar_tipo_documento(tipo_documento),
            _normalizar_documento(numero_documento),
            _normalizar_texto(apellido),
        ]
    )
    return _hmac_seguro(mensaje)


def construir_documento_hash(*, ambiente, tipo_documento, numero_documento):
    mensaje = '|'.join(
        [
            _normalizar_texto(ambiente),
            _normalizar_tipo_documento(tipo_documento),
            _normalizar_documento(numero_documento),
        ]
    )
    return _hmac_seguro(mensaje)


def obtener_dias_reutilizacion():
    return int(getattr(settings, 'DATACREDITO_REUSE_DAYS', 30) or 30)


def _consultar_y_normalizar(*, servicio, tipo_documento, numero_documento, apellido):
    if servicio == SERVICIO_DECISOR:
        raw = decisor_client.consultar_midecisor_persona_natural(
            EntradaMiDecisor(
                tipo_identificacion=tipo_documento,
                numero_identificacion=numero_documento,
                apellido_razon_social=apellido,
            )
        )
        return raw, normalizar_midecisor_pn(raw)
    if servicio == SERVICIO_HISTORIAL:
        configuracion = obtener_configuracion_datacredito()
        raw = historial_client.consultar_historial_credito(
            EntradaHistorialCredito(
                tipo_identificacion=tipo_documento,
                numero_identificacion=numero_documento,
                apellido=apellido,
                parametros=configuracion.parametros_historial,
            )
        )
        return raw, normalizar_historial_credito(raw)
    raise DatacreditoConfigError('Servicio DataCredito invalido.')


def _buscar_snapshot_vigente(*, ambiente, servicio, fingerprint):
    return (
        ConsultaDatacreditoSnapshot.objects.filter(
            ambiente=ambiente,
            servicio=servicio,
            request_fingerprint=fingerprint,
            source=ConsultaDatacreditoSnapshot.SOURCE_CONSULTA_REAL,
            vigente_hasta__gt=timezone.now(),
            estado_normalizado__in=ESTADOS_REUTILIZABLES,
        )
        .order_by('-consulted_at')
        .first()
    )


def _esperar_snapshot_vigente(*, ambiente, servicio, fingerprint):
    for _ in range(INTENTOS_ESPERA_LOCK):
        time.sleep(ESPERA_LOCK_SEGUNDOS)
        snapshot = _buscar_snapshot_vigente(ambiente=ambiente, servicio=servicio, fingerprint=fingerprint)
        if snapshot:
            return snapshot
    return None


def _hmac_seguro(mensaje):
    secreto = getattr(settings, 'DATACREDITO_DOCUMENT_HASH_SECRET', '') or ''
    if not secreto:
        if not getattr(settings, 'DEBUG', False):
            raise DatacreditoConfigError('DATACREDITO_DOCUMENT_HASH_SECRET es obligatorio.')
        secreto = getattr(settings, 'SECRET_KEY', 'datacredito-dev-secret')
    return hmac.new(secreto.encode('utf-8'), mensaje.encode('utf-8'), hashlib.sha256).hexdigest()


def _normalizar_servicio(servicio):
    servicio = str(servicio or '').strip().lower()
    if servicio not in {SERVICIO_DECISOR, SERVICIO_HISTORIAL}:
        raise DatacreditoConfigError('Servicio DataCredito invalido.')
    return servicio


def _normalizar_tipo_documento(tipo_documento):
    valor = str(tipo_documento or '').strip().upper()
    if valor == 'CC':
        return 'CC'
    homologado = homologar_tipo_identificacion_midecisor(valor)
    return 'CC' if homologado == '1' else valor


def _normalizar_documento(numero_documento):
    return ''.join(caracter for caracter in str(numero_documento or '') if caracter.isalnum()).upper()


def _normalizar_texto(valor):
    texto = unicodedata.normalize('NFKD', str(valor or '').strip().upper())
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = ''.join(caracter if caracter.isalnum() else ' ' for caracter in texto)
    return ' '.join(texto.split())


def _enmascarar_documento(documento):
    texto = _normalizar_documento(documento)
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"


def _utilizable_para_score(resultado):
    tiene_score = bool(resultado.score_midecisor or resultado.score or resultado.scores_hdc)
    return bool(resultado.disponible and tiene_score)


def _valor_json_seguro(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def _decimal_desde_json(valor):
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _obtener_request_id(request):
    if not request:
        return ''
    return (
        getattr(request, 'request_id', None)
        or getattr(request, 'id', None)
        or request.headers.get('X-Request-ID', '')
    )

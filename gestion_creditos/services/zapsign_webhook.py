"""Authenticate callbacks and project remote evidence without retaining provider PII."""
from dataclasses import dataclass
import hmac
import json
import logging
import re

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gestion_creditos.models import Credito, OrigenCreditoPrestador, Pagare, ZapSignWebhookLog


TOKEN = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
HEADER = re.compile(r'[A-Za-z][A-Za-z0-9-]*\Z')
RECEPTORES = {'libranza': 'ZAPSIGN_WEBHOOK', 'prestadores': 'ZAPSIGN_PRESTADORES_WEBHOOK'}
logger = logging.getLogger(__name__)


class EvidenciaZapSignInvalida(Exception):
    """Controlled failure: never include provider values in exceptions."""


def autenticar_webhook(request, *, receptor='libranza'):
    prefijo = RECEPTORES.get(receptor)
    if prefijo is None:
        return 503
    secreto = getattr(settings, prefijo + '_SECRET', None)
    header = getattr(settings, prefijo + '_HEADER', None)
    if (not isinstance(header, str) or not HEADER.fullmatch(header)
            or header.lower() in {'cookie', 'host', 'content-type', 'content-length'}):
        return 503
    # Temporary P1 debt: only an absent Libranza secret permits legacy receipt.
    if receptor == 'libranza' and secreto is None:
        return None
    if (not isinstance(secreto, str) or not secreto or len(secreto) > 512
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in secreto)):
        return 503
    otro_prefijo = RECEPTORES['prestadores' if receptor == 'libranza' else 'libranza']
    otro = getattr(settings, otro_prefijo + '_SECRET', None)
    if isinstance(otro, str) and otro and hmac.compare_digest(secreto.encode('utf-8'), otro.encode('utf-8')):
        return 503
    recibido = request.headers.get(header, '')
    if header.lower() == 'authorization' and recibido.lower().startswith('bearer '):
        recibido = recibido[7:]
    return None if hmac.compare_digest(recibido.encode('utf-8'), secreto.encode('utf-8')) else 403


@dataclass(frozen=True)
class ReferenciaFirma:
    credito_id: int
    pagare_id: int
    correo: str
    enviada_en: object
    formalizacion_id: int | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class FirmaRemota:
    accion: str
    estado: str
    firmantes: int


def referencia_firma(credito, pagare, formalizacion=None):
    if formalizacion:
        correo = formalizacion.credito_libranza.correo_electronico
    else:
        correo = credito.cliente_email
    return ReferenciaFirma(
        credito.pk, pagare.pk, str(correo or '').strip().lower(), pagare.fecha_envio,
        formalizacion.pk if formalizacion else None,
        formalizacion.clave_idempotencia if formalizacion else None,
    )


def estado_terminal(credito, pagare, formalizacion=None):
    if credito.estado == Credito.EstadoCredito.ANULADO:
        return 'credit_cancelled_ignored' if formalizacion else 'cancelled_credit_ignored'
    if pagare.estado == Pagare.EstadoPagare.SIGNED:
        return 'already_processed'
    if pagare.estado in {Pagare.EstadoPagare.REFUSED, Pagare.EstadoPagare.CANCELLED}:
        return 'terminal_state_ignored'
    if (credito.estado != Credito.EstadoCredito.PENDIENTE_FIRMA
            or pagare.estado != Pagare.EstadoPagare.SENT
            or (formalizacion and formalizacion.estado != 'PENDIENTE_FIRMA')):
        return 'invalid_state_ignored'
    return None


def verificar_documento_remoto(token, referencia):
    from gestion_creditos.services.zapsign_client import ZapSignClient
    cliente = ZapSignClient()
    data = cliente.consultar_documento(token)
    if (not isinstance(data, dict) or data.get('token') != token
            or not referencia.correo
            or (referencia.external_id is not None and data.get('external_id') != referencia.external_id)):
        raise EvidenciaZapSignInvalida('DOCUMENTO_NO_COINCIDE')
    if 'sandbox' in data and (type(data['sandbox']) is not bool
                             or data['sandbox'] != (cliente.environment == 'sandbox')):
        raise EvidenciaZapSignInvalida('AMBIENTE_NO_COINCIDE')
    estado = data.get('status')
    if estado not in {'signed', 'pending', 'refused', 'recusado'}:
        raise EvidenciaZapSignInvalida('ESTADO_NO_RECONOCIDO')
    firmantes = data.get('signers')
    if not isinstance(firmantes, list) or not firmantes:
        raise EvidenciaZapSignInvalida('FIRMANTES_AUSENTES')
    tokens = set()
    titular = False
    for firmante in firmantes:
        if (not isinstance(firmante, dict) or not isinstance(firmante.get('token'), str)
                or not TOKEN.fullmatch(firmante['token']) or firmante['token'] in tokens):
            raise EvidenciaZapSignInvalida('FIRMANTES_INVALIDOS')
        tokens.add(firmante['token'])
        correo = firmante.get('email')
        titular |= isinstance(correo, str) and correo.strip().lower() == referencia.correo
        if estado == 'signed':
            if firmante.get('status') != 'signed':
                raise EvidenciaZapSignInvalida('FIRMANTES_PENDIENTES')
            try:
                fecha = parse_datetime(firmante.get('signed_at') or '')
            except (TypeError, ValueError):
                fecha = None
            if (fecha is None or timezone.is_naive(fecha) or fecha > timezone.now()
                    or (referencia.enviada_en and fecha < referencia.enviada_en)):
                raise EvidenciaZapSignInvalida('FECHA_FIRMA_INVALIDA')
    if not titular:
        raise EvidenciaZapSignInvalida('TITULAR_NO_COINCIDE')
    if estado == 'signed' and data.get('deleted'):
        raise EvidenciaZapSignInvalida('DOCUMENTO_ELIMINADO')
    accion = 'signed' if estado == 'signed' else 'refused' if estado in {'refused', 'recusado'} else 'ignored'
    return FirmaRemota(accion, estado, len(firmantes))


def _propiedad_documental(token, digest):
    """Ownership comes from local relations, never callback labels or credit line."""
    from contractors.models import FormalizacionCreditoPrestador

    formalizacion = FormalizacionCreditoPrestador.objects.select_related(
        'credito__usuario', 'credito_libranza', 'pagare',
    ).filter(proveedor_document_id_hash=digest).first()
    pagare_token = Pagare.objects.select_related('credito__usuario').filter(zapsign_doc_token=token).first()
    if formalizacion:
        pagare = formalizacion.pagare
        # Prestadores deliberately stores the document hash, not its raw token.
        valida = bool(pagare and formalizacion.pagare_id == pagare.pk
                      and (pagare_token is None or pagare_token.pk == pagare.pk)
                      and (not pagare.zapsign_doc_token or pagare.zapsign_doc_token == token)
                      and formalizacion.credito_id == pagare.credito_id
                      and formalizacion.credito_libranza.credito_id == pagare.credito_id
                      and OrigenCreditoPrestador.objects.filter(
                          pk=formalizacion.origen_credito_prestador_id,
                          credito_id=pagare.credito_id,
                          credito_libranza_id=formalizacion.credito_libranza_id).exists())
        return 'prestadores', formalizacion, pagare, valida
    pagare = pagare_token
    if not pagare:
        return None, None, None, False
    # Prestadores credits also use LIBRANZA: a missing/changed hash must not
    # silently fall through to the legacy transition path.
    es_prestador = (
        OrigenCreditoPrestador.objects.filter(credito_id=pagare.credito_id).exists()
        or FormalizacionCreditoPrestador.objects.filter(credito_id=pagare.credito_id).exists()
        or FormalizacionCreditoPrestador.objects.filter(pagare_id=pagare.pk).exists()
    )
    return ('prestadores' if es_prestador else 'libranza'), None, pagare, not es_prestador


def procesar_webhook(request, *, receptor):
    """Single authenticated pipeline for both routes; remote I/O precedes locks."""
    from contractors.models import FormalizacionCreditoPrestador
    from contractors.services.formalizacion import (
        hash_documento_proveedor, procesar_callback_formalizacion_prestador,
    )
    from gestion_creditos import credit_services

    rechazo = autenticar_webhook(request, receptor=receptor)
    if rechazo:
        return JsonResponse({'error': 'Webhook unavailable' if rechazo == 503 else 'Unauthorized'},
                            status=rechazo)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    token = payload.get('token') or payload.get('doc_token')
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return JsonResponse({'error': 'Invalid document reference'}, status=400)
    event = payload.get('event') or payload.get('event_type')
    event = event if event in ('doc_signed', 'doc_refused') else 'ignored'
    digest = hash_documento_proveedor(token)
    autenticado = getattr(settings, RECEPTORES[receptor] + '_SECRET', None) is not None
    resumen = {'event': event, 'status': 'unverified', 'receptor': receptor}
    if not autenticado:
        resumen['autenticacion'] = 'legacy_sin_secreto'
    webhook_log = ZapSignWebhookLog.objects.create(
        doc_token='sha256:' + digest, event=event,
        payload=resumen,
        headers={}, ip_address='0.0.0.0', signature_valid=autenticado, processed=False,
    )

    def terminar(estado, *, codigo=200, procesado=True):
        webhook_log.processed = procesado
        if estado in {'credit_cancelled_ignored', 'cancelled_credit_ignored'}:
            webhook_log.error_message = 'ANULADO'
        elif codigo != 200:
            webhook_log.error_message = estado
        webhook_log.payload['resultado'] = estado
        webhook_log.save(update_fields=['processed', 'error_message', 'payload'])
        return JsonResponse({'status': estado}, status=codigo)

    if event == 'ignored':
        return terminar('event_ignored')
    propietario, formalizacion, pagare, coherente = _propiedad_documental(token, digest)
    if propietario is None:
        return terminar('document_not_found_ignored')
    webhook_log.payload['producto'] = propietario
    if propietario != receptor:
        return terminar('product_mismatch_ignored')
    if not coherente:
        return terminar('local_document_context_invalid', codigo=409, procesado=False)
    credito = pagare.credito
    terminal = estado_terminal(credito, pagare, formalizacion)
    if terminal:
        return terminar(terminal)
    referencia = referencia_firma(credito, pagare, formalizacion)
    try:
        remoto = verificar_documento_remoto(token, referencia)
    except Exception:
        return terminar('remote_verification_failed', codigo=503, procesado=False)
    webhook_log.payload['status'] = remoto.estado
    if remoto.accion == 'ignored':
        return terminar('remote_pending_ignored')
    try:
        with transaction.atomic():
            if formalizacion:
                formalizacion = FormalizacionCreditoPrestador.objects.select_for_update(
                    of=('self',)).select_related('credito_libranza').get(pk=referencia.formalizacion_id)
            credito = Credito.objects.select_for_update().get(pk=referencia.credito_id)
            pagare = Pagare.objects.select_for_update().get(pk=referencia.pagare_id)
            propietario_actual, formal_actual, pagare_actual, coherente = _propiedad_documental(token, digest)
            if (not coherente or propietario_actual != receptor or pagare_actual.pk != pagare.pk
                    or (formal_actual.pk if formal_actual else None) != referencia.formalizacion_id
                    or referencia_firma(credito, pagare, formalizacion) != referencia):
                return terminar('local_context_changed', codigo=409, procesado=False)
            terminal = estado_terminal(credito, pagare, formalizacion)
            if terminal:
                return terminar(terminal)
            if formalizacion:
                resultado = procesar_callback_formalizacion_prestador(
                    documento_id=token, accion=remoto.accion, estado_proveedor=remoto.estado,
                )
                estado = resultado['estado']
            elif remoto.accion == 'signed':
                pagare.estado = Pagare.EstadoPagare.SIGNED
                pagare.fecha_firma = timezone.now()
                pagare.zapsign_status = remoto.estado
                pagare.evidencias = {'verificacion': 'api_zapsign', 'estado': remoto.estado,
                                     'firmantes': remoto.firmantes}
                pagare.save(update_fields=['estado', 'fecha_firma', 'zapsign_status', 'evidencias'])
                credit_services.gestionar_cambio_estado_credito(
                    credito=credito, nuevo_estado=Credito.EstadoCredito.FIRMADO,
                    motivo='Pagare confirmado mediante API ZapSign',
                )
                credit_services.iniciar_proceso_desembolso(credito)
                transaction.on_commit(lambda: _guardar_pdf_zapsign_verificado(pagare.pk))
                estado = 'ok'
            else:
                pagare.estado = Pagare.EstadoPagare.REFUSED
                pagare.fecha_rechazo = timezone.now()
                pagare.zapsign_status = remoto.estado
                pagare.evidencias = {'verificacion': 'api_zapsign', 'estado': remoto.estado}
                pagare.save(update_fields=['estado', 'fecha_rechazo', 'zapsign_status', 'evidencias'])
                estado = 'refused_recorded'
            return terminar(estado)
    except Exception:
        return terminar('processing_failed', codigo=500, procesado=False)


def _guardar_pdf_zapsign_verificado(pagare_id):
    """Best-effort local copy after commit, never trust a callback download URL."""
    try:
        from gestion_creditos.services.zapsign_client import descargar_pdf_firmado_pagare
        pagare = Pagare.objects.get(pk=pagare_id)
        if pagare.estado == Pagare.EstadoPagare.SIGNED and not pagare.archivo_pdf_firmado:
            contenido = descargar_pdf_firmado_pagare(pagare)
            if contenido:
                pagare.archivo_pdf_firmado.save(
                    f'{pagare.numero_pagare}_firmado.pdf', ContentFile(contenido), save=True,
                )
    except Exception:
        logger.warning('Copia local del PDF firmado pendiente; sin cambios de estado.')

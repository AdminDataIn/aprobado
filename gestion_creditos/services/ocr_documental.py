"""Versioned local OCR; never changes application or financial decisions.

Lock order: capture session, application context, processing. Tesseract always
runs outside transactions. Publication requires the current execution token.
"""
import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gestion_creditos.models import (
    SesionCapturaDocumental as Sesion, ProcesamientoOCRDocumental as OCR,
    ComparacionOCRDocumental as Comparacion, EventoCapturaDocumental as Evento,
    CreditoLibranza,
)
from .ocr import tesseract_adapter as motor
from .ocr.parser_documento_colombia import VERSION, normalizar_numero, normalizar_texto, parsear

logger = logging.getLogger(__name__)
TERMINALES = {'COMPLETADO', 'REQUIERE_REVISION', 'FALLIDO'}
RECUPERABLES = {'OCR_TIMEOUT', 'OCR_ERROR_TECNICO'}
MAX_INTENTOS = 3


def firma(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def configuracion_actual():
    return {'motor': 'tesseract', 'version_motor': getattr(settings, 'OCR_DOCUMENTAL_VERSION_MOTOR', ''),
            'modelo_sha256': getattr(settings, 'OCR_DOCUMENTAL_MODELO_SHA256', ''),
            'parser': VERSION, 'idioma': 'spa', 'oem': 1, 'psm': 6,
            'timeout': max(1, min(60, int(getattr(settings, 'OCR_DOCUMENTAL_TIMEOUT', 20))))}


def evento(p, codigo):
    Evento.objects.create(sesion_id=p.sesion_id, procesamiento_ocr=p, evento=codigo)


def encolar(procesamiento_id):
    try:
        from gestion_creditos.tasks import procesar_ocr_documental_task
        procesar_ocr_documental_task.apply_async(args=[procesamiento_id], retry=False)
        return True
    except Exception:
        logger.warning('OCR_ENCOLADO_FALLIDO procesamiento_id=%s', procesamiento_id)
        return False


@transaction.atomic
def asegurar_procesamiento(sesion_id):
    sesion = Sesion.objects.select_for_update().get(pk=sesion_id)
    _bloquear_contexto(sesion)
    if sesion.estado not in {'FINALIZADA', 'UTILIZADA'}:
        return None
    captures = {c.lado: c for c in sesion.capturas.filter(activo=True, purgado_en__isnull=True,
                                                        estado_tecnico='VALIDO_TECNICAMENTE')}
    if set(captures) != {'FRONTAL', 'TRASERA'}:
        return None
    config = configuracion_actual()
    p, created = OCR.objects.get_or_create(sesion=sesion, captura_frontal=captures['FRONTAL'],
        captura_trasera=captures['TRASERA'], firma_configuracion=firma(config), defaults={
            'version_parser': VERSION, 'configuracion': config,
            'firma_inputs': firma({side: [str(c.pk), c.hash_archivo] for side, c in captures.items()})})
    if created:
        anteriores = sesion.procesamientos_ocr.exclude(pk=p.pk)
        anteriores.update(vigente=False)
        Comparacion.objects.filter(procesamiento__in=anteriores).update(vigente=False)
        evento(p, 'OCR_CREADO')
        transaction.on_commit(lambda: encolar(p.pk))
    return p


def _vigente(p, sesion):
    if not p.vigente or p.purgado_en or sesion.estado not in {'FINALIZADA', 'UTILIZADA'}:
        return False
    if sesion.estado == 'UTILIZADA':
        lookup = {'credito_id': sesion.credito_id} if sesion.producto == 'LIBRANZA' else {'solicitud_id': sesion.solicitud_id}
        latest = Sesion.objects.filter(**lookup, estado='UTILIZADA').order_by('-utilizado_en', '-creado_en', '-pk').first()
        if latest.pk != sesion.pk:
            return False
    captures = list(sesion.capturas.filter(activo=True, purgado_en__isnull=True,
        estado_tecnico='VALIDO_TECNICAMENTE', pk__in=[p.captura_frontal_id, p.captura_trasera_id]))
    return (len(captures) == 2 and firma({c.lado: [str(c.pk), c.hash_archivo] for c in captures}) == p.firma_inputs)


def _lock(procesamiento_id):
    sesion_id = OCR.objects.values_list('sesion_id', flat=True).get(pk=procesamiento_id)
    sesion = Sesion.objects.select_for_update().get(pk=sesion_id)
    _bloquear_contexto(sesion)
    return sesion, OCR.objects.select_for_update().get(pk=procesamiento_id)


def _bloquear_contexto(sesion):
    if sesion.estado != 'UTILIZADA':
        return
    if sesion.producto == 'LIBRANZA':
        CreditoLibranza.objects.select_for_update().filter(credito_id=sesion.credito_id).first()
    else:
        from contractors.models import ContractorApplication
        ContractorApplication.objects.select_for_update().get(pk=sesion.solicitud_id)


def _fallar(p, codigo):
    p.estado = 'FALLIDO'
    p.codigo_error = codigo
    p.finalizado_en = timezone.now()
    p.reservado_hasta = p.token_ejecucion = None
    p.save()
    evento(p, 'OCR_FALLIDO')


def procesar(procesamiento_id):
    with transaction.atomic():
        sesion, p = _lock(procesamiento_id)
        if p.estado in TERMINALES or (p.estado == 'PROCESANDO' and p.reservado_hasta
                                     and p.reservado_hasta > timezone.now()):
            return
        if not _vigente(p, sesion):
            _fallar(p, 'OCR_INPUT_NO_VIGENTE')
            return
        if p.numero_intentos >= MAX_INTENTOS:
            _fallar(p, 'OCR_INTENTOS_AGOTADOS')
            return
        p.token_ejecucion = token = uuid.uuid4()
        p.estado = 'PROCESANDO'
        p.iniciado_en = timezone.now()
        p.reservado_hasta = p.iniciado_en + timedelta(seconds=2 * (p.configuracion['timeout'] + 15) + 60)
        p.numero_intentos += 1
        p.codigo_error = ''
        p.save()
        evento(p, 'OCR_INICIADO')
    error = ''
    try:
        if p.version_parser != VERSION:
            raise motor.ErrorOCR('OCR_VERSION_PARSER_INVALIDA')
        front = motor.extraer(p.captura_frontal, p.configuracion)
        back = motor.extraer(p.captura_trasera, p.configuracion)
        if front['version'] != back['version']:
            raise motor.ErrorOCR('OCR_VERSION_MOTOR_INVALIDA')
        result = parsear(front, back)
    except motor.ErrorOCR as exc:
        error = exc.codigo
    except Exception:
        error = 'OCR_ERROR_TECNICO'
    with transaction.atomic():
        sesion, actual = _lock(procesamiento_id)
        if actual.token_ejecucion != token or actual.estado != 'PROCESANDO':
            return
        if not _vigente(actual, sesion):
            _fallar(actual, 'OCR_INPUT_NO_VIGENTE')
            return
        if error:
            if error in RECUPERABLES and actual.numero_intentos < MAX_INTENTOS:
                actual.estado = 'PENDIENTE'
                actual.codigo_error = error
                actual.token_ejecucion = actual.reservado_hasta = None
                actual.save()
                evento(actual, 'OCR_REENCOLADO')
                transaction.on_commit(lambda: encolar(actual.pk))
            else:
                _fallar(actual, error)
            return
        for key, value in result.items():
            setattr(actual, key, value)
        actual.version_motor = front['version']
        actual.finalizado_en = timezone.now()
        actual.token_ejecucion = actual.reservado_hasta = None
        actual.save()
        evento(actual, 'OCR_' + actual.estado)
        sincronizar_comparacion_ocr(sesion.pk)


@transaction.atomic
def sincronizar_comparacion_ocr(sesion_id):
    sesion = Sesion.objects.select_for_update().get(pk=sesion_id)
    if sesion.estado != 'UTILIZADA':
        return
    if sesion.producto == 'LIBRANZA':
        context = CreditoLibranza.objects.select_for_update().filter(credito_id=sesion.credito_id).first()
        if context is None:
            return
        lookup = {'libranza': context, 'solicitud': None}
        sessions = Sesion.objects.filter(credito_id=sesion.credito_id, estado='UTILIZADA')
        number, doc_type = context.cedula, context.tipo_documento or 'CC'
    else:
        from contractors.models import ContractorApplication
        context = ContractorApplication.objects.select_for_update().get(pk=sesion.solicitud_id)
        lookup = {'solicitud': context, 'libranza': None}
        sessions = Sesion.objects.filter(solicitud_id=sesion.solicitud_id, estado='UTILIZADA')
        number, doc_type = context.numero_documento, context.tipo_documento
    newest = sessions.order_by('-utilizado_en', '-creado_en', '-pk').first()
    if newest.pk != sesion.pk:
        sesion.procesamientos_ocr.update(vigente=False)
        Comparacion.objects.filter(procesamiento__sesion=sesion).update(vigente=False)
        return
    old_sessions = sessions.exclude(pk=sesion.pk)
    OCR.objects.filter(sesion__in=old_sessions).update(vigente=False)
    Comparacion.objects.filter(**lookup).exclude(procesamiento__sesion=sesion).update(vigente=False)
    p = sesion.procesamientos_ocr.select_for_update().filter(vigente=True, purgado_en__isnull=True,
        estado__in=['COMPLETADO', 'REQUIERE_REVISION']).first()
    if not p:
        return
    snapshot = {'numero_documento': number, 'nombres': context.nombres,
                'apellidos': context.apellidos, 'tipo_documento': doc_type}
    version = firma(snapshot)
    results = {}
    for field in ('numero_documento', 'nombres', 'apellidos'):
        normalize = normalizar_numero if field == 'numero_documento' else normalizar_texto
        known = normalize(snapshot[field])
        extracted = getattr(p, field + '_normalizado')
        if not known or doc_type != 'CC':
            results[field] = 'NO_DISPONIBLE'
        elif not extracted:
            results[field] = 'NO_LEIBLE'
        else:
            results[field] = 'COINCIDE' if known == extracted else 'NO_COINCIDE'
    discrepancies = [key for key, value in results.items() if value != 'COINCIDE']
    comp, created = Comparacion.objects.get_or_create(procesamiento=p, version_datos=version,
        version_comparador='1', defaults={**lookup, 'snapshot': snapshot, 'resultados': results,
            'discrepancias': discrepancies, 'requiere_revision': bool(discrepancies) or p.estado == 'REQUIERE_REVISION'})
    Comparacion.objects.filter(**lookup).exclude(pk=comp.pk).update(vigente=False)
    if not comp.vigente:
        comp.vigente = True
        comp.save(update_fields=['vigente'])
    if created:
        evento(p, 'COMPARACION_OCR_CREADA')


def recuperar(limite=100):
    limite = max(1, min(int(limite), 1000))
    candidates = list(OCR.objects.filter(Q(estado='PENDIENTE') |
        Q(estado='PROCESANDO', reservado_hasta__lte=timezone.now())).order_by('pk').values_list('pk', flat=True)[:limite])
    count = 0
    for pk in candidates:
        with transaction.atomic():
            sesion, p = _lock(pk)
            if p.estado in TERMINALES or (p.reservado_hasta and p.reservado_hasta > timezone.now()):
                continue
            if not _vigente(p, sesion):
                _fallar(p, 'OCR_INPUT_NO_VIGENTE')
                continue
            if p.numero_intentos >= MAX_INTENTOS:
                _fallar(p, 'OCR_INTENTOS_AGOTADOS')
                continue
            p.estado = 'PENDIENTE'
            p.token_ejecucion = None
            # Short publication lease avoids competing recovery commands flooding the broker.
            p.reservado_hasta = timezone.now() + timedelta(seconds=60)
            p.save()
            evento(p, 'OCR_REENCOLADO')
            transaction.on_commit(lambda pk=pk: encolar(pk))
            count += 1
    return count


def purgar_resultados(sesion_id):
    """Caller holds the session lock. Retain technical audit, erase extracted PII."""
    fields = {f.name: '' for f in OCR._meta.fields if f.name.endswith(('_bruto', '_normalizado'))}
    Comparacion.objects.filter(procesamiento__sesion_id=sesion_id).delete()
    OCR.objects.filter(sesion_id=sesion_id).update(**fields, fecha_nacimiento=None,
        fecha_expedicion=None, confianza={}, evidencia={}, vigente=False, purgado_en=timezone.now(),
        estado='FALLIDO', codigo_error='OCR_ARCHIVOS_PURGADOS', token_ejecucion=None, reservado_hasta=None)

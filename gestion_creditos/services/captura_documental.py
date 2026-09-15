import hashlib
import secrets
from datetime import timedelta
from functools import wraps
from io import BytesIO

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps

from gestion_creditos.models import (
    CapturaDocumentoIdentidad as Captura, SesionCapturaDocumental as Sesion,
    EventoCapturaDocumental as Evento, calcular_hash_archivo, validar_archivo_documento_empresa,
)


def _hash(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def _actor(actor):
    if not (actor and actor.is_authenticated and actor.is_active) or hasattr(actor, 'perfil_pagador'):
        raise PermissionDenied('La captura requiere la cuenta personal del solicitante.')


def _evento(sesion, actor, evento):
    Evento.objects.create(sesion=sesion, actor=actor if getattr(actor, 'is_authenticated', False) else None, evento=evento)


def _auditada(funcion):
    @wraps(funcion)
    def ejecutar(**kwargs):
        try:
            return funcion(**kwargs)
        except (ValidationError, PermissionDenied):
            actor = kwargs.get('actor')
            with transaction.atomic():
                sesion = None
                if getattr(actor, 'is_authenticated', False):
                    try:
                        sesion = Sesion.objects.select_for_update().filter(
                            pk=kwargs.get('sesion_id'), usuario=actor).first()
                    except (ValidationError, ValueError):
                        pass
                if (sesion and sesion.expira_en <= timezone.now()
                        and sesion.estado not in {Sesion.Estado.UTILIZADA, Sesion.Estado.REVOCADA, Sesion.Estado.EXPIRADA}):
                    sesion.estado = Sesion.Estado.EXPIRADA
                    sesion.save(update_fields=['estado', 'actualizado_en'])
                    _evento(sesion, actor, 'EXPIRACION')
                _evento(sesion, actor, 'RECHAZO')
            raise
    return ejecutar


def _contexto(actor, producto, solicitud_id):
    _actor(actor)
    if producto not in Sesion.Producto.values:
        raise ValidationError('Producto documental invalido.')
    if producto == Sesion.Producto.LIBRANZA and solicitud_id:
        raise ValidationError('Contexto documental invalido.')
    if solicitud_id:
        from contractors.models import ContractorApplication
        solicitud = ContractorApplication.objects.filter(pk=solicitud_id, usuario=actor).first()
        if not solicitud:
            raise PermissionDenied('Solicitud no disponible.')
        if solicitud.estado in {'PENDIENTE_FIRMA', 'FIRMADO', 'APROBADO_POR_PAGADOR', 'NO_APROBADO'}:
            raise ValidationError('La solicitud no admite nuevas capturas.')
        return solicitud
    return None


def _obtener(sesion_id, actor, producto, solicitud_id=None, *, lock=True):
    _contexto(actor, producto, solicitud_id)
    qs = Sesion.objects.select_for_update(of=('self',)) if lock else Sesion.objects
    try:
        sesion = qs.get(pk=sesion_id, usuario=actor, producto=producto, solicitud_id=solicitud_id, proposito='CEDULA')
    except (Sesion.DoesNotExist, ValidationError, ValueError, TypeError) as exc:
        raise PermissionDenied('Sesion documental no disponible.') from exc
    return sesion


def _vigente(sesion):
    if sesion.expira_en <= timezone.now() or sesion.estado in {Sesion.Estado.EXPIRADA, Sesion.Estado.REVOCADA}:
        raise ValidationError('La sesion documental expiro o fue revocada.')


def _vinculo(sesion, vinculo):
    if not vinculo or not secrets.compare_digest(sesion.vinculo_hash, _hash(vinculo)):
        raise PermissionDenied('Esta sesion del navegador no puede cargar documentos.')


@_auditada
@transaction.atomic
def crear_sesion(*, actor, producto, solicitud_id=None):
    solicitud = _contexto(actor, producto, solicitud_id)
    token = secrets.token_urlsafe(32)
    sesion = Sesion.objects.create(
        usuario=actor, producto=producto, solicitud=solicitud, token_hash=_hash(token),
        expira_en=timezone.now() + timedelta(seconds=int(getattr(settings, 'CAPTURA_DOCUMENTAL_TTL_SECONDS', 600))),
    )
    _evento(sesion, actor, 'SESION_CREADA')
    return sesion, token


@_auditada
@transaction.atomic
def canjear_sesion(*, sesion_id, actor, producto, token, vinculo, solicitud_id=None):
    sesion = _obtener(sesion_id, actor, producto, solicitud_id)
    _vigente(sesion)
    if sesion.estado != Sesion.Estado.ABIERTA or not secrets.compare_digest(sesion.token_hash, _hash(token)):
        raise ValidationError('Enlace invalido o ya utilizado.')
    if not vinculo:
        raise PermissionDenied('Se requiere una sesion autenticada.')
    sesion.estado = Sesion.Estado.CANJEADA
    sesion.canjeado_en = timezone.now()
    sesion.vinculo_hash = _hash(vinculo)
    sesion.token_hash = _hash(secrets.token_urlsafe(32))
    sesion.save()
    _evento(sesion, actor, 'CANJE')
    return sesion


@_auditada
@transaction.atomic
def regenerar_enlace(*, sesion_id, actor, producto, solicitud_id=None):
    sesion = _obtener(sesion_id, actor, producto, solicitud_id)
    _vigente(sesion)
    if sesion.estado not in {Sesion.Estado.ABIERTA, Sesion.Estado.CANJEADA}:
        raise ValidationError('Esta sesion no permite regeneracion.')
    token = secrets.token_urlsafe(32)
    sesion.token_hash = _hash(token)
    sesion.vinculo_hash = ''
    sesion.estado = Sesion.Estado.ABIERTA
    sesion.canjeado_en = None
    sesion.save()
    sesion.capturas.filter(activo=True).update(activo=False)
    _evento(sesion, actor, 'ENLACE_REGENERADO')
    return sesion, token


@_auditada
@transaction.atomic
def revocar_sesion(*, sesion_id, actor, producto, solicitud_id=None):
    sesion = _obtener(sesion_id, actor, producto, solicitud_id)
    if sesion.estado == Sesion.Estado.UTILIZADA:
        raise ValidationError('Los documentos ya estan vinculados a la solicitud.')
    if sesion.estado != Sesion.Estado.REVOCADA:
        sesion.estado = Sesion.Estado.REVOCADA
        sesion.revocado_en = timezone.now()
        sesion.vinculo_hash = ''
        sesion.save()
        _evento(sesion, actor, 'REVOCACION')
    return sesion


def _imagen_tecnica(archivo):
    if not archivo or not archivo.size or archivo.size > 8 * 1024 * 1024:
        raise ValidationError('La imagen debe tener contenido y no superar 8 MiB.')
    validar_archivo_documento_empresa(archivo)
    try:
        archivo.seek(0)
        with Image.open(archivo) as imagen:
            formato = imagen.format
            if formato not in {'JPEG', 'PNG', 'WEBP'} or imagen.width * imagen.height > 20000000:
                raise ValidationError('Formato o dimensiones no admitidos.')
            imagen.load()
            metadata = {'formato': formato, 'ancho': imagen.width, 'alto': imagen.height}
            salida = BytesIO()
            ImageOps.exif_transpose(imagen).convert('RGB').save(salida, format='JPEG', quality=95)
        limpio = ContentFile(salida.getvalue(), name='captura.jpg')
        if limpio.size > 8 * 1024 * 1024:
            raise ValidationError('La imagen procesada supera 8 MiB.')
        return limpio, metadata
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError('La imagen no es valida.') from exc
    finally:
        archivo.seek(0)


@_auditada
def recibir_captura(*, sesion_id, actor, producto, vinculo, lado, archivo, solicitud_id=None):
    guardado = None
    try:
        with transaction.atomic():
            sesion = _obtener(sesion_id, actor, producto, solicitud_id)
            _vigente(sesion)
            _vinculo(sesion, vinculo)
            if sesion.estado != Sesion.Estado.CANJEADA or lado not in Captura.Lado.values:
                raise ValidationError('La sesion o el lado no admiten carga.')
            limpio, metadata = _imagen_tecnica(archivo)
            digest = calcular_hash_archivo(limpio)
            anteriores = sesion.capturas.filter(activo=True, lado=lado)
            actual = anteriores.first()
            if actual and actual.hash_archivo == digest:
                return actual
            anteriores.update(activo=False)
            captura = Captura(sesion=sesion, actor=actor, lado=lado, hash_archivo=digest, metadata=metadata)
            captura.archivo.save('captura.jpg', limpio, save=False)
            guardado = captura.archivo
            captura.save()
            _evento(sesion, actor, 'REEMPLAZO' if actual else f'{lado}_RECIBIDO')
            return captura
    except Exception:
        if guardado:
            guardado.delete(save=False)
        raise


@_auditada
@transaction.atomic
def finalizar_sesion(*, sesion_id, actor, producto, vinculo, solicitud_id=None):
    sesion = _obtener(sesion_id, actor, producto, solicitud_id)
    _vigente(sesion)
    _vinculo(sesion, vinculo)
    if sesion.estado in {Sesion.Estado.FINALIZADA, Sesion.Estado.UTILIZADA}:
        return sesion
    if sesion.estado != Sesion.Estado.CANJEADA:
        raise ValidationError('La sesion no admite finalizacion.')
    lados = set(sesion.capturas.filter(activo=True, estado_tecnico='VALIDO_TECNICAMENTE',
                                      purgado_en__isnull=True).values_list('lado', flat=True))
    if lados != set(Captura.Lado.values):
        raise ValidationError('Se requieren frontal y trasera validos de esta sesion.')
    sesion.estado = Sesion.Estado.FINALIZADA
    sesion.finalizado_en = timezone.now()
    sesion.save()
    _evento(sesion, actor, 'FINALIZACION')
    return sesion


def obtener_documentos_finalizados(*, sesion_id, actor, producto, solicitud_id=None):
    """El llamador conserva el lock hasta crear/asociar la solicitud real."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('La integracion documental requiere transaction.atomic.')
    sesion = _obtener(sesion_id, actor, producto, solicitud_id)
    _vigente(sesion)
    if sesion.estado != Sesion.Estado.FINALIZADA:
        raise ValidationError('Completa la captura documental antes de continuar.')
    capturas = {c.lado: c for c in sesion.capturas.filter(activo=True, purgado_en__isnull=True,
                                                       estado_tecnico='VALIDO_TECNICAMENTE')}
    if set(capturas) != set(Captura.Lado.values):
        raise ValidationError('Faltan documentos aceptados.')
    return sesion, capturas


def consumir_documentos(*, sesion, actor, credito=None, solicitud=None):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Se requiere una transaccion de originacion.')
    sesion = _obtener(sesion.pk, actor, sesion.producto, sesion.solicitud_id)
    _vigente(sesion)
    if sesion.estado != Sesion.Estado.FINALIZADA:
        raise ValidationError('Sesion ya consumida o no finalizada.')
    if sesion.producto == Sesion.Producto.LIBRANZA:
        if not credito or credito.usuario_id != actor.pk or credito.linea != 'LIBRANZA' or solicitud:
            raise PermissionDenied('Credito incompatible con la captura.')
        sesion.credito = credito
    else:
        if not solicitud or solicitud.usuario_id != actor.pk or credito:
            raise PermissionDenied('Solicitud incompatible con la captura.')
        if sesion.solicitud_id and sesion.solicitud_id != solicitud.pk:
            raise PermissionDenied('No se pueden mezclar solicitudes.')
        sesion.solicitud = solicitud
    sesion.estado = Sesion.Estado.UTILIZADA
    sesion.utilizado_en = timezone.now()
    sesion.save()
    _evento(sesion, actor, 'VINCULACION')


def archivos_para_formulario(capturas, campos):
    archivos = {}
    for lado, campo in campos.items():
        with capturas[lado].archivo.open('rb') as archivo:
            contenido = ContentFile(archivo.read(), name=f'{lado.lower()}.jpg')
        contenido.content_type = 'image/jpeg'
        archivos[campo] = contenido
    return archivos


def preparar_identidad_formulario(request, *, producto, solicitud=None):
    campos = ({'FRONTAL': 'cedula_frontal', 'TRASERA': 'cedula_trasera'} if producto == 'LIBRANZA'
              else {'FRONTAL': 'documento_identidad_frontal', 'TRASERA': 'documento_identidad_reverso'})
    if any(request.FILES.get(campo) for campo in campos.values()):
        raise ValidationError('La cedula debe proceder de una sesion documental finalizada.')
    sesion_id = request.POST.get('sesion_documental_id')
    if not sesion_id and solicitud and set(solicitud.documentos.values_list('tipo_documento', flat=True)).issuperset(
            {'CEDULA_FRONTAL', 'CEDULA_TRASERA'}):
        return None, request.FILES
    sesion, capturas = obtener_documentos_finalizados(
        sesion_id=sesion_id, actor=request.user, producto=producto, solicitud_id=solicitud.pk if solicitud else None)
    archivos = request.FILES.copy()
    archivos.update(archivos_para_formulario(capturas, campos))
    return sesion, archivos


def estado_publico(*, sesion_id, actor, producto, solicitud_id=None):
    sesion = _obtener(sesion_id, actor, producto, solicitud_id, lock=False)
    estado = sesion.estado
    if estado not in {Sesion.Estado.UTILIZADA, Sesion.Estado.REVOCADA} and sesion.expira_en <= timezone.now():
        estado = Sesion.Estado.EXPIRADA
    return {'id': str(sesion.pk), 'estado': estado, 'identidad': 'IDENTIDAD_NO_VERIFICADA',
            'lados': list(sesion.capturas.filter(activo=True, purgado_en__isnull=True).values_list('lado', flat=True))}


def purgar_sesiones_expiradas():
    """Solo borradores no vinculados. Retiene filas y eventos; nunca toca legacy."""
    ahora = timezone.now()
    total = 0
    ids = Sesion.objects.filter(expira_en__lte=ahora).exclude(estado=Sesion.Estado.UTILIZADA).values_list('pk', flat=True)
    for pk in ids.iterator():
        with transaction.atomic():
            sesion = Sesion.objects.select_for_update().get(pk=pk)
            if sesion.estado == Sesion.Estado.UTILIZADA:
                continue
            if sesion.estado not in {Sesion.Estado.EXPIRADA, Sesion.Estado.REVOCADA}:
                sesion.estado = Sesion.Estado.EXPIRADA
                sesion.save()
                _evento(sesion, None, 'EXPIRACION')
            purgadas = 0
            for captura in sesion.capturas.filter(purgado_en__isnull=True):
                captura.archivo.delete(save=False)
                captura.activo = False
                captura.purgado_en = ahora
                captura.save(update_fields=['activo', 'purgado_en'])
                total += 1
                purgadas += 1
            if purgadas:
                _evento(sesion, None, 'PURGA_TEMPORALES')
    return total

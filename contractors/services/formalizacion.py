import hashlib
import hmac
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    FormalizacionCreditoPrestador,
    TimelinePrestador,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from contractors.services.aprobacion_pagador import validar_aprobacion_pagador_vigente
from contractors.services.validacion_contractual import validar_contrato_prestador
from gestion_creditos.models import (
    Credito,
    HistorialEstado,
    OrigenCreditoPrestador,
    Pagare,
)


@dataclass(frozen=True)
class ResultadoFormalizacionPrestador:
    formalizacion: FormalizacionCreditoPrestador
    reutilizada: bool


class IdentidadFirmaExpirada(ValidationError):
    pass


def construir_clave_formalizacion_prestador(origen):
    return f'prestador:{origen.credito_id}:formalizacion:{origen.gate_version}'


def preparar_formalizacion_credito_prestador(origen, *, actor):
    _exigir_permiso(actor, 'contractors.can_prepare_contractor_formalization')
    formalizacion = None
    reutilizada = False
    try:
        with transaction.atomic():
            origen = (
                OrigenCreditoPrestador.objects.select_for_update(of=('self',))
                .select_related('credito', 'credito_libranza')
                .get(pk=origen.pk)
            )
            _validar_origen_formalizable(origen)
            clave = construir_clave_formalizacion_prestador(origen)
            formalizacion, creada = FormalizacionCreditoPrestador.objects.get_or_create(
                origen_credito_prestador=origen,
                defaults={
                    'credito': origen.credito,
                    'credito_libranza': origen.credito_libranza,
                    'clave_idempotencia': clave,
                    'version_origen': origen.gate_version,
                    'estado': FormalizacionCreditoPrestador.Estado.PREPARANDO_DOCUMENTO,
                    'created_by': actor,
                },
            )
            if not creada:
                _validar_coincidencia_formalizacion(formalizacion, origen, clave)
                if formalizacion.pagare_id:
                    reutilizada = True
                elif formalizacion.estado in {
                    FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
                    FormalizacionCreditoPrestador.Estado.PENDIENTE,
                }:
                    formalizacion.estado = (
                        FormalizacionCreditoPrestador.Estado.PREPARANDO_DOCUMENTO
                    )
                    formalizacion.error_codigo = ''
                    formalizacion.error_etapa = ''
                    formalizacion.save(update_fields=[
                        'estado', 'error_codigo', 'error_etapa', 'updated_at'
                    ])
                else:
                    reutilizada = True

        if reutilizada:
            _registrar_evento(
                formalizacion,
                TimelinePrestador.TipoEvento.FORMALIZACION_REUTILIZADA,
                actor,
            )
            return ResultadoFormalizacionPrestador(formalizacion, True)

        _registrar_evento(
            formalizacion,
            TimelinePrestador.TipoEvento.FORMALIZACION_INICIADA,
            actor,
        )
        from gestion_creditos.services.pagare_service import generar_pagare_prestador_pdf

        with transaction.atomic():
            pagare = generar_pagare_prestador_pdf(
                formalizacion,
                usuario_creador=actor,
            )
            formalizacion = FormalizacionCreditoPrestador.objects.select_for_update().get(
                pk=formalizacion.pk
            )
            formalizacion.pagare = pagare
            formalizacion.estado = (
                FormalizacionCreditoPrestador.Estado.PENDIENTE_VALIDACION_IDENTIDAD
            )
            formalizacion.error_codigo = ''
            formalizacion.error_etapa = ''
            formalizacion.save(update_fields=[
                'pagare', 'estado', 'error_codigo', 'error_etapa', 'updated_at'
            ])
        _registrar_evento(
            formalizacion,
            TimelinePrestador.TipoEvento.PAGARE_GENERADO,
            actor,
        )
        return ResultadoFormalizacionPrestador(formalizacion, False)
    except Exception as exc:
        if formalizacion is not None:
            _marcar_error_controlado(formalizacion, 'PREPARACION_DOCUMENTO', exc)
        raise


def registrar_resultado_validacion_identidad_prestador(
    formalizacion,
    *,
    usuario,
    referencia_proveedor,
    expira_en,
):
    """Registra una validacion ya confirmada por un proveedor; no valida OTP."""
    if usuario is None or not usuario.is_authenticated:
        raise PermissionDenied('Debes iniciar sesion para validar la identidad.')
    if not referencia_proveedor or expira_en is None:
        raise ValidationError('La referencia y su vigencia son obligatorias.')
    if expira_en <= timezone.now():
        raise ValidationError('La validacion de identidad ya esta expirada.')

    with transaction.atomic():
        formalizacion = (
            FormalizacionCreditoPrestador.objects.select_for_update(of=('self',))
            .select_related('credito')
            .get(pk=formalizacion.pk)
        )
        if formalizacion.credito.usuario_id != usuario.id:
            raise PermissionDenied('La validacion no pertenece al titular del credito.')
        if formalizacion.estado not in {
            FormalizacionCreditoPrestador.Estado.PENDIENTE_VALIDACION_IDENTIDAD,
            FormalizacionCreditoPrestador.Estado.IDENTIDAD_VALIDADA,
            FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
        }:
            raise ValidationError('La formalizacion no admite validacion de identidad.')
        referencia_hash = _hash_secreto(
            'identidad',
            f'{formalizacion.id}:{usuario.id}:{referencia_proveedor}',
        )
        if (
            formalizacion.identidad_referencia_hash
            and formalizacion.identidad_referencia_hash != referencia_hash
        ):
            raise ValidationError('La formalizacion ya tiene otra validacion de identidad.')
        formalizacion.identidad_usuario = usuario
        formalizacion.identidad_referencia_hash = referencia_hash
        formalizacion.identidad_validada_en = timezone.now()
        formalizacion.identidad_expira_en = expira_en
        formalizacion.identidad_selfie_validada = True
        formalizacion.identidad_documento_validada = True
        formalizacion.identidad_firmante_coincide = True
        formalizacion.identidad_evidencia_hash = _hash_secreto(
            'evidencia-identidad',
            f'{formalizacion.id}:{usuario.id}:{referencia_proveedor}',
        )
        formalizacion.estado_identidad = (
            FormalizacionCreditoPrestador.EstadoIdentidad.VALIDADA
        )
        formalizacion.estado = FormalizacionCreditoPrestador.Estado.IDENTIDAD_VALIDADA
        formalizacion.error_codigo = ''
        formalizacion.error_etapa = ''
        formalizacion.save(update_fields=[
            'identidad_usuario', 'identidad_referencia_hash',
            'identidad_validada_en', 'identidad_expira_en', 'estado_identidad',
            'identidad_selfie_validada', 'identidad_documento_validada',
            'identidad_firmante_coincide', 'identidad_evidencia_hash',
            'estado', 'error_codigo', 'error_etapa', 'updated_at',
        ])
    _registrar_evento(
        formalizacion,
        TimelinePrestador.TipoEvento.IDENTIDAD_VALIDADA_FIRMA,
        usuario,
    )
    return formalizacion


def enviar_formalizacion_prestador_a_firma(formalizacion, *, actor, cliente=None):
    _exigir_permiso(actor, 'contractors.can_retry_contractor_signature')
    error_identidad = None
    with transaction.atomic():
        formalizacion = (
            FormalizacionCreditoPrestador.objects.select_for_update(of=('self',))
            .select_related('credito', 'credito_libranza', 'pagare')
            .get(pk=formalizacion.pk)
        )
        if formalizacion.estado == FormalizacionCreditoPrestador.Estado.FIRMADO:
            return ResultadoFormalizacionPrestador(formalizacion, True)
        if formalizacion.proveedor_document_id_hash:
            _reconciliar_envio_confirmado(formalizacion, actor=actor)
            return ResultadoFormalizacionPrestador(formalizacion, True)
        if formalizacion.estado == FormalizacionCreditoPrestador.Estado.ENVIANDO_A_FIRMA:
            raise ValidationError(
                'Existe un envio con resultado remoto pendiente de conciliacion. '
                'No se realizara un segundo envio automatico.'
            )
        try:
            _validar_identidad_vigente(formalizacion)
        except IdentidadFirmaExpirada as exc:
            formalizacion.estado_identidad = (
                FormalizacionCreditoPrestador.EstadoIdentidad.EXPIRADA
            )
            formalizacion.estado = (
                FormalizacionCreditoPrestador.Estado.PENDIENTE_VALIDACION_IDENTIDAD
            )
            formalizacion.save(update_fields=[
                'estado_identidad', 'estado', 'updated_at'
            ])
            error_identidad = exc
        if error_identidad is None:
            if not formalizacion.pagare_id or not formalizacion.pagare.archivo_pdf:
                raise ValidationError('La formalizacion no tiene un pagare generado.')
            if formalizacion.credito.estado != Credito.EstadoCredito.EN_REVISION:
                raise ValidationError('El credito no esta en revision para iniciar firma.')
            formalizacion.estado = FormalizacionCreditoPrestador.Estado.ENVIANDO_A_FIRMA
            formalizacion.intentos_firma += 1
            formalizacion.error_codigo = ''
            formalizacion.error_etapa = ''
            formalizacion.save(update_fields=[
                'estado', 'intentos_firma', 'error_codigo', 'error_etapa', 'updated_at'
            ])

    if error_identidad is not None:
        raise error_identidad

    _registrar_evento(
        formalizacion,
        TimelinePrestador.TipoEvento.ENVIO_FIRMA_INICIADO,
        actor,
    )
    try:
        from gestion_creditos.services.pagare_url import generar_url_publica_temporal
        from gestion_creditos.services.zapsign_client import ZapSignClient

        cliente = cliente or ZapSignClient()
        detalle = formalizacion.credito_libranza
        respuesta = cliente.crear_documento(
            nombre=f'Pagare {formalizacion.credito.numero_credito}',
            url_pdf=generar_url_publica_temporal(formalizacion.pagare),
            email_firmante=detalle.correo_electronico,
            nombre_firmante=detalle.nombre_completo,
            brand_name='Aprobado',
            external_id=formalizacion.clave_idempotencia,
            require_identity_validation=True,
            require_document_validation=True,
        )
        documento_id = str(respuesta.get('token') or '').strip()
        if not documento_id:
            raise ValidationError('El proveedor no retorno identificador del documento.')
        documento_hash = hash_documento_proveedor(documento_id)

        try:
            with transaction.atomic():
                formalizacion = (
                    FormalizacionCreditoPrestador.objects.select_for_update()
                    .get(pk=formalizacion.pk)
                )
                formalizacion.proveedor_document_id_hash = documento_hash
                _reconciliar_envio_confirmado(formalizacion, actor=actor)
        except Exception:
            # El proveedor ya devolvio un identificador. Se conserva solo su hash para
            # que un reintento concilie el envio sin crear otro documento remoto.
            FormalizacionCreditoPrestador.objects.filter(pk=formalizacion.pk).update(
                proveedor_document_id_hash=documento_hash,
                estado=FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
                error_codigo='PERSISTENCIA_ENVIO',
                error_etapa='PERSISTENCIA_DOCUMENTO_REMOTO',
                updated_at=timezone.now(),
            )
            raise
        _registrar_evento(
            formalizacion,
            TimelinePrestador.TipoEvento.PENDIENTE_FIRMA,
            actor,
        )
        return ResultadoFormalizacionPrestador(formalizacion, False)
    except Exception as exc:
        identificador_confirmado = FormalizacionCreditoPrestador.objects.filter(
            pk=formalizacion.pk,
            proveedor_document_id_hash__isnull=False,
        ).exists()
        if not identificador_confirmado:
            _marcar_error_controlado(formalizacion, 'ENVIO_ZAPSIGN', exc)
        raise


def es_documento_formalizacion_prestador(documento_id):
    if not documento_id:
        return False
    return FormalizacionCreditoPrestador.objects.filter(
        proveedor_document_id_hash=hash_documento_proveedor(documento_id)
    ).exists()


def procesar_callback_formalizacion_prestador(
    *, documento_id, accion, estado_proveedor='', actor=None
):
    documento_hash = hash_documento_proveedor(documento_id)
    with transaction.atomic():
        formalizacion = (
            FormalizacionCreditoPrestador.objects.select_for_update(of=('self',))
            .select_related('pagare')
            .filter(proveedor_document_id_hash=documento_hash)
            .first()
        )
        if formalizacion is None:
            return None
        credito = Credito.objects.select_for_update().get(
            pk=formalizacion.credito_id
        )
        if credito.estado == Credito.EstadoCredito.ANULADO:
            return {
                'estado': 'credit_cancelled_ignored',
                'formalizacion': formalizacion,
            }
        if accion == 'signed':
            if formalizacion.estado == FormalizacionCreditoPrestador.Estado.FIRMADO:
                return {'estado': 'already_processed', 'formalizacion': formalizacion}
            if formalizacion.estado != FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA:
                raise ValidationError('La formalizacion no esta pendiente de firma.')
            _validar_evidencia_identidad_completa(formalizacion)
            ahora = timezone.now()
            formalizacion.estado = FormalizacionCreditoPrestador.Estado.FIRMADO
            formalizacion.firmada_en = ahora
            formalizacion.save(update_fields=['estado', 'firmada_en', 'updated_at'])
            pagare = Pagare.objects.select_for_update().get(pk=formalizacion.pagare_id)
            pagare.estado = Pagare.EstadoPagare.SIGNED
            pagare.fecha_firma = ahora
            pagare.zapsign_status = str(estado_proveedor or '')[:20]
            pagare.evidencias = {
                'formalizacion_id': formalizacion.id,
                'evento': 'firma_confirmada',
                'estado_proveedor': str(estado_proveedor or '')[:40],
            }
            pagare.save(update_fields=[
                'estado', 'fecha_firma', 'zapsign_status', 'evidencias'
            ])
            _cambiar_estado_credito_sin_efectos_financieros(
                formalizacion.credito_id,
                Credito.EstadoCredito.FIRMADO,
                actor=actor,
                motivo='Firma de prestador confirmada por el proveedor.',
            )
            evento = TimelinePrestador.TipoEvento.FIRMA_CONFIRMADA
            resultado = 'ok'
        elif accion == 'refused':
            formalizacion.estado = FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO
            formalizacion.error_codigo = 'FIRMA_RECHAZADA'
            formalizacion.error_etapa = 'FIRMA'
            formalizacion.save(update_fields=[
                'estado', 'error_codigo', 'error_etapa', 'updated_at'
            ])
            pagare = Pagare.objects.select_for_update().get(pk=formalizacion.pagare_id)
            pagare.estado = Pagare.EstadoPagare.REFUSED
            pagare.fecha_rechazo = timezone.now()
            pagare.zapsign_status = str(estado_proveedor or '')[:20]
            pagare.save(update_fields=['estado', 'fecha_rechazo', 'zapsign_status'])
            evento = TimelinePrestador.TipoEvento.FIRMA_ERROR_CONTROLADO
            resultado = 'refused_recorded'
        else:
            return {'estado': 'event_ignored', 'formalizacion': formalizacion}

    _registrar_evento(formalizacion, evento, actor)
    return {'estado': resultado, 'formalizacion': formalizacion}


def hash_documento_proveedor(documento_id):
    return hashlib.sha256(str(documento_id).encode('utf-8')).hexdigest()


def _hash_secreto(namespace, valor):
    clave = str(settings.SECRET_KEY).encode('utf-8')
    mensaje = f'{namespace}:{valor}'.encode('utf-8')
    return hmac.new(clave, mensaje, hashlib.sha256).hexdigest()


def _validar_origen_formalizable(origen):
    if origen.estado != OrigenCreditoPrestador.Estado.COMPLETADO:
        raise ValidationError('El credito no tiene una originacion completada.')
    if not origen.credito_id or not origen.credito_libranza_id:
        raise ValidationError('La originacion no tiene enlaces financieros completos.')
    if origen.credito.estado != Credito.EstadoCredito.EN_REVISION:
        raise ValidationError('Solo un credito EN_REVISION puede formalizarse.')
    gate = AprobacionInternaPrestador.objects.filter(pk=origen.gate_id).first()
    if gate is None or gate.estado != AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
        raise ValidationError('La aprobacion interna no habilita la formalizacion.')
    if gate.version_datos != origen.gate_version:
        raise ValidationError('La version aprobada no coincide con la originacion.')
    validar_aprobacion_pagador_vigente(gate)
    contrato = validar_contrato_prestador(gate.solicitud)
    if (
        contrato.bloqueos
        or contrato.requiere_revision_manual
        or not contrato.forma_pago_mensual
        or not contrato.capacidad_automatica
    ):
        raise ValidationError('El contrato ya no habilita la formalizacion.')


def _validar_coincidencia_formalizacion(formalizacion, origen, clave):
    if formalizacion.clave_idempotencia != clave:
        raise ValidationError('La formalizacion existente pertenece a otra version.')
    if formalizacion.credito_id != origen.credito_id:
        raise ValidationError('La formalizacion existente pertenece a otro credito.')
    if formalizacion.version_origen != origen.gate_version:
        raise ValidationError('La version de formalizacion no coincide con el origen.')


def _validar_identidad_vigente(formalizacion):
    if formalizacion.estado_identidad != FormalizacionCreditoPrestador.EstadoIdentidad.VALIDADA:
        raise ValidationError('Debes validar la identidad antes de enviar a firma.')
    if formalizacion.identidad_usuario_id != formalizacion.credito.usuario_id:
        raise ValidationError('La identidad validada no pertenece al titular del credito.')
    if not formalizacion.identidad_expira_en or formalizacion.identidad_expira_en <= timezone.now():
        raise IdentidadFirmaExpirada('La validacion de identidad esta expirada.')
    _validar_evidencia_identidad_completa(formalizacion)


def _validar_evidencia_identidad_completa(formalizacion):
    if not all((
        formalizacion.identidad_selfie_validada,
        formalizacion.identidad_documento_validada,
        formalizacion.identidad_firmante_coincide,
        formalizacion.identidad_evidencia_hash,
    )):
        raise ValidationError(
            'La firma no tiene evidencia completa de selfie, documento y firmante.'
        )


def _cambiar_estado_credito_sin_efectos_financieros(
    credito_id, nuevo_estado, *, actor, motivo
):
    credito = Credito.objects.select_for_update().get(pk=credito_id)
    estado_anterior = credito.estado
    if estado_anterior == nuevo_estado:
        return credito
    credito.estado = nuevo_estado
    credito.save(update_fields=['estado', 'fecha_actualizacion'])
    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        usuario_modificacion=actor if getattr(actor, 'is_authenticated', False) else None,
        motivo=motivo,
    )
    return credito


def _reconciliar_envio_confirmado(formalizacion, *, actor):
    """Completa localmente un envio identificado sin repetir la llamada externa."""
    ahora = formalizacion.enviada_firma_en or timezone.now()
    formalizacion.estado = FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA
    formalizacion.enviada_firma_en = ahora
    formalizacion.error_codigo = ''
    formalizacion.error_etapa = ''
    formalizacion.save(update_fields=[
        'proveedor_document_id_hash', 'estado', 'enviada_firma_en',
        'error_codigo', 'error_etapa', 'updated_at',
    ])
    pagare = Pagare.objects.select_for_update().get(pk=formalizacion.pagare_id)
    pagare.estado = Pagare.EstadoPagare.SENT
    pagare.fecha_envio = ahora
    pagare.zapsign_status = 'pending'
    pagare.save(update_fields=['estado', 'fecha_envio', 'zapsign_status'])
    _cambiar_estado_credito_sin_efectos_financieros(
        formalizacion.credito_id,
        Credito.EstadoCredito.PENDIENTE_FIRMA,
        actor=actor,
        motivo='Documento de prestador enviado a firma.',
    )


def _marcar_error_controlado(formalizacion, etapa, exc):
    with transaction.atomic():
        formalizacion = FormalizacionCreditoPrestador.objects.select_for_update().get(
            pk=formalizacion.pk
        )
        formalizacion.estado = FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO
        formalizacion.error_codigo = type(exc).__name__[:80]
        formalizacion.error_etapa = str(etapa)[:80]
        formalizacion.save(update_fields=[
            'estado', 'error_codigo', 'error_etapa', 'updated_at'
        ])
    _registrar_evento(
        formalizacion,
        TimelinePrestador.TipoEvento.FIRMA_ERROR_CONTROLADO,
        None,
    )


def _registrar_evento(formalizacion, tipo_evento, actor):
    gate = AprobacionInternaPrestador.objects.select_related('solicitud').get(
        pk=formalizacion.origen_credito_prestador.gate_id
    )
    return registrar_evento_timeline_prestador(
        solicitud=gate.solicitud,
        tipo_evento=tipo_evento,
        titulo=TimelinePrestador.TipoEvento(tipo_evento).label,
        descripcion='Evento operativo controlado de formalizacion.',
        metadata={
            'formalizacion_id': formalizacion.id,
            'credito_id': formalizacion.credito_id,
            'pagare_id': formalizacion.pagare_id,
            'actor_id': getattr(actor, 'id', None),
            'estado': formalizacion.estado,
            'proveedor': formalizacion.proveedor_firma,
        },
        visible_cliente=False,
        usuario=actor,
    )


def _exigir_permiso(actor, permiso):
    if (
        actor is None
        or not actor.is_authenticated
        or not actor.is_staff
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm(permiso)
    ):
        raise PermissionDenied('No tienes permiso para operar esta formalizacion.')

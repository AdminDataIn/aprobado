import hashlib
import hmac
from dataclasses import asdict, dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    FormalizacionCreditoPrestador,
    NovedadOperativaPrestador,
    TimelinePrestador,
)
from contractors.services.evaluacion_timeline import registrar_evento_timeline_prestador
from gestion_creditos.models import Credito, OrigenCreditoPrestador
from usuarios.models import PerfilPagador


@dataclass(frozen=True)
class NovedadOperativaPrestadorDTO:
    credito_id: int
    numero_credito: str
    empresa_id: int
    empresa_nombre: str
    titular_nombre: str
    documento_enmascarado: str
    monto_formalizado: Decimal
    plazo_meses: int
    fecha_firma: object
    fecha_inicio_contrato: object
    fecha_fin_contrato: object
    tipo_novedad: str

    def como_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ResultadoNovedadOperativaPrestador:
    novedad: NovedadOperativaPrestador
    reutilizada: bool


@dataclass(frozen=True)
class ResultadoEnvioNovedadOperativaPrestador:
    novedad: NovedadOperativaPrestador
    reutilizado: bool
    destinatarios: int


def construir_clave_novedad_operativa_prestador(formalizacion):
    return (
        f'prestador:{formalizacion.credito_id}:novedad-operativa:'
        f'{formalizacion.id}'
    )


def construir_dto_novedad_operativa_prestador(novedad):
    detalle = novedad.credito_libranza
    return NovedadOperativaPrestadorDTO(
        credito_id=novedad.credito_id,
        numero_credito=novedad.credito.numero_credito,
        empresa_id=novedad.empresa_id,
        empresa_nombre=novedad.empresa.nombre,
        titular_nombre=detalle.nombre_completo,
        documento_enmascarado=_enmascarar_documento(detalle.cedula),
        monto_formalizado=Decimal(str(novedad.credito.monto_aprobado)),
        plazo_meses=int(novedad.credito.plazo),
        fecha_firma=novedad.formalizacion.firmada_en,
        fecha_inicio_contrato=detalle.fecha_inicio_contrato,
        fecha_fin_contrato=detalle.fecha_fin_contrato,
        tipo_novedad=novedad.tipo_novedad,
    )


def crear_o_reutilizar_novedad_operativa_prestador(formalizacion, *, actor):
    _exigir_staff(
        actor,
        'contractors.can_create_contractor_operational_notice',
    )
    with transaction.atomic():
        formalizacion = (
            FormalizacionCreditoPrestador.objects
            .select_for_update(of=('self',))
            .select_related(
                'origen_credito_prestador',
                'credito',
                'credito_libranza__empresa',
            )
            .get(pk=formalizacion.pk)
        )
        gate = _validar_formalizacion_para_novedad(formalizacion)
        clave = construir_clave_novedad_operativa_prestador(formalizacion)
        novedad, creada = NovedadOperativaPrestador.objects.get_or_create(
            formalizacion=formalizacion,
            defaults={
                'credito': formalizacion.credito,
                'credito_libranza': formalizacion.credito_libranza,
                'empresa': formalizacion.credito_libranza.empresa,
                'clave_idempotencia': clave,
            },
        )
        if not creada:
            _validar_coincidencia_novedad(novedad, formalizacion, clave)

    if creada:
        _registrar_evento(
            novedad,
            TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_GENERADA,
            actor,
            solicitud=gate.solicitud,
        )
    return ResultadoNovedadOperativaPrestador(novedad=novedad, reutilizada=not creada)


def obtener_destinatarios_novedad_operativa_prestador(empresa):
    perfiles = (
        PerfilPagador.objects
        .select_related('usuario')
        .filter(
            empresa=empresa,
            es_pagador=True,
            usuario__is_active=True,
        )
        .exclude(usuario__email='')
        .order_by('usuario_id')
    )
    destinatarios = []
    vistos = set()
    for perfil in perfiles:
        email = str(perfil.usuario.email or '').strip()
        clave = email.lower()
        if email and clave not in vistos:
            vistos.add(clave)
            destinatarios.append(email)
    return destinatarios


def enviar_novedad_operativa_prestador(novedad, *, actor, cliente=None):
    novedad_actual = NovedadOperativaPrestador.objects.get(pk=novedad.pk)
    es_reintento = (
        novedad_actual.estado == NovedadOperativaPrestador.Estado.ERROR_CONTROLADO
        or novedad_actual.intentos_envio > 0
    )
    _exigir_staff(
        actor,
        (
            'contractors.can_retry_contractor_operational_notice'
            if es_reintento
            else 'contractors.can_create_contractor_operational_notice'
        ),
    )
    if novedad_actual.estado in {
        NovedadOperativaPrestador.Estado.ENVIADA,
        NovedadOperativaPrestador.Estado.RECIBIDA,
        NovedadOperativaPrestador.Estado.GESTIONADA,
    }:
        return ResultadoEnvioNovedadOperativaPrestador(
            novedad=novedad_actual,
            reutilizado=True,
            destinatarios=len(novedad_actual.destinatarios_hash),
        )
    destinatarios = obtener_destinatarios_novedad_operativa_prestador(
        novedad_actual.empresa
    )
    if not destinatarios:
        _marcar_error_controlado(
            novedad_actual,
            codigo='SIN_DESTINATARIOS',
            etapa='RESOLUCION_DESTINATARIOS',
            actor=actor,
        )
        raise ValidationError(
            'La empresa no tiene pagadores activos con correo configurado.'
        )

    with transaction.atomic():
        novedad = (
            NovedadOperativaPrestador.objects
            .select_for_update(of=('self',))
            .select_related(
                'formalizacion', 'credito', 'credito_libranza', 'empresa'
            )
            .get(pk=novedad.pk)
        )
        _validar_novedad_para_envio(novedad)
        if novedad.estado in {
            NovedadOperativaPrestador.Estado.ENVIADA,
            NovedadOperativaPrestador.Estado.RECIBIDA,
            NovedadOperativaPrestador.Estado.GESTIONADA,
        }:
            return ResultadoEnvioNovedadOperativaPrestador(
                novedad=novedad,
                reutilizado=True,
                destinatarios=len(destinatarios),
            )
        if novedad.estado == NovedadOperativaPrestador.Estado.ENVIANDO:
            raise ValidationError(
                'Existe un envio en proceso. No se realizara un envio duplicado.'
            )
        if novedad.estado not in {
            NovedadOperativaPrestador.Estado.PENDIENTE_ENVIO,
            NovedadOperativaPrestador.Estado.ERROR_CONTROLADO,
        }:
            raise ValidationError('La novedad no admite envio en su estado actual.')
        reenvio = novedad.intentos_envio > 0
        novedad.estado = NovedadOperativaPrestador.Estado.ENVIANDO
        novedad.intentos_envio += 1
        novedad.error_codigo = ''
        novedad.error_etapa = ''
        novedad.save(update_fields=[
            'estado', 'intentos_envio', 'error_codigo', 'error_etapa', 'updated_at'
        ])

    _registrar_evento(
        novedad,
        (
            TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_REENVIO
            if reenvio
            else TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_ENVIO_INICIADO
        ),
        actor,
    )
    dto = construir_dto_novedad_operativa_prestador(novedad)
    try:
        cliente = cliente or _enviar_email_novedad_operativa
        resultado_envio = cliente(dto=dto, destinatarios=destinatarios)
        if resultado_envio is False:
            raise RuntimeError('El canal de correo no confirmo el envio.')
    except Exception:
        _marcar_error_controlado(
            novedad,
            codigo='ERROR_ENVIO_EMAIL',
            etapa='ENVIO_EMAIL',
            actor=actor,
        )
        raise

    with transaction.atomic():
        novedad = NovedadOperativaPrestador.objects.select_for_update().get(
            pk=novedad.pk
        )
        if novedad.estado != NovedadOperativaPrestador.Estado.ENVIANDO:
            raise ValidationError('El estado local del envio cambio durante la operacion.')
        novedad.estado = NovedadOperativaPrestador.Estado.ENVIADA
        novedad.enviada_por = actor
        novedad.enviada_en = timezone.now()
        novedad.destinatarios_hash = [_hash_email(item) for item in destinatarios]
        novedad.destinatarios_enmascarados = [
            _enmascarar_email(item) for item in destinatarios
        ]
        novedad.save(update_fields=[
            'estado', 'enviada_por', 'enviada_en', 'destinatarios_hash',
            'destinatarios_enmascarados', 'updated_at',
        ])
    _registrar_evento(
        novedad,
        TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_ENVIADA,
        actor,
    )
    return ResultadoEnvioNovedadOperativaPrestador(
        novedad=novedad,
        reutilizado=False,
        destinatarios=len(destinatarios),
    )


def confirmar_recepcion_novedad_operativa_prestador(novedad, *, actor):
    perfil = exigir_pagador_operativo(actor)
    with transaction.atomic():
        novedad = NovedadOperativaPrestador.objects.select_for_update().get(
            pk=novedad.pk
        )
        _validar_empresa_pagador(novedad, perfil)
        if novedad.estado in {
            NovedadOperativaPrestador.Estado.RECIBIDA,
            NovedadOperativaPrestador.Estado.GESTIONADA,
        }:
            return ResultadoNovedadOperativaPrestador(novedad, True)
        if novedad.estado != NovedadOperativaPrestador.Estado.ENVIADA:
            raise ValidationError('La novedad aun no esta disponible para recepcion.')
        novedad.estado = NovedadOperativaPrestador.Estado.RECIBIDA
        novedad.recibida_por = actor
        novedad.recibida_en = timezone.now()
        novedad.save(update_fields=[
            'estado', 'recibida_por', 'recibida_en', 'updated_at'
        ])
    _registrar_evento(
        novedad,
        TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_RECIBIDA,
        actor,
    )
    return ResultadoNovedadOperativaPrestador(novedad, False)


def marcar_novedad_operativa_prestador_gestionada(novedad, *, actor):
    perfil = exigir_pagador_operativo(actor)
    with transaction.atomic():
        novedad = NovedadOperativaPrestador.objects.select_for_update().get(
            pk=novedad.pk
        )
        _validar_empresa_pagador(novedad, perfil)
        if novedad.estado == NovedadOperativaPrestador.Estado.GESTIONADA:
            return ResultadoNovedadOperativaPrestador(novedad, True)
        if novedad.estado != NovedadOperativaPrestador.Estado.RECIBIDA:
            raise ValidationError(
                'La recepcion debe confirmarse antes de marcar la novedad como gestionada.'
            )
        novedad.estado = NovedadOperativaPrestador.Estado.GESTIONADA
        novedad.gestionada_por = actor
        novedad.gestionada_en = timezone.now()
        novedad.save(update_fields=[
            'estado', 'gestionada_por', 'gestionada_en', 'updated_at'
        ])
    _registrar_evento(
        novedad,
        TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_GESTIONADA,
        actor,
    )
    return ResultadoNovedadOperativaPrestador(novedad, False)


def exigir_pagador_operativo(
    actor,
    permiso='contractors.can_acknowledge_contractor_operational_notice',
):
    if actor is None or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Debes iniciar sesion como pagador activo.')
    try:
        perfil = actor.perfil_pagador
    except PerfilPagador.DoesNotExist as exc:
        raise PermissionDenied('El usuario no tiene perfil de pagador.') from exc
    if not perfil.es_pagador:
        raise PermissionDenied('El perfil de pagador no esta activo.')
    if not actor.has_perm(permiso):
        raise PermissionDenied('No tienes permiso para gestionar novedades operativas.')
    return perfil


def _validar_formalizacion_para_novedad(formalizacion):
    origen = formalizacion.origen_credito_prestador
    credito = formalizacion.credito
    detalle = formalizacion.credito_libranza
    if formalizacion.estado != FormalizacionCreditoPrestador.Estado.FIRMADO:
        raise ValidationError('La formalizacion debe estar firmada.')
    if credito.estado != Credito.EstadoCredito.FIRMADO:
        raise ValidationError('El credito debe estar firmado.')
    if origen.estado != OrigenCreditoPrestador.Estado.COMPLETADO:
        raise ValidationError('La originacion del prestador no esta completa.')
    if origen.credito_id != credito.id or origen.credito_libranza_id != detalle.id:
        raise ValidationError('La formalizacion tiene enlaces financieros inconsistentes.')
    if not detalle.es_prestador_servicios:
        raise ValidationError('El credito no corresponde a un prestador de servicios.')
    if not detalle.empresa.permite_libranza:
        raise ValidationError('La empresa no tiene un convenio de libranza vigente.')
    gate = AprobacionInternaPrestador.objects.select_related('solicitud').filter(
        pk=origen.gate_id
    ).first()
    if gate is None or gate.solicitud.empresa_id != detalle.empresa_id:
        raise ValidationError('La empresa formalizada no coincide con la solicitud.')
    return gate


def _validar_coincidencia_novedad(novedad, formalizacion, clave):
    if novedad.clave_idempotencia != clave:
        raise ValidationError('La novedad existente pertenece a otra formalizacion.')
    if (
        novedad.credito_id != formalizacion.credito_id
        or novedad.credito_libranza_id != formalizacion.credito_libranza_id
        or novedad.empresa_id != formalizacion.credito_libranza.empresa_id
    ):
        raise ValidationError('La novedad existente tiene relaciones inconsistentes.')


def _validar_novedad_para_envio(novedad):
    _validar_formalizacion_para_novedad(novedad.formalizacion)
    if novedad.credito_id != novedad.formalizacion.credito_id:
        raise ValidationError('La novedad no corresponde al credito formalizado.')


def _validar_empresa_pagador(novedad, perfil):
    if perfil.empresa_id != novedad.empresa_id:
        raise PermissionDenied('La novedad no pertenece a la empresa del pagador.')


def _exigir_staff(actor, permiso):
    if (
        actor is None
        or not actor.is_authenticated
        or not actor.is_active
        or not actor.is_staff
        or hasattr(actor, 'perfil_pagador')
        or not actor.has_perm(permiso)
    ):
        raise PermissionDenied('No tienes permiso para operar novedades de prestadores.')


def _marcar_error_controlado(novedad, *, codigo, etapa, actor):
    with transaction.atomic():
        novedad = NovedadOperativaPrestador.objects.select_for_update().get(
            pk=novedad.pk
        )
        novedad.estado = NovedadOperativaPrestador.Estado.ERROR_CONTROLADO
        novedad.error_codigo = str(codigo)[:80]
        novedad.error_etapa = str(etapa)[:80]
        novedad.save(update_fields=[
            'estado', 'error_codigo', 'error_etapa', 'updated_at'
        ])
    _registrar_evento(
        novedad,
        TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_ERROR,
        actor,
    )


def _registrar_evento(novedad, tipo_evento, actor, *, solicitud=None):
    if solicitud is None:
        gate = AprobacionInternaPrestador.objects.select_related('solicitud').get(
            pk=novedad.formalizacion.origen_credito_prestador.gate_id
        )
        solicitud = gate.solicitud
    registrar_evento_timeline_prestador(
        solicitud=solicitud,
        tipo_evento=tipo_evento,
        titulo=dict(TimelinePrestador.TipoEvento.choices).get(
            tipo_evento, 'Novedad operativa'
        ),
        descripcion='Evento operativo posterior a firma.',
        metadata={
            'novedad_id': novedad.id,
            'credito_id': novedad.credito_id,
            'empresa_id': novedad.empresa_id,
            'actor_id': getattr(actor, 'id', None),
            'canal': novedad.canal_envio,
            'estado': novedad.estado,
        },
        usuario=actor,
    )


def _enviar_email_novedad_operativa(*, dto, destinatarios):
    contexto = {'novedad': dto}
    html = render_to_string(
        'emails/pagadores/novedad_operativa_prestador.html',
        contexto,
    )
    mensaje = EmailMultiAlternatives(
        subject=f'Novedad operativa - credito {dto.numero_credito}',
        body=(
            f'Novedad operativa del credito {dto.numero_credito}. '
            'Confirma su recepcion desde el portal del pagador.'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=list(destinatarios),
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje.send(fail_silently=False) > 0


def _enmascarar_documento(documento):
    valor = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum())
    return f'****{valor[-4:]}' if valor else 'No disponible'


def _enmascarar_email(email):
    local, separador, dominio = str(email or '').partition('@')
    if not separador:
        return '***'
    visible = local[:1] if local else ''
    return f'{visible}***@{dominio}'


def _hash_email(email):
    clave = str(settings.SECRET_KEY).encode('utf-8')
    valor = str(email or '').strip().lower().encode('utf-8')
    return hmac.new(clave, b'pagador-email:' + valor, hashlib.sha256).hexdigest()

"""
Servicio de envío de emails para notificaciones del sistema de créditos.

Este módulo maneja todos los tipos de notificaciones por email usando Django SMTP:
- Cambios de estado de crédito
- Recordatorios de pago
- Alertas de mora
- Confirmaciones de pago

Configuración:
    Usa EMAIL_BACKEND de Django con Gmail SMTP
    Requiere EMAIL_HOST_USER y EMAIL_HOST_PASSWORD en settings
"""
import logging
import io
from decimal import Decimal
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string, get_template
from django.conf import settings
from django.utils import timezone
from django.urls import reverse
from weasyprint import HTML
from pypdf import PdfReader, PdfWriter
from .models import Credito
from .services.mora_notifications import preparar_alerta_mora_colaborador

logger = logging.getLogger(__name__)


def _obtener_resumen_cuenta_destino(detalle):
    """
    Arma un resumen legible de la cuenta destino solo si existen datos estructurados.
    Evita mostrar banco o numeros ficticios en comunicaciones al cliente.
    """
    if not detalle:
        return None

    banco = getattr(detalle, 'banco', None)
    numero_cuenta = getattr(detalle, 'numero_cuenta', None)

    if banco and numero_cuenta:
        ultimos = str(numero_cuenta)[-4:]
        return f"{banco} ****{ultimos}"

    return None


def _obtener_destinatarios_internos():
    return [
        email for email in getattr(settings, 'CREDIT_INTERNAL_NOTIFICATION_EMAILS', [])
        if email
    ]


def _agregar_destinatario(destinatarios, email):
    if not email:
        return
    normalizado = str(email).strip()
    if not normalizado:
        return
    existentes = {item.lower() for item in destinatarios}
    if normalizado.lower() not in existentes:
        destinatarios.append(normalizado)


def _build_absolute_url(path):
    if not path:
        return None
    host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    return f"https://{host}{path}"


def _nombre_archivo(file_field):
    if not file_field:
        return None
    try:
        return file_field.name.split('/')[-1]
    except Exception:
        return str(file_field)


def enviar_notificacion_interna_nueva_solicitud(credito):
    """
    Notifica al equipo interno cada vez que entra una nueva solicitud.
    Usa destinatarios configurables desde settings/.env.
    """
    destinatarios = _obtener_destinatarios_internos()
    if not destinatarios:
        logger.warning("No hay destinatarios configurados en CREDIT_INTERNAL_NOTIFICATION_EMAILS.")
        return False

    detalle = credito.detalle
    primary_host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    admin_url = f"https://{primary_host}{reverse('gestion:credito_detalle', kwargs={'credito_id': credito.id})}"

    nombre_cliente = credito.nombre_cliente
    cedula = (
        getattr(detalle, 'cedula', None)
        or getattr(detalle, 'numero_cedula', None)
        or 'No registrada'
    )
    empresa = getattr(getattr(detalle, 'empresa', None), 'nombre', None) or 'No aplica'
    email_cliente = getattr(detalle, 'correo_electronico', None) or getattr(credito.usuario, 'email', '')
    telefono = getattr(detalle, 'telefono', None) or getattr(detalle, 'celular_wh', None) or 'No registrado'

    context = {
        'credito': credito,
        'detalle': detalle,
        'nombre_cliente': nombre_cliente,
        'cedula': cedula,
        'empresa': empresa,
        'email_cliente': email_cliente,
        'telefono': telefono,
        'admin_url': admin_url,
    }

    try:
        html_content = render_to_string('emails/internos/notificacion_interna_nueva_solicitud.html', context)
        email = EmailMultiAlternatives(
            subject=f"Nueva solicitud de crédito - {credito.get_linea_display()} - {credito.numero_credito}",
            body=(
                f"Nueva solicitud registrada\n"
                f"Crédito: {credito.numero_credito}\n"
                f"Cliente: {nombre_cliente}\n"
                f"Cédula: {cedula}\n"
                f"Monto: ${credito.monto_solicitado:,.0f}\n"
                f"Plazo: {credito.plazo_solicitado} meses\n"
                f"Revisar: {admin_url}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info(
            "Notificación interna enviada para crédito %s a %s",
            credito.numero_credito,
            ", ".join(destinatarios)
        )
        return True
    except Exception as e:
        logger.error("Error al enviar notificación interna para crédito %s: %s", credito.numero_credito, e)
        return False


def enviar_email_html(destinatario, asunto, template_html, context, template_text=None):
    """
    Envía un email con contenido HTML y texto plano como fallback.

    Args:
        destinatario (str): Email del destinatario
        asunto (str): Asunto del email
        template_html (str): Ruta al template HTML
        context (dict): Contexto para renderizar los templates
        template_text (str, optional): Ruta al template de texto plano

    Returns:
        bool: True si se envió exitosamente, False en caso contrario
    """
    try:
        # Renderizar contenido HTML
        html_content = render_to_string(template_html, context)

        # Crear email con alternativas
        email = EmailMultiAlternatives(
            subject=asunto,
            body=context.get('mensaje_texto', ''),  # Texto plano como fallback
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario]
        )
        email.attach_alternative(html_content, "text/html")
        email.send()

        logger.info(f"Email enviado exitosamente a {destinatario}: {asunto}")
        return True

    except Exception as e:
        logger.error(f"Error al enviar email a {destinatario}: {e}")
        return False


def enviar_notificacion_cambio_estado(credito, nuevo_estado, motivo=""):
    """
    Envía notificación al cliente cuando cambia el estado de su crédito.

    Args:
        credito (Credito): Instancia del crédito
        nuevo_estado (str): Nuevo estado del crédito
        motivo (str): Motivo del cambio de estado
    """
    # Configurar asunto y mensaje según el estado
    # NOTA: APROBADO no envía email porque se integra con el proveedor de firma
    configuraciones = {
        Credito.EstadoCredito.EN_REVISION: {
            'asunto': 'Tu solicitud de crédito ha sido recibida',
            'template': 'emails/usuarios/credito_en_revision.html',
        },
        Credito.EstadoCredito.RECHAZADO: {
            'asunto': 'Actualización sobre tu solicitud de crédito',
            'template': 'emails/usuarios/credito_rechazado.html',
        },
        Credito.EstadoCredito.ACTIVO: {
            'asunto': '¡Tu crédito ha sido desembolsado!',
            'template': 'emails/usuarios/credito_desembolsado.html',
        },
        Credito.EstadoCredito.EN_MORA: {
            'asunto': 'Alerta: Tu crédito está en mora',
            'template': 'emails/usuarios/credito_en_mora.html',
        },
        Credito.EstadoCredito.PAGADO: {
            'asunto': '¡Felicitaciones! Has completado tu crédito',
            'template': 'emails/usuarios/credito_pagado.html',
        },
    }

    config = configuraciones.get(nuevo_estado)
    if not config:
        if nuevo_estado in {
            Credito.EstadoCredito.APROBADO_PAGADOR,
            Credito.EstadoCredito.APROBADO,
            Credito.EstadoCredito.PENDIENTE_FIRMA,
        }:
            return False
        logger.warning(f"No hay configuración de email para el estado: {nuevo_estado}")
        return False

    detalle = credito.detalle
    cedula_solicitante = "No registrada"
    if detalle:
        cedula_solicitante = (
            getattr(detalle, 'cedula', None)
            or getattr(detalle, 'numero_cedula', None)
            or "No registrada"
        )

    plazo_solicitado = credito.plazo_solicitado or credito.plazo or "-"
    primary_host = getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
    emprender_host = getattr(settings, 'EMPRENDER_SUBDOMAIN_HOST', 'emprender.aprobado.com.co')

    if credito.linea == Credito.LineaCredito.LIBRANZA:
        cta_url = f'https://{primary_host}/libranza/login/?next=/libranza/mi-credito/'
    else:
        cta_url = f'https://{emprender_host}/emprendimiento/login/?next=/emprendimiento/mi-credito/'
    cta_label = 'Consultar Estado'

    context = {
        'credito': credito,
        'nombre_cliente': credito.nombre_cliente,
        'nuevo_estado': credito.get_estado_display(),
        'motivo': motivo,
        'numero_credito': credito.numero_credito,
        'cedula_solicitante': cedula_solicitante,
        'plazo_solicitado_email': plazo_solicitado,
        'cta_url': cta_url,
        'cta_label': cta_label,
        'cuenta_destino_resumen': _obtener_resumen_cuenta_destino(credito.detalle),
    }

    # Renderizar contenido HTML
    html_content = render_to_string(config['template'], context)

    # Crear email con alternativas
    email = EmailMultiAlternatives(
        subject=config['asunto'],
        body=context.get('mensaje_texto', ''),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[credito.usuario.email]
    )
    email.attach_alternative(html_content, "text/html")

    # Si es desembolso (ACTIVO), adjuntar PDF del plan de pagos
    if nuevo_estado == Credito.EstadoCredito.ACTIVO:
        try:
            # Generar PDF del plan de pagos
            pdf_content = generar_pdf_plan_pagos(credito)
            if pdf_content:
                email.attach(
                    f'plan_de_pagos_{credito.numero_credito}.pdf',
                    pdf_content,
                    'application/pdf'
                )
                logger.info(f"PDF del plan de pagos adjuntado al email de desembolso para crédito {credito.numero_credito}")
        except Exception as e:
            logger.error(f"Error al generar PDF del plan de pagos para crédito {credito.numero_credito}: {e}")

    # Enviar email
    try:
        email.send()
        logger.info(f"Email enviado exitosamente a {credito.usuario.email}: {config['asunto']}")
        return True
    except Exception as e:
        logger.error(f"Error al enviar email a {credito.usuario.email}: {e}")
        return False


def enviar_recordatorio_pago(credito, dias_restantes):
    """
    Envía recordatorio de pago próximo a vencer.

    Args:
        credito (Credito): Instancia del crédito
        dias_restantes (int): Días que faltan para el vencimiento
    """
    asunto = f"Recordatorio: Tu cuota vence en {dias_restantes} días"

    context = {
        'credito': credito,
        'nombre_cliente': credito.nombre_cliente,
        'dias_restantes': dias_restantes,
        'valor_cuota': f"${credito.valor_cuota:,.2f}",
        'fecha_vencimiento': credito.fecha_proximo_pago,
        'numero_credito': credito.numero_credito,
    }

    return enviar_email_html(
        destinatario=credito.usuario.email,
        asunto=asunto,
        template_html='emails/usuarios/recordatorio_pago.html',
        context=context
    )


def enviar_confirmacion_pago(
    credito,
    monto_pagado,
    nuevo_saldo,
    destinatario=None,
    nombre_destinatario=None,
    referencia=None,
    metodo_pago=None,
    banco=None,
    fecha_pago=None,
    cta_url=None,
    cta_label=None,
):
    """
    Envía confirmación de pago recibido.

    Args:
        credito (Credito): Instancia del crédito
        monto_pagado (Decimal): Monto del pago
        nuevo_saldo (Decimal): Nuevo saldo pendiente
    """
    asunto = "Confirmación de pago recibido"

    context = {
        'credito': credito,
        'nombre_cliente': nombre_destinatario or credito.nombre_cliente,
        'monto_pagado': f"${monto_pagado:,.2f}",
        'nuevo_saldo': f"${nuevo_saldo:,.2f}",
        'numero_credito': credito.numero_credito,
        'fecha_proximo_pago': credito.fecha_proximo_pago,
        'fecha_pago': fecha_pago or timezone.now(),
        'referencia_pago': referencia,
        'metodo_pago': metodo_pago,
        'banco': banco,
        'cta_url': cta_url,
        'cta_label': cta_label,
    }

    return enviar_email_html(
        destinatario=destinatario or credito.usuario.email,
        asunto=asunto,
        template_html='emails/usuarios/confirmacion_pago.html',
        context=context
    )


def enviar_alerta_mora(credito, dias_mora):
    """
    Envía alerta cuando el crédito entra en mora.

    Args:
        credito (Credito): Instancia del crédito
        dias_mora (int): Días en mora
    """
    asunto = f"URGENTE: Tu crédito tiene {dias_mora} días de mora"

    context = {
        'credito': credito,
        'nombre_cliente': credito.nombre_cliente,
        'dias_mora': dias_mora,
        'saldo_pendiente': f"${credito.saldo_pendiente:,.2f}",
        'valor_cuota': f"${credito.valor_cuota:,.2f}",
        'numero_credito': credito.numero_credito,
    }

    return enviar_email_html(
        destinatario=credito.usuario.email,
        asunto=asunto,
        template_html='emails/usuarios/alerta_mora.html',
        context=context
    )


def enviar_email_simple(destinatario, asunto, mensaje):
    """
    Envía un email simple sin template (texto plano).

    Args:
        destinatario (str): Email del destinatario
        asunto (str): Asunto del email
        mensaje (str): Contenido del mensaje

    Returns:
        bool: True si se envió exitosamente, False en caso contrario
    """
    try:
        send_mail(
            subject=asunto,
            message=mensaje,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[destinatario],
            fail_silently=False,
        )
        logger.info(f"Email simple enviado a {destinatario}: {asunto}")
        return True

    except Exception as e:
        logger.error(f"Error al enviar email simple a {destinatario}: {e}")
        return False


def enviar_notificacion_solicitud_libranza_empresa(destinatario, empresa, credito, detalle, dashboard_url, login_url):
    """
    Envía un email al pagador/empresa cuando se registra una nueva solicitud de libranza.

    Este correo se usa para solicitar la validación previa del pagador antes de la aprobación administrativa.
    """
    asunto = f"Nueva solicitud de libranza - {detalle.nombre_completo}"
    context = {
        'empresa': empresa,
        'credito': credito,
        'detalle': detalle,
        'dashboard_url': dashboard_url,
        'login_url': login_url,
    }

    return enviar_email_html(
        destinatario=destinatario,
        asunto=asunto,
        template_html='emails/pagadores/notificacion_solicitud_libranza_empresa.html',
        context=context
    )


def enviar_resumen_operativo_pago_offline(*, credito, pago, usuario=None):
    destinatarios = _obtener_destinatarios_internos()
    _agregar_destinatario(destinatarios, getattr(usuario, 'email', None))

    if not destinatarios:
        logger.warning(
            "No hay destinatarios para el resumen operativo del pago offline %s.",
            getattr(pago, 'referencia_pago', 'sin-referencia'),
        )
        return False

    empresa = pago.empresa_origen or credito.empresa_relacionada
    comprobante = pago.comprobante or getattr(getattr(pago, 'lote_pago', None), 'comprobante', None)
    credito_url = None
    try:
        credito_url = _build_absolute_url(
            reverse('gestion:credito_detalle', kwargs={'credito_id': credito.id})
        )
    except Exception:
        credito_url = None

    context = {
        'titulo': 'Pago offline aplicado',
        'subtitulo': 'Se registró un pago manual y quedó trazabilidad operativa del movimiento.',
        'empresa_nombre': getattr(empresa, 'nombre', 'No definida'),
        'referencia': pago.referencia_pago,
        'fecha_aplicacion': pago.fecha_aplicacion,
        'usuario_nombre': (
            getattr(usuario, 'get_full_name', lambda: '')() or getattr(usuario, 'username', '') or 'Sistema'
        ),
        'comprobante_nombre': _nombre_archivo(comprobante),
        'credito_url': credito_url,
        'credito_numero': credito.numero_credito,
        'cliente_nombre': credito.nombre_cliente,
        'monto_total': pago.monto,
        'cantidad_registros': 1,
        'metodo_pago': pago.get_metodo_pago_display(),
        'origen_label': 'Registro manual',
        'notas': pago.notas or '',
    }

    try:
        html_content = render_to_string('emails/operacion/resumen_operativo_pago_offline.html', context)
        email = EmailMultiAlternatives(
            subject=f"Pago offline aplicado - {credito.numero_credito}",
            body=(
                f"Empresa: {context['empresa_nombre']}\n"
                f"Crédito: {credito.numero_credito}\n"
                f"Cliente: {credito.nombre_cliente}\n"
                f"Monto aplicado: ${pago.monto:,.2f}\n"
                f"Referencia: {pago.referencia_pago}\n"
                f"Registrado por: {context['usuario_nombre']}\n"
                f"Fecha de aplicación: {context['fecha_aplicacion']:%d/%m/%Y %H:%M}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info(
            "Resumen operativo de pago offline enviado para %s a %s",
            credito.numero_credito,
            ", ".join(destinatarios),
        )
        return True
    except Exception as exc:
        logger.error(
            "Error al enviar resumen operativo del pago offline %s: %s",
            pago.referencia_pago,
            exc,
        )
        return False


def enviar_resumen_operativo_carga_pagos(*, lote, pagos_aplicados, monto_total, usuario=None):
    destinatarios = _obtener_destinatarios_internos()
    _agregar_destinatario(destinatarios, getattr(usuario, 'email', None))

    if not destinatarios:
        logger.warning(
            "No hay destinatarios para el resumen operativo de la carga de pagos %s.",
            lote.id,
        )
        return False

    dashboard_url = None
    try:
        dashboard_url = _build_absolute_url(reverse('pagador:dashboard'))
    except Exception:
        dashboard_url = None

    referencia = f"CP-{lote.id:05d}"
    context = {
        'titulo': 'Carga de pagos confirmada',
        'subtitulo': 'La carga de pagos por archivo fue aplicada y quedó registrada para seguimiento operativo.',
        'empresa_nombre': lote.empresa.nombre,
        'referencia': referencia,
        'fecha_aplicacion': timezone.localtime(lote.creado_en),
        'usuario_nombre': (
            getattr(usuario, 'get_full_name', lambda: '')() or getattr(usuario, 'username', '') or 'Sistema'
        ),
        'comprobante_nombre': _nombre_archivo(lote.comprobante),
        'credito_url': dashboard_url,
        'credito_numero': lote.nombre_original,
        'cliente_nombre': 'Carga por archivo',
        'monto_total': monto_total,
        'cantidad_registros': pagos_aplicados,
        'metodo_pago': 'Transferencia directa',
        'origen_label': 'Carga de pagos',
        'notas': lote.notas or '',
    }

    try:
        html_content = render_to_string('emails/operacion/resumen_operativo_pago_offline.html', context)
        email = EmailMultiAlternatives(
            subject=f"Carga de pagos confirmada - {lote.empresa.nombre} ({pagos_aplicados} aplicados)",
            body=(
                f"Empresa: {lote.empresa.nombre}\n"
                f"Referencia: {referencia}\n"
                f"Archivo: {lote.nombre_original}\n"
                f"Pagos aplicados: {pagos_aplicados}\n"
                f"Total aplicado: ${monto_total:,.2f}\n"
                f"Registrado por: {context['usuario_nombre']}\n"
                f"Fecha: {context['fecha_aplicacion']:%d/%m/%Y %H:%M}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info(
            "Resumen operativo de carga de pagos enviado para lote %s a %s",
            lote.id,
            ", ".join(destinatarios),
        )
        return True
    except Exception as exc:
        logger.error(
            "Error al enviar resumen operativo de la carga de pagos %s: %s",
            lote.id,
            exc,
        )
        return False


def enviar_resumen_pago_masivo_pagador(*, lote, pagos_aplicados, monto_total, pagador_email, pagador_nombre=''):
    if not pagador_email:
        logger.warning(
            "No se envió resumen de carga de pagos porque el pagador no tiene correo. Lote %s.",
            lote.id,
        )
        return False


    dashboard_url = None
    try:
        dashboard_url = _build_absolute_url(reverse('pagador:dashboard'))
    except Exception:
        dashboard_url = None

    referencia = f"CP-{lote.id:05d}"
    context = {
        'titulo': 'Carga de pagos confirmada',
        'subtitulo': 'Tu archivo fue aplicado correctamente y este correo resume la confirmacion del pago masivo.',
        'empresa_nombre': lote.empresa.nombre,
        'referencia': referencia,
        'fecha_aplicacion': timezone.localtime(lote.creado_en),
        'usuario_nombre': pagador_nombre or 'Pagador',
        'comprobante_nombre': _nombre_archivo(lote.comprobante),
        'credito_url': dashboard_url,
        'credito_numero': lote.nombre_original,
        'cliente_nombre': 'Archivo de pagos',
        'monto_total': monto_total,
        'cantidad_registros': pagos_aplicados,
        'metodo_pago': 'Transferencia directa',
        'origen_label': 'Pago masivo offline',
        'notas': lote.notas or '',
    }

    try:
        html_content = render_to_string('emails/operacion/resumen_operativo_pago_offline.html', context)
        email = EmailMultiAlternatives(
            subject=f"Carga de pagos confirmada - {lote.empresa.nombre} ({pagos_aplicados} aplicados)",
            body=(
                f"Empresa: {lote.empresa.nombre}\n"
                f"Referencia: {referencia}\n"
                f"Archivo: {lote.nombre_original}\n"
                f"Pagos aplicados: {pagos_aplicados}\n"
                f"Total aplicado: ${monto_total:,.2f}\n"
                f"Confirmado por: {context['usuario_nombre']}\n"
                f"Fecha: {context['fecha_aplicacion']:%d/%m/%Y %H:%M}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[pagador_email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info(
            "Resumen de carga de pagos enviado al pagador para lote %s a %s",
            lote.id,
            pagador_email,
        )
        return True
    except Exception as exc:
        logger.error(
            "Error al enviar resumen de la carga de pagos %s: %s",
            lote.id,
            exc,
        )
        return False


def preparar_resumen_cuotas_pendientes_pagador(
    *,
    empresa,
    cuotas,
    fecha_corte,
    fecha_inicio_vencimiento=None,
    fecha_fin_vencimiento=None,
):
    from usuarios.models import PerfilPagador

    if not cuotas:
        return {
            'empresa': empresa,
            'cuotas': [],
            'destinatarios': [],
            'internos': [],
            'items': [],
            'total': Decimal('0.00'),
            'fecha_corte': fecha_corte,
            'fecha_inicio_vencimiento': fecha_inicio_vencimiento,
            'fecha_fin_vencimiento': fecha_fin_vencimiento,
        }

    destinatarios = []
    for perfil in PerfilPagador.objects.select_related('usuario').filter(empresa=empresa, es_pagador=True):
        _agregar_destinatario(destinatarios, getattr(perfil.usuario, 'email', None))
    _agregar_destinatario(destinatarios, empresa.correo_contacto)

    internos = list(getattr(settings, 'CREDIT_INTERNAL_NOTIFICATION_EMAILS', []))
    items = []
    total = Decimal('0.00')
    for cuota in cuotas:
        credito = cuota.credito
        restante = (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00'))
        total += restante
        items.append({
            'numero_credito': credito.numero_credito,
            'nombre_cliente': credito.nombre_cliente,
            'documento': credito.cliente_documento,
            'fecha_vencimiento': cuota.fecha_vencimiento,
            'monto': restante,
        })

    context = {
        'empresa': empresa,
        'fecha_corte': fecha_corte,
        'fecha_inicio_vencimiento': fecha_inicio_vencimiento,
        'fecha_fin_vencimiento': fecha_fin_vencimiento,
        'items': items,
        'total': total,
    }
    try:
        context['dashboard_url'] = _build_absolute_url(reverse('pagador:dashboard'))
    except Exception:
        context['dashboard_url'] = None
    return {
        'empresa': empresa,
        'cuotas': list(cuotas),
        'destinatarios': destinatarios,
        'internos': internos,
        'items': items,
        'total': total,
        'fecha_corte': fecha_corte,
        'fecha_inicio_vencimiento': fecha_inicio_vencimiento,
        'fecha_fin_vencimiento': fecha_fin_vencimiento,
        'context': context,
    }


def enviar_resumen_cuotas_pendientes_pagador(
    *,
    empresa,
    cuotas,
    fecha_corte,
    fecha_inicio_vencimiento=None,
    fecha_fin_vencimiento=None,
    destinatarios_override=None,
    cc_override=None,
):
    payload = preparar_resumen_cuotas_pendientes_pagador(
        empresa=empresa,
        cuotas=cuotas,
        fecha_corte=fecha_corte,
        fecha_inicio_vencimiento=fecha_inicio_vencimiento,
        fecha_fin_vencimiento=fecha_fin_vencimiento,
    )
    destinatarios = (
        list(destinatarios_override)
        if destinatarios_override is not None
        else payload['destinatarios']
    )
    internos = list(cc_override) if cc_override is not None else payload['internos']

    if not destinatarios:
        logger.warning("No hay destinatarios pagador para resumen mensual de cuotas de %s.", empresa.nombre)
        return False

    html_content = render_to_string(
        'emails/pagadores/pagador_resumen_cuotas_pendientes.html',
        payload['context'],
    )
    body = (
        f"Empresa: {empresa.nombre}\n"
        f"Fecha de referencia: {fecha_corte:%d/%m/%Y}\n"
        f"Cuotas proximas a vencer: {len(payload['items'])}\n"
        f"Total estimado: ${payload['total']:,.2f}\n"
    )
    try:
        email = EmailMultiAlternatives(
            subject=f"Resumen de obligaciones proximas a vencer - {empresa.nombre}",
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
            cc=internos,
        )
        email.attach_alternative(html_content, 'text/html')
        email.send()
        logger.info(
            "Resumen de obligaciones proximas a vencer enviado | empresa=%s destinatarios=%s cc=%s cuotas=%s total=%s",
            empresa.nombre,
            destinatarios,
            internos,
            len(payload['items']),
            payload['total'],
        )
        return True
    except Exception as exc:
        logger.error("Error al enviar resumen de cuotas proximas a vencer al pagador: %s", exc)
        return False


def enviar_alerta_obligacion_pendiente_usuario(*, credito, cuota, dias_atraso):
    destinatario = credito.cliente_email or credito.usuario.email
    if not destinatario:
        logger.warning("No hay destinatario para alerta de atraso del credito %s.", credito.numero_credito)
        return False

    alerta = preparar_alerta_mora_colaborador(
        dias_mora=dias_atraso,
        numero_credito=credito.numero_credito,
    )
    if alerta is None:
        logger.info(
            "Alerta de atraso omitida para credito %s: %s dias no alcanza el umbral.",
            credito.numero_credito,
            dias_atraso,
        )
        return False

    restante = (cuota.valor_cuota or Decimal('0.00')) - (cuota.monto_pagado or Decimal('0.00'))
    context = {
        'credito': credito,
        'cuota': cuota,
        'dias_atraso': dias_atraso,
        'restante': restante,
        'nombre_cliente': credito.nombre_cliente,
        **alerta,
    }
    return enviar_email_html(
        destinatario=destinatario,
        asunto=alerta['asunto'],
        template_html='emails/usuarios/usuario_alerta_obligacion_pendiente.html',
        context=context,
    )

    dashboard_url = None
    try:
        dashboard_url = _build_absolute_url(reverse('pagador:dashboard'))
    except Exception:
        dashboard_url = None

    referencia = f"CP-{lote.id:05d}"
    context = {
        'titulo': 'Carga de pagos confirmada',
        'subtitulo': 'Tu archivo fue aplicado correctamente y este correo resume la confirmación del pago masivo.',
        'empresa_nombre': lote.empresa.nombre,
        'referencia': referencia,
        'fecha_aplicacion': timezone.localtime(lote.creado_en),
        'usuario_nombre': pagador_nombre or 'Pagador',
        'comprobante_nombre': _nombre_archivo(lote.comprobante),
        'credito_url': dashboard_url,
        'credito_numero': lote.nombre_original,
        'cliente_nombre': 'Archivo de pagos',
        'monto_total': monto_total,
        'cantidad_registros': pagos_aplicados,
        'metodo_pago': 'Transferencia directa',
        'origen_label': 'Pago masivo offline',
        'notas': lote.notas or '',
    }

    try:
        html_content = render_to_string('emails/operacion/resumen_operativo_pago_offline.html', context)
        email = EmailMultiAlternatives(
            subject=f"Carga de pagos confirmada - {lote.empresa.nombre} ({pagos_aplicados} aplicados)",
            body=(
                f"Empresa: {lote.empresa.nombre}\n"
                f"Referencia: {referencia}\n"
                f"Archivo: {lote.nombre_original}\n"
                f"Pagos aplicados: {pagos_aplicados}\n"
                f"Total aplicado: ${monto_total:,.2f}\n"
                f"Confirmado por: {context['usuario_nombre']}\n"
                f"Fecha: {context['fecha_aplicacion']:%d/%m/%Y %H:%M}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[pagador_email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info(
            "Resumen de carga de pagos enviado al pagador para lote %s a %s",
            lote.id,
            pagador_email,
        )
        return True
    except Exception as exc:
        logger.error(
            "Error al enviar resumen de la carga de pagos %s: %s",
            lote.id,
            exc,
        )
        return False


def generar_pdf_plan_pagos(credito):
    """
    Genera un PDF con el plan de pagos del crédito.
    El PDF está protegido con la cédula del cliente como contraseña.

    Args:
        credito (Credito): Instancia del crédito

    Returns:
        bytes: Contenido del PDF en bytes, o None si hay error
    """
    try:
        from django.contrib.staticfiles import finders
        import base64

        # Función auxiliar para obtener el logo
        def get_logo_base64():
            logo_path = finders.find('images/logo-dark.png')
            if not logo_path:
                return None
            try:
                with open(logo_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                return f"data:image/png;base64,{encoded_string}"
            except (IOError, FileNotFoundError):
                return None

        plan_pagos = credito.tabla_amortizacion.all().order_by('numero_cuota')
        detalle = credito.detalle

        context = {
            'credito': credito,
            'usuario': credito.usuario,
            'detalle': detalle,
            'plan_pagos': plan_pagos,
            'fecha_generacion': timezone.now(),
            'logo_base64': get_logo_base64(),
            'es_libranza': credito.linea == Credito.LineaCredito.LIBRANZA,
            'linea_credito': credito.get_linea_display(),
            'telefono_contacto': '+57 313 247 7352',
            'sede_ciudad': 'Villavicencio, Meta, Colombia',
        }

        template = get_template('usuariocreditos/plan_pagos_pdf.html')
        html_content = template.render(context)

        # Generar PDF con WeasyPrint
        pdf_bytes = HTML(string=html_content).write_pdf()

        # Obtener la cédula del cliente para encriptar el PDF
        cedula = None
        if credito.linea == credito.LineaCredito.EMPRENDIMIENTO and hasattr(detalle, 'numero_cedula'):
            cedula = detalle.numero_cedula
        elif credito.linea == credito.LineaCredito.LIBRANZA and hasattr(detalle, 'cedula'):
            cedula = detalle.cedula

        # Encriptar el PDF si hay cédula disponible
        if cedula:
            # Crear reader y writer para pypdf
            pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
            pdf_writer = PdfWriter()

            # Copiar todas las páginas
            for page in pdf_reader.pages:
                pdf_writer.add_page(page)

            # Encriptar con la cédula como contraseña
            pdf_writer.encrypt(user_password=str(cedula), owner_password=str(cedula))

            # Generar el PDF encriptado
            encrypted_pdf = io.BytesIO()
            pdf_writer.write(encrypted_pdf)
            return encrypted_pdf.getvalue()
        else:
            return pdf_bytes

    except Exception as e:
        logger.error(f"Error al generar PDF del plan de pagos para crédito {credito.numero_credito}: {e}")
        return None

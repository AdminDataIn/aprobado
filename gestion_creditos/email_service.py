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
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string, get_template
from django.conf import settings
from django.utils import timezone
from weasyprint import HTML
from pypdf import PdfReader, PdfWriter
from .models import Credito

logger = logging.getLogger(__name__)


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
            'template': 'emails/credito_en_revision.html',
        },
        Credito.EstadoCredito.RECHAZADO: {
            'asunto': 'Actualización sobre tu solicitud de crédito',
            'template': 'emails/credito_rechazado.html',
        },
        Credito.EstadoCredito.ACTIVO: {
            'asunto': '¡Tu crédito ha sido desembolsado!',
            'template': 'emails/credito_desembolsado.html',
        },
        Credito.EstadoCredito.EN_MORA: {
            'asunto': 'Alerta: Tu crédito está en mora',
            'template': 'emails/credito_en_mora.html',
        },
        Credito.EstadoCredito.PAGADO: {
            'asunto': '¡Felicitaciones! Has completado tu crédito',
            'template': 'emails/credito_pagado.html',
        },
    }

    config = configuraciones.get(nuevo_estado)
    if not config:
        if nuevo_estado in {
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

    context = {
        'credito': credito,
        'nombre_cliente': credito.nombre_cliente,
        'nuevo_estado': credito.get_estado_display(),
        'motivo': motivo,
        'numero_credito': credito.numero_credito,
        'cedula_solicitante': cedula_solicitante,
        'plazo_solicitado_email': plazo_solicitado,
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
        template_html='emails/recordatorio_pago.html',
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
        template_html='emails/confirmacion_pago.html',
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
        template_html='emails/alerta_mora.html',
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
        template_html='emails/notificacion_solicitud_libranza_empresa.html',
        context=context
    )


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

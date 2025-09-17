from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse

from .models import Credito

#! LOGICA PENSADA PERO NO IMPLEMENTADA AUN
@receiver(post_save, sender=Credito)
def gestionar_aprobacion_credito(sender, instance, created, **kwargs):
    """
    Señal para enviar correo de firma de pagaré cuando un crédito es aprobado.
    """
    # Verificar si el crédito fue aprobado y el documento aún no se ha enviado
    if instance.estado == Credito.EstadoCredito.APROBADO and not instance.documento_enviado:
        print(f"Crédito {instance.numero_credito} aprobado. Enviando correo para firma.")

        # --- FALTA IMPLEMENTAR AUTENTIC ---
        # 1. Generar el pagaré en formato PDF.
        # 2. Enviar el documento al servicio de firma digital para crear una sesión de firma.
        # 3. El servicio retornará una URL única para que el cliente firme.
        
        # URL de firma (simulada por ahora)
        # En un caso real, esta URL vendría del servicio de firma digital.
        # El webhook debería apuntar a una URL que creemos para recibir la notificación.
        webhook_url = settings.SITE_URL + reverse('webhook_firma_documento', kwargs={'numero_credito': instance.numero_credito})
        url_firma = f"https://servicio-firma.com/firmar?documento_id={instance.numero_credito}&callback_url={webhook_url}"

        # Preparar y enviar el correo
        asunto = f"¡Tu crédito ha sido aprobado! Firma el pagaré para continuar"
        mensaje = (
            f"Hola {instance.usuario.get_full_name() or instance.usuario.username},<br><br>"
            f"¡Buenas noticias! Tu crédito con número {instance.numero_credito} ha sido aprobado.<br>"
            f"El siguiente paso es firmar el pagaré digitalmente. Por favor, haz clic en el siguiente enlace:<br><br>"
            f'<a href="{url_firma}">Firmar Pagaré Ahora</a><br><br>'
            f"Una vez firmado, procesaremos el desembolso de tu crédito.<br><br>"
            f"Gracias,<br>El equipo de Aprobado"
        )
        
        send_mail(
            subject=asunto,
            message="", # El mensaje HTML se pasa en html_message
            html_message=mensaje,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.usuario.email],
            fail_silently=False,
        )

        # Marcar el documento como enviado para no volver a enviarlo
        instance.documento_enviado = True
        instance.save(update_fields=['documento_enviado'])
        print(f"Correo para firma del crédito {instance.numero_credito} enviado a {instance.usuario.email}.")

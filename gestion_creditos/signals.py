from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse

from .models import Credito

#! LOGICA PENSADA PERO NO IMPLEMENTADA AUN
@receiver(post_save, sender=Credito)
def gestionar_aprobacion_credito(sender, instance, **kwargs):
    """
    Señal para enviar correo de firma de pagaré cuando un crédito es aprobado
    y actualizar el estado a PENDIENTE_TRANSFERENCIA.
    """
    if instance.estado == Credito.EstadoCredito.APROBADO and not instance.documento_enviado:
        print(f"Crédito {instance.numero_credito} aprobado. Enviando correo para firma.")

        # --- Lógica de envío de correo y firma ---
        try:
            webhook_url = settings.SITE_URL + reverse('gestion_creditos:webhook_firma_documento', kwargs={'numero_credito': instance.numero_credito})
        except AttributeError:
            print("ERROR: La variable SITE_URL no está definida en el archivo de settings. Usando un valor temporal.")
            webhook_url = "https://example.com" # Valor temporal para evitar que el programa falle

        url_firma = f"https://servicio-firma.com/firmar?documento_id={instance.numero_credito}&callback_url={webhook_url}"
        
        asunto = "¡Tu crédito ha sido aprobado! Firma el pagaré para continuar"
        mensaje = (
            f"Hola {instance.usuario.get_full_name() or instance.usuario.username},<br><br>"
            f"¡Buenas noticias! Tu crédito con número {instance.numero_credito} ha sido aprobado.<br>"
            f"El siguiente paso es firmar el pagaré digitalmente. Por favor, haz clic en el siguiente enlace:<br><br>"
            f'<a href="{url_firma}">Firmar Pagaré Ahora</a><br><br>'
            f"Una vez firmado, procesaremos el desembolso de tu crédito.<br><br>"
            f"Gracias,<br>El equipo de Aprobado"
        )
        
        try:
            send_mail(
                subject=asunto,
                message="",
                html_message=mensaje,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[instance.usuario.email],
                fail_silently=False,
            )
            print(f"Correo para firma del crédito {instance.numero_credito} enviado a {instance.usuario.email}.")
        except Exception as e:
            print(f"ERROR: No se pudo enviar el correo para el crédito {instance.numero_credito}. Error: {e}")

        # Desconectar la señal temporalmente para evitar recursión
        post_save.disconnect(gestionar_aprobacion_credito, sender=Credito)
        
        try:
            # Actualizar el estado y marcar como documento enviado
            instance.documento_enviado = True
            instance.estado = Credito.EstadoCredito.PENDIENTE_FIRMA
            instance.save(update_fields=['documento_enviado', 'estado'])
            print(f"Crédito {instance.numero_credito} actualizado a PENDIENTE_FIRMA.")
        finally:
            # Reconectar la señal
            post_save.connect(gestionar_aprobacion_credito, sender=Credito)

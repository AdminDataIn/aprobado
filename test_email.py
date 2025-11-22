"""
Script de prueba rápida para el servicio de emails.
Ejecutar con: python test_email.py
"""
import os
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
django.setup()

from gestion_creditos.email_service import enviar_email_simple

print("=" * 50)
print("PRUEBA DE ENVÍO DE EMAIL CON GMAIL SMTP")
print("=" * 50)

# Cambia este email por el tuyo
destinatario = input("\nIngresa tu email para recibir la prueba: ")

print(f"\nEnviando email de prueba a: {destinatario}")
print("Espera un momento...")

resultado = enviar_email_simple(
    destinatario=destinatario,
    asunto='Prueba de Email desde Django con Gmail SMTP',
    mensaje='Este es un email de prueba. Si lo recibes, el sistema funciona correctamente!'
)

print("\n" + "=" * 50)
if resultado:
    print("✅ EMAIL ENVIADO EXITOSAMENTE")
    print(f"Revisa tu bandeja de entrada: {destinatario}")
    print("También revisa la carpeta de SPAM por si acaso")
else:
    print("❌ ERROR AL ENVIAR EMAIL")
    print("Revisa los logs arriba para más detalles")
print("=" * 50)

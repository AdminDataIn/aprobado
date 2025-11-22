# 📧 GUÍA COMPLETA: CONFIGURACIÓN DE GMAIL API

> **NOTA**: Actualmente el sistema usa **Gmail SMTP** que es más simple y funcional.
> Esta guía es para implementar Gmail API en el futuro si deseas mayor robustez y control.

---

## 📋 ¿CUÁNDO USAR GMAIL API EN LUGAR DE SMTP?

### **Usa Gmail API si**:
- ✅ Tienes **Google Workspace** (G Suite) con dominio propio
- ✅ Necesitas enviar más de 500 emails por día
- ✅ Quieres acceso a todas las funcionalidades de Gmail (lectura, etiquetas, etc.)
- ✅ Necesitas mayor control y monitoreo de emails

### **Usa SMTP (actual) si**:
- ✅ Tienes cuenta Gmail personal
- ✅ Envías menos de 500 emails por día
- ✅ Solo necesitas enviar emails (no leer ni gestionar)
- ✅ Prefieres simplicidad

---

## 🚀 ARQUITECTURA DE GMAIL API

Gmail API tiene 3 métodos de autenticación:

### **1. Service Account + Domain-Wide Delegation** (Google Workspace)
**Mejor para**: Aplicaciones server-to-server en Google Workspace
- ✅ No requiere interacción del usuario
- ✅ Funciona sin tokens que expiren
- ❌ Solo funciona con Google Workspace (de pago)
- ❌ Requiere configuración de dominio

### **2. OAuth 2.0 User Consent** (Gmail Personal - RECOMENDADO)
**Mejor para**: Aplicaciones que usan Gmail personal
- ✅ Funciona con cuentas Gmail gratuitas
- ✅ Más seguro con refresh tokens
- ❌ Requiere autorización inicial del usuario
- ❌ Tokens pueden expirar

### **3. API Key** (Solo lectura pública)
**No aplica** para envío de emails

---

## 📦 ARCHIVOS Y CÓDIGO PARA GMAIL API

### **Archivo 1: `gestion_creditos/email_service_gmail_api.py`**

Este archivo contiene la implementación completa de Gmail API:

```python
"""
Servicio de envío de emails usando Gmail API con OAuth2.

IMPORTANTE: Este archivo es una alternativa a email_service.py (que usa SMTP).
Para usar Gmail API:
1. Renombra email_service.py a email_service_smtp.py
2. Renombra este archivo a email_service.py
3. Configura las credenciales según GMAIL_API_SETUP.md
"""
import logging
import base64
import os
import pickle
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from django.template.loader import render_to_string
from django.conf import settings
from .models import Credito

logger = logging.getLogger(__name__)

# Scopes necesarios para enviar emails
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# Ruta donde se guardan los tokens de autorización
TOKEN_FILE = os.path.join(settings.BASE_DIR, 'config', 'gmail_token.pickle')
CREDENTIALS_FILE = os.path.join(settings.BASE_DIR, 'config', 'gmail_oauth_credentials.json')


def get_gmail_service_oauth():
    """
    Crea y retorna un servicio de Gmail API autenticado con OAuth2.

    En la primera ejecución, abrirá el navegador para autorizar la aplicación.
    Luego guardará el token para futuras ejecuciones.

    Returns:
        googleapiclient.discovery.Resource: Servicio de Gmail API

    Raises:
        Exception: Si hay problemas con las credenciales o la autenticación
    """
    creds = None

    # Verificar si ya existe un token guardado
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # Si no hay credenciales válidas, obtener nuevas
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refrescar el token expirado
            logger.info("Refrescando token de Gmail API...")
            creds.refresh(Request())
        else:
            # Iniciar flujo de autorización (abre navegador)
            logger.info("Iniciando flujo de autorización OAuth2...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=8080)

        # Guardar el token para futuras ejecuciones
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
        logger.info("Token de Gmail guardado exitosamente")

    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Error al crear servicio de Gmail API: {e}")
        raise


def crear_mensaje_mime(destinatario, asunto, contenido_html, contenido_texto=""):
    """
    Crea un mensaje MIME multiparte con HTML y texto plano.
    """
    mensaje = MIMEMultipart('alternative')
    mensaje['To'] = destinatario
    mensaje['From'] = settings.DEFAULT_FROM_EMAIL
    mensaje['Subject'] = asunto

    if contenido_texto:
        parte_texto = MIMEText(contenido_texto, 'plain', 'utf-8')
        mensaje.attach(parte_texto)

    parte_html = MIMEText(contenido_html, 'html', 'utf-8')
    mensaje.attach(parte_html)

    mensaje_raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode('utf-8')
    return {'raw': mensaje_raw}


def enviar_email_html(destinatario, asunto, template_html, context, template_text=None):
    """
    Envía un email con contenido HTML usando Gmail API con OAuth2.
    """
    try:
        html_content = render_to_string(template_html, context)
        texto_content = context.get('mensaje_texto', '')
        if template_text:
            texto_content = render_to_string(template_text, context)

        mensaje = crear_mensaje_mime(
            destinatario=destinatario,
            asunto=asunto,
            contenido_html=html_content,
            contenido_texto=texto_content
        )

        service = get_gmail_service_oauth()
        resultado = service.users().messages().send(
            userId='me',
            body=mensaje
        ).execute()

        logger.info(f"Email enviado exitosamente a {destinatario}: {asunto} (ID: {resultado.get('id')})")
        return True

    except HttpError as error:
        logger.error(f"Error HTTP al enviar email a {destinatario}: {error}")
        return False
    except Exception as e:
        logger.error(f"Error al enviar email a {destinatario}: {e}")
        return False


def enviar_email_simple(destinatario, asunto, mensaje):
    """
    Envía un email simple sin template (texto plano).
    """
    try:
        mime_message = MIMEText(mensaje, 'plain', 'utf-8')
        mime_message['To'] = destinatario
        mime_message['From'] = settings.DEFAULT_FROM_EMAIL
        mime_message['Subject'] = asunto

        mensaje_raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode('utf-8')
        body = {'raw': mensaje_raw}

        service = get_gmail_service_oauth()
        resultado = service.users().messages().send(
            userId='me',
            body=body
        ).execute()

        logger.info(f"Email simple enviado a {destinatario}: {asunto} (ID: {resultado.get('id')})")
        return True

    except HttpError as error:
        logger.error(f"Error HTTP al enviar email simple a {destinatario}: {error}")
        return False
    except Exception as e:
        logger.error(f"Error al enviar email simple a {destinatario}: {e}")
        return False


# Importar funciones de notificación
# (Copiar las funciones enviar_notificacion_cambio_estado, enviar_recordatorio_pago, etc.
#  del email_service.py actual, ya que son iguales)
```

---

## 🔧 PASOS PARA IMPLEMENTAR GMAIL API

### **Paso 1: Configurar Google Cloud Platform**

1. **Ir a Google Cloud Console**: https://console.cloud.google.com/

2. **Crear/Seleccionar Proyecto**: `aprobado-web`

3. **Habilitar Gmail API**:
   - APIs & Services → Library
   - Buscar "Gmail API"
   - Click en "Enable"

4. **Configurar OAuth Consent Screen**:
   - APIs & Services → OAuth consent screen
   - Seleccionar **"External"** (para Gmail personal)
   - Completar:
     - App name: `Aprobado Email System`
     - User support email: Tu email
     - Developer contact: Tu email
   - **Scopes**: Agregar `https://www.googleapis.com/auth/gmail.send`
   - **Test users**: Agregar el email que enviará (ej: `medios.datain@gmail.com`)

5. **Crear Credenciales OAuth 2.0**:
   - APIs & Services → Credentials
   - Click "Create Credentials" → "OAuth client ID"
   - Application type: **"Desktop app"**
   - Name: `Aprobado Email Client`
   - Click "Create"
   - **Descargar el archivo JSON** (se llamará algo como `client_secret_XXX.json`)

6. **Guardar Credenciales**:
   ```bash
   # Renombrar y mover el archivo descargado
   mv ~/Downloads/client_secret_XXX.json C:\.vscode\Project_aprobado\config\gmail_oauth_credentials.json
   ```

### **Paso 2: Autorización Inicial**

Crear script de setup:

```python
# setup_gmail_oauth.py
import os
import django
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
django.setup()

from django.conf import settings

SCOPES = ['https://www.googleapis.com/auth/gmail.send']
TOKEN_FILE = os.path.join(settings.BASE_DIR, 'config', 'gmail_token.pickle')
CREDENTIALS_FILE = os.path.join(settings.BASE_DIR, 'config', 'gmail_oauth_credentials.json')

print("Iniciando autorización OAuth2...")
print("Se abrirá tu navegador. Autoriza la aplicación.")

flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
creds = flow.run_local_server(port=8080)

os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
with open(TOKEN_FILE, 'wb') as token:
    pickle.dump(creds, token)

print(f"✅ Token guardado en: {TOKEN_FILE}")
print("Ahora puedes usar Gmail API")
```

Ejecutar:
```bash
python setup_gmail_oauth.py
```

### **Paso 3: Actualizar Código**

1. **Renombrar archivos**:
   ```bash
   # Respaldar la versión SMTP
   mv gestion_creditos/email_service.py gestion_creditos/email_service_smtp.py

   # Crear la versión Gmail API
   # (usar el código de email_service_gmail_api.py de arriba)
   ```

2. **Actualizar settings.py**:
   ```python
   # Descomentar la sección de Gmail API
   GOOGLE_OAUTH_CREDENTIALS_FILE = os.path.join(BASE_DIR, 'config', 'gmail_oauth_credentials.json')
   DEFAULT_FROM_EMAIL = 'Aprobado <medios.datain@gmail.com>'
   ```

---

## 🔄 COMPARACIÓN: SMTP vs Gmail API

| Característica | SMTP (Actual) | Gmail API |
|----------------|---------------|-----------|
| **Facilidad** | ⭐⭐⭐⭐⭐ Muy fácil | ⭐⭐⭐ Moderado |
| **Configuración** | 5 minutos | 30 minutos |
| **Requisitos** | Contraseña de app | OAuth2 setup |
| **Límites** | 500 emails/día | 1,000,000,000 cuota/día |
| **Funcionalidades** | Solo envío | Envío, lectura, gestión |
| **Mantenimiento** | Ninguno | Refresh tokens |
| **Cuentas soportadas** | Gmail personal/Workspace | Gmail personal/Workspace |
| **Costo** | Gratis | Gratis |

---

## 📊 CUOTAS DE GMAIL API

- **Envío de emails**: 100 unidades por email
- **Cuota diaria**: 1,000,000,000 unidades
- **Estimado**: ~10,000,000 emails por día

Más que suficiente para cualquier aplicación en producción.

---

## 🔐 SEGURIDAD

### **Archivos que NO debes subir a Git**:

```gitignore
config/gmail_oauth_credentials.json
config/gmail_token.pickle
.env
```

---

## 🐛 TROUBLESHOOTING

### **Error: "invalid_grant"**
- **Causa**: El token expiró o fue revocado
- **Solución**: Elimina `gmail_token.pickle` y ejecuta `setup_gmail_oauth.py`

### **Error: "insufficient authentication scopes"**
- **Causa**: El scope no está autorizado
- **Solución**: Verifica que `https://www.googleapis.com/auth/gmail.send` esté en OAuth consent screen

### **Error: "Precondition check failed"**
- **Causa**: Usando Service Account sin Google Workspace
- **Solución**: Usa OAuth 2.0 (método 2) en lugar de Service Account

---

## ✅ VENTAJAS DE GMAIL API

1. **Mayor cuota de envío**: 10M emails/día vs 500/día con SMTP
2. **Más funcionalidades**: Leer emails, gestionar etiquetas, etc.
3. **Mejor monitoreo**: IDs de mensajes, estado de entrega
4. **Más robusto**: Menos probabilidad de ser bloqueado por Google
5. **Escalable**: Preparado para grandes volúmenes

---

## 📞 CUÁNDO MIGRAR DE SMTP A GMAIL API

Considera migrar cuando:
- ✅ Necesites enviar más de 500 emails por día
- ✅ Quieras funcionalidades avanzadas (lectura, etiquetas)
- ✅ Necesites mejor monitoreo y control
- ✅ Tengas Google Workspace (hace todo más fácil)

Por ahora, **SMTP es perfectamente adecuado** para tu caso de uso.

---

**Creado con ❤️ para Aprobado**
**Fecha:** 18/11/2025

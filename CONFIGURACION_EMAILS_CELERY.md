# 📧 GUÍA COMPLETA: CONFIGURACIÓN DE EMAILS Y CELERY

## 📋 RESUMEN DE LO IMPLEMENTADO

Se ha implementado un sistema completo de notificaciones automatizadas para tu plataforma de créditos, incluyendo:

### ✅ **1. Sistema de Emails con Gmail SMTP**
- Configuración de Django para enviar emails vía Gmail SMTP
- Servicio de notificaciones mejorado con templates HTML
- 7 tipos diferentes de emails automatizados
- Fácil configuración con contraseña de aplicación de Gmail

> **Nota**: Para una implementación más robusta con Gmail API, consulta [GMAIL_API_SETUP.md](GMAIL_API_SETUP.md)

### ✅ **2. Celery para Tareas Asíncronas**
- Sistema de tareas en segundo plano
- Tareas programadas con Celery Beat
- 3 tareas automáticas diarias

### ✅ **3. Correcciones al Código**
- Cálculo correcto de amortización
- Diferenciación entre `capital_pendiente` y `saldo_pendiente`
- Índices de base de datos para optimización
- Documentación mejorada

---

## 🚀 PASOS DE CONFIGURACIÓN

### **PASO 1: Instalar Dependencias**

```bash
pip install -r requirements.txt
```

**Nuevas dependencias agregadas:**
- `celery==5.4.0` - Framework para tareas asíncronas
- `redis==5.0.1` - Broker de mensajes para Celery
- `django-celery-beat==2.8.1` - Programador de tareas
- `google-api-python-client==2.162.0` - Cliente de Google API
- `google-auth-httplib2==0.2.0` - Autenticación HTTP para Google
- `google-auth-oauthlib==1.2.1` - OAuth2 para Google
- `google-auth==2.38.0` - Biblioteca de autenticación de Google

---

### **PASO 2: Crear Contraseña de Aplicación de Gmail**

Este es el método más simple y funciona perfectamente para cuentas Gmail personales.

#### **2.1. Activar Verificación en 2 Pasos**

1. Ve a tu cuenta de Gmail: https://myaccount.google.com/
2. En el menú izquierdo, haz clic en **"Seguridad"**
3. Busca **"Verificación en 2 pasos"** y actívala si no la tienes
4. Sigue los pasos para configurarla (número de teléfono, etc.)

#### **2.2. Crear Contraseña de Aplicación**

1. Una vez activada la verificación en 2 pasos, busca **"Contraseñas de aplicaciones"**
2. Haz clic en **"Contraseñas de aplicaciones"**
3. Es posible que te pida verificar tu identidad nuevamente
4. Completa:
   - **Selecciona la app**: Correo
   - **Selecciona el dispositivo**: Windows Computer (o el que prefieras)
5. Haz clic en **"Generar"**
6. Gmail te mostrará una contraseña de 16 caracteres (ejemplo: `abcd efgh ijkl mnop`)
7. **COPIA ESTA CONTRASEÑA** - solo se muestra una vez

#### **2.3. Configurar Variables de Entorno**

Crea un archivo `.env` en la raíz del proyecto:

```env
# ================================
# CONFIGURACIÓN DE EMAIL (Gmail SMTP)
# ================================

# Email de Gmail que enviará los correos
EMAIL_HOST_USER=medios.datain@gmail.com

# Contraseña de aplicación de Gmail (la que copiaste en el paso anterior)
EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop

# Email "From" que aparecerá en los correos
DEFAULT_FROM_EMAIL=Aprobado <medios.datain@gmail.com>

# ================================
# CONFIGURACIÓN DE CELERY/REDIS
# ================================

REDIS_URL=redis://localhost:6379/0
```

**IMPORTANTE**: Reemplaza:
- `medios.datain@gmail.com` con tu email de Gmail
- `abcd efgh ijkl mnop` con la contraseña de aplicación que generaste

#### **2.4. Proteger las Credenciales**

Agrega `.env` a `.gitignore` para no subir las credenciales a Git:

```bash
echo ".env" >> .gitignore
```

---

### **PASO 3: Instalar y Configurar Redis**

#### **En Windows:**

1. **Descargar Redis para Windows:**
   - Ir a: https://github.com/microsoftarchive/redis/releases
   - Descargar `Redis-x64-3.0.504.msi`
   - Instalar normalmente

2. **Iniciar Redis:**
   ```bash
   redis-server
   ```

   O instalar como servicio de Windows:
   ```bash
   redis-server --service-install
   redis-server --service-start
   ```

#### **En Linux/Mac:**

```bash
# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis
sudo systemctl enable redis

# Mac
brew install redis
brew services start redis
```

#### **Verificar que Redis funciona:**

```bash
redis-cli ping
# Debería responder: PONG
```

---

### **PASO 4: Aplicar Migraciones**

Los índices de base de datos y `django-celery-beat` requieren migraciones:

```bash
python manage.py makemigrations
python manage.py migrate
```

---

### **PASO 5: Iniciar Celery**

#### **5.1. Abrir 3 terminales diferentes:**

**Terminal 1 - Django (servidor web):**
```bash
python manage.py runserver
```

**Terminal 2 - Celery Worker (procesa tareas):**
```bash
celery -A aprobado_web worker -l info --pool=solo
```

> **Nota:** En Windows usa `--pool=solo`. En Linux/Mac puedes omitirlo.

**Terminal 3 - Celery Beat (programa tareas):**
```bash
celery -A aprobado_web beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

---

## 📅 TAREAS AUTOMÁTICAS CONFIGURADAS

Las siguientes tareas se ejecutan automáticamente:

### **1. Marcar Créditos en Mora**
- **Frecuencia:** Diariamente a las 6:00 AM
- **Función:** Marca automáticamente créditos activos con fecha vencida como "EN_MORA"
- **Archivo:** [gestion_creditos/tasks.py:21](gestion_creditos/tasks.py#L21)

### **2. Enviar Recordatorios de Pago**
- **Frecuencia:** Diariamente a las 8:00 AM
- **Función:** Envía recordatorios 7 y 3 días antes del vencimiento
- **Archivo:** [gestion_creditos/tasks.py:58](gestion_creditos/tasks.py#L58)

### **3. Enviar Alertas de Mora**
- **Frecuencia:** Diariamente a las 9:00 AM
- **Función:** Envía alertas escalonadas (días 1, 7, 15, 30, y cada 30 días)
- **Archivo:** [gestion_creditos/tasks.py:107](gestion_creditos/tasks.py#L107)

---

## 📧 TIPOS DE EMAILS AUTOMATIZADOS

Se envían emails automáticamente en los siguientes eventos:

1. **Solicitud Recibida** (`EN_REVISION`) - Confirmación de recepción
2. **Crédito Aprobado** (`APROBADO`) - Notificación de aprobación
3. **Crédito Rechazado** (`RECHAZADO`) - Información sobre rechazo
4. **Crédito Desembolsado** (`ACTIVO`) - Confirmación de desembolso
5. **Crédito en Mora** (`EN_MORA`) - Alerta de mora
6. **Recordatorio de Pago** - 3 y 7 días antes del vencimiento
7. **Confirmación de Pago** - Después de cada pago exitoso

---

## 🧪 PRUEBAS Y COMANDOS ÚTILES

### **Ejecutar Manualmente la Tarea de Marcar Moras:**

```bash
python manage.py marcar_moras
```

### **Probar Envío de Email:**

```bash
python manage.py shell
```

Luego en la consola de Python:

```python
from gestion_creditos.email_service import enviar_email_simple

# Prueba de email simple
enviar_email_simple(
    destinatario='tu-email@gmail.com',
    asunto='Prueba de Email desde Django con Gmail API',
    mensaje='Este es un email de prueba. Si lo recibes, ¡funciona!'
)
```

### **Ejecutar Tareas de Celery Manualmente:**

```bash
python manage.py shell
```

```python
from gestion_creditos.tasks import marcar_creditos_en_mora_task

# Ejecutar tarea inmediatamente
resultado = marcar_creditos_en_mora_task.delay()
print(resultado.get())
```

### **Ver Tareas Programadas en la BD:**

```bash
python manage.py shell
```

```python
from django_celery_beat.models import PeriodicTask

# Ver todas las tareas programadas
for task in PeriodicTask.objects.all():
    print(f"{task.name}: {task.enabled}")
```

---

## 🐛 TROUBLESHOOTING

### **Problema: Los emails no se envían**

1. Verifica que Gmail API esté habilitada en Google Cloud Console
2. Verifica que el archivo de credenciales existe y está en la ruta correcta:
   ```python
   python manage.py shell
   from django.conf import settings
   import os
   print(settings.GOOGLE_SERVICE_ACCOUNT_FILE)
   print(os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE))
   ```

3. Verifica que la Service Account tenga los permisos correctos
4. Revisa el log de Django para ver errores específicos
5. Si usas delegación de dominio, verifica que el Client ID esté autorizado en Google Workspace Admin

### **Problema: Error "insufficient authentication scopes"**

Esto significa que la Service Account no tiene los permisos necesarios. Verifica:

1. Que hayas habilitado Gmail API en tu proyecto de GCP
2. Que hayas configurado la delegación de dominio correctamente
3. Que el scope `https://www.googleapis.com/auth/gmail.send` esté autorizado

### **Problema: Celery no inicia**

1. Verifica que Redis esté corriendo:
   ```bash
   redis-cli ping
   ```

2. Revisa logs de Celery Worker/Beat para ver errores
3. En Windows, asegúrate de usar `--pool=solo`

### **Problema: Las tareas programadas no se ejecutan**

1. Verifica que Celery Beat esté corriendo
2. Revisa que las tareas estén registradas en la BD:
   ```bash
   python manage.py shell
   from django_celery_beat.models import PeriodicTask
   print(PeriodicTask.objects.count())
   ```

3. Si no hay tareas, ejecuta:
   ```bash
   python manage.py migrate django_celery_beat
   ```

---

## 📊 MONITOREO Y LOGS

### **Ver Logs de Celery:**

Los logs de Celery se muestran directamente en la terminal donde ejecutaste el worker/beat.

### **Ver Logs de Django:**

Revisa el archivo `logs/django.log` o la consola donde ejecutaste `runserver`.

### **Filtrar Logs por Módulo:**

```bash
# Ver solo logs de gestion_creditos
tail -f logs/django.log | grep "gestion_creditos"
```

---

## 🚀 PRÓXIMOS PASOS RECOMENDADOS

1. **Personalizar Templates de Email:**
   - Edita los archivos en `templates/emails/` para personalizarlos con tu marca
   - Agrega logos, colores corporativos, etc.

2. **Agregar Más Tareas Automáticas:**
   - Reportes mensuales automáticos
   - Notificaciones de cumpleaños
   - Promociones de nuevos productos

3. **Implementar Dashboard de Monitoreo:**
   - Usar Flower para monitorear Celery: `pip install flower`
   - Ejecutar: `celery -A aprobado_web flower`
   - Acceder a: http://localhost:5555

4. **Configurar en Producción:**
   - Usar un servidor Redis dedicado
   - Configurar Supervisor o systemd para mantener Celery corriendo
   - Considerar usar Cloud Pub/Sub o Cloud Tasks para tareas en GCP

---

## 🔐 SEGURIDAD

### **Proteger las Credenciales:**

1. **NUNCA** subas el archivo `google-service-account.json` a Git
2. Agrega el archivo a `.gitignore`:
   ```
   config/google-service-account.json
   .env
   ```

3. En producción, usa variables de entorno o servicios de secretos:
   - Google Cloud Secret Manager
   - AWS Secrets Manager
   - HashiCorp Vault

### **Permisos Mínimos:**

La Service Account solo debe tener el permiso `https://www.googleapis.com/auth/gmail.send` (enviar emails). No necesita más permisos.

---

## 📞 SOPORTE

Si tienes algún problema con la configuración:

1. Revisa los logs en la terminal de Celery
2. Verifica que todas las dependencias estén instaladas
3. Asegúrate de que Redis esté corriendo
4. Revisa las variables de entorno en `.env`
5. Verifica que Gmail API esté habilitada en Google Cloud Console

---

## ✅ CHECKLIST DE IMPLEMENTACIÓN

- [ ] Instalar dependencias (`pip install -r requirements.txt`)
- [ ] Crear proyecto en Google Cloud Platform
- [ ] Habilitar Gmail API
- [ ] Crear Service Account
- [ ] Descargar credenciales JSON
- [ ] Configurar delegación de dominio (si usas Google Workspace)
- [ ] Colocar archivo de credenciales en `config/google-service-account.json`
- [ ] Crear archivo `.env` con variables de entorno
- [ ] Instalar e iniciar Redis
- [ ] Aplicar migraciones (`python manage.py migrate`)
- [ ] Iniciar Django (`python manage.py runserver`)
- [ ] Iniciar Celery Worker
- [ ] Iniciar Celery Beat
- [ ] Probar envío de email
- [ ] Ejecutar comando `marcar_moras` manualmente
- [ ] Verificar que las tareas programadas se ejecuten

---

## 🔄 DIFERENCIAS ENTRE SMTP Y GMAIL API

### **¿Por qué Gmail API en lugar de SMTP?**

1. **Mayor robustez**: Menos probabilidad de ser bloqueado por Google
2. **Mejor rendimiento**: API nativa optimizada
3. **Más control**: Acceso a todas las funcionalidades de Gmail
4. **Seguridad mejorada**: OAuth2 en lugar de contraseñas de aplicación
5. **Escalabilidad**: Mejor para grandes volúmenes de emails

### **Cuotas de Gmail API:**

- **Límite diario**: 1,000,000,000 unidades de cuota por día
- **Envío de emails**: 100 unidades por email
- **Estimado**: ~10,000,000 emails por día (más que suficiente para producción)

---

**¡FELICITACIONES! 🎉**

Ahora tienes un sistema completo de notificaciones automatizadas funcionando con **Gmail API de Google Cloud Platform**. Los créditos se marcarán automáticamente en mora, se enviarán recordatorios de pago, y tus clientes recibirán notificaciones en cada etapa de su crédito.

---

**Creado con ❤️ para Aprobado**
**Última actualización:** 18/11/2025

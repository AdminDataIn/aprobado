# ⚡ INICIO RÁPIDO: Configuración de Emails en 5 Minutos

## 📋 Lo que Necesitas

- ✅ Cuenta de Gmail (ej: `medios.datain@gmail.com`)
- ✅ 5 minutos de tu tiempo

---

## 🚀 Pasos Rápidos

### **1. Crear Contraseña de Aplicación de Gmail** (2 minutos)

1. Ve a: https://myaccount.google.com/security
2. Activa **"Verificación en 2 pasos"** (si no la tienes)
3. Busca **"Contraseñas de aplicaciones"**
4. Crea una para "Correo" en "Windows Computer"
5. **Copia la contraseña de 16 caracteres** (ej: `abcd efgh ijkl mnop`)

### **2. Crear Archivo `.env`** (1 minuto)

Crea el archivo `C:\.vscode\Project_aprobado\.env` con este contenido:

```env
EMAIL_HOST_USER=medios.datain@gmail.com
EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop
DEFAULT_FROM_EMAIL=Aprobado <medios.datain@gmail.com>
REDIS_URL=redis://localhost:6379/0
```

**Reemplaza**:
- `medios.datain@gmail.com` → Tu email de Gmail
- `abcd efgh ijkl mnop` → La contraseña que copiaste

### **3. Probar** (1 minuto)

```bash
python test_email.py
```

Ingresa tu email cuando te lo pida y ¡listo! Deberías recibir el email de prueba.

---

## ✅ ¿Funcionó?

Si recibiste el email, **¡felicitaciones!** El sistema está listo.

Ahora todos los emails automáticos funcionarán:
- ✉️ Notificaciones de crédito aprobado
- ✉️ Recordatorios de pago
- ✉️ Alertas de mora
- ✉️ Confirmaciones de pago

---

## ❌ ¿No Funcionó?

### **Error: "Authentication failed"**
- Verifica que la contraseña de aplicación sea correcta (16 caracteres)
- Asegúrate de que la verificación en 2 pasos esté activa

### **Error: "SMTPServerDisconnected"**
- Verifica tu conexión a internet
- Asegúrate de que el puerto 587 no esté bloqueado

### **El email no llega**
- Revisa la carpeta de SPAM
- Espera unos minutos (a veces tarda)
- Verifica que el email destino sea correcto

---

## 📚 Más Información

- **Documentación completa**: [CONFIGURACION_EMAILS_CELERY.md](CONFIGURACION_EMAILS_CELERY.md)
- **Gmail API (avanzado)**: [GMAIL_API_SETUP.md](GMAIL_API_SETUP.md)

---

**¿Todo listo? Ahora configura Celery para emails automáticos:**

```bash
# Terminal 1
python manage.py runserver

# Terminal 2
celery -A aprobado_web worker -l info --pool=solo

# Terminal 3
celery -A aprobado_web beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

¡Eso es todo! 🎉

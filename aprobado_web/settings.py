from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ========================
# Seguridad y Debug
# ========================

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key')  # clave de respaldo para local

# Si existe la variable RENDER, asumimos que estamos en producción
DEBUG = 'RENDER' not in os.environ

def _split_env_list(name, default=''):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]

PRIMARY_DOMAIN_HOST = os.environ.get('PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
EMPRENDER_SUBDOMAIN_HOST = os.environ.get('EMPRENDER_SUBDOMAIN_HOST', 'emprender.aprobado.com.co')
MARKET_SUBDOMAIN_HOST = os.environ.get('MARKET_SUBDOMAIN_HOST', 'market.aprobado.com.co')

ALLOWED_HOSTS = _split_env_list(
    'ALLOWED_HOSTS',
    (
        f'{PRIMARY_DOMAIN_HOST},'
        f'www.{PRIMARY_DOMAIN_HOST},'
        f'{EMPRENDER_SUBDOMAIN_HOST},'
        f'{MARKET_SUBDOMAIN_HOST},'
        '127.0.0.1,localhost,'
        '.onrender.com,aprobado-proj.onrender.com'
    )
)

CSRF_TRUSTED_ORIGINS = _split_env_list(
    'CSRF_TRUSTED_ORIGINS',
    (
        'https://aprobado.com.co,'
        'https://www.aprobado.com.co,'
        'https://emprender.aprobado.com.co,'
        'https://market.aprobado.com.co'
    )
)

USE_X_FORWARDED_HOST = True

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MANUAL_PAYMENT_AUTH_KEY = os.environ.get('MANUAL_PAYMENT_AUTH_KEY', 'clave-secreta-para-desarrollo')

# ========================
# Aplicaciones
# ========================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.humanize',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'django_celery_beat',  # Para tareas programadas con Celery
    'usuarios',
    'configuraciones',
    'gestion_creditos',
    'usuariocreditos',
]

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SITE_ID = 1

# ========================================
# Django Allauth - Autenticación
# ========================================
LOGIN_URL = '/accounts/google/login/'
LOGIN_REDIRECT_URL = '/emprendimiento/solicitar/'  # NUEVA URL - Redirige a solicitud de emprendimiento
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_LOGOUT_REDIRECT_URL = '/'  # NUEVA URL - Redirige a home (landing emprendimiento)
SOCIALACCOUNT_AUTO_SIGNUP = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}

ACCOUNT_ADAPTER = 'usuarios.adapter.AccountAdapter'

if DEBUG:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'aprobado_web.middleware.SubdomainRoutingMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'usuarios.middleware.ProductoContextMiddleware',  # Detecta producto (libranza/emprendimiento) por URL - DEBE ir después de AuthenticationMiddleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
]

ROOT_URLCONF = 'aprobado_web.urls_main'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'usuarios.context_processors.user_groups_processor',
                'usuarios.context_processors.notificaciones_processor',
                'usuarios.context_processors.producto_context_processor',
            ],
        },
    },
]

WSGI_APPLICATION = 'aprobado_web.wsgi.application'

# ========================
# Bases de datos
# ========================

if DEBUG:
    # En local usamos SQLite para no depender de Render
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    # En producción usamos la URL de Render
    DATABASES = {
        'default': dj_database_url.config(default=os.environ.get('DATABASE_URL'))
    }

# ========================
# Validación de contraseñas
# ========================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ========================
# Internacionalización
# ========================

LANGUAGE_CODE = 'es-CO'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

# ========================
# Archivos estáticos
# ========================

STATIC_URL = 'static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT = '/var/www/aprobado/staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ========================
# Seguridad
# ========================

SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
# Configuración de Email (Gmail SMTP)
# ========================

# Backend de email - usando SMTP de Gmail
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'medios.datain@gmail.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')  # Contraseña de aplicación de Gmail
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', f'Aprobado <{EMAIL_HOST_USER}>')
SERVER_EMAIL = EMAIL_HOST_USER

# ========================
# Configuración de WOMPI (Pasarela de Pagos)
# ========================
WOMPI_PUBLIC_KEY = os.environ.get('WOMPI_PUBLIC_KEY', 'pub_test_xxxxx')
WOMPI_PRIVATE_KEY = os.environ.get('WOMPI_PRIVATE_KEY', 'priv_test_xxxxx')
WOMPI_INTEGRITY_KEY = os.environ.get('WOMPI_INTEGRITY_KEY', 'int_test_xxxxx')
WOMPI_EVENTS_SECRET = os.environ.get('WOMPI_EVENTS_SECRET', 'evt_test_xxxxx')
WOMPI_ENVIRONMENT = os.environ.get('WOMPI_ENVIRONMENT', 'sandbox')  # 'sandbox' o 'production'

# URL base se calcula automáticamente según el ambiente
WOMPI_API_BASE_URL = (
    'https://sandbox.wompi.co/v1'
    if WOMPI_ENVIRONMENT == 'sandbox'
    else 'https://production.wompi.co/v1'
)

# Controles de duplicidad y rate limiting para WOMPI
WOMPI_DUPLICATE_COOLDOWN_SECONDS = int(os.environ.get('WOMPI_DUPLICATE_COOLDOWN_SECONDS', '300'))
WOMPI_DUPLICATE_WINDOW_MINUTES = int(os.environ.get('WOMPI_DUPLICATE_WINDOW_MINUTES', '10'))
WOMPI_RATE_LIMIT_ATTEMPTS = int(os.environ.get('WOMPI_RATE_LIMIT_ATTEMPTS', '3'))
WOMPI_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('WOMPI_RATE_LIMIT_WINDOW_SECONDS', '60'))

# ========================
# Seguridad
# ========================
# Permite previsualizar PDFs internos en iframes (mismo origen)
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Cache (usar Redis si esta disponible)
REDIS_URL = os.environ.get('REDIS_URL', '')
if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'aprobado-cache',
        }
    }

# ========================
# Configuración de ZapSign (Firma Electrónica de Pagarés)
# ========================
ZAPSIGN_API_TOKEN = os.environ.get('ZAPSIGN_API_TOKEN', '')
ZAPSIGN_WEBHOOK_SECRET = os.environ.get('ZAPSIGN_WEBHOOK_SECRET', '')
ZAPSIGN_WEBHOOK_HEADER = os.environ.get('ZAPSIGN_WEBHOOK_HEADER', 'X-ZapSign-Secret')
ZAPSIGN_ENVIRONMENT = os.environ.get('ZAPSIGN_ENVIRONMENT', 'sandbox')  # 'sandbox' o 'production'

# Configuración del dominio público para URLs de descarga de PDFs
SITE_DOMAIN = os.environ.get('SITE_DOMAIN', 'localhost:8000')
SITE_HTTPS = os.environ.get('SITE_HTTPS', 'False').lower() == 'true'

# ========================
# Configuración de Email con Gmail API (COMENTADO - Para uso futuro)
# ========================
# Para implementar Gmail API en el futuro, consulta: GMAIL_API_SETUP.md
#
# GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
#     'GOOGLE_SERVICE_ACCOUNT_FILE',
#     os.path.join(BASE_DIR, 'config', 'google-service-account.json')
# )
# DEFAULT_FROM_EMAIL = 'Aprobado <aprobado-email-service@aprobado-web.iam.gserviceaccount.com>'
# SERVER_EMAIL = DEFAULT_FROM_EMAIL
# GMAIL_DELEGATED_USER = os.environ.get('GMAIL_DELEGATED_USER', 'tu-email@tudominio.com')

# ========================
# Configuración de Celery
# ========================

# URL de Redis (usar Redis como broker y backend)
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Configuración adicional de Celery
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'America/Bogota'
CELERY_ENABLE_UTC = False

# Configuración de Celery Beat (tareas programadas)
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

from pathlib import Path
import importlib.util
import os
import socket
import sys
from urllib.parse import urlparse

try:
    import dj_database_url
except ModuleNotFoundError:
    dj_database_url = None

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

# Cargar variables de entorno desde .env
if load_dotenv is not None:
    load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ========================
# Seguridad y Debug
# ========================

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key')  # clave de respaldo para local

# Si existe la variable RENDER, asumimos que estamos en producción
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'
RUNNING_TESTS = 'test' in sys.argv


def _module_available(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


ALLAUTH_AVAILABLE = _module_available('allauth')
ALLAUTH_ACCOUNT_AVAILABLE = _module_available('allauth.account')
ALLAUTH_SOCIALACCOUNT_AVAILABLE = _module_available('allauth.socialaccount')
ALLAUTH_GOOGLE_AVAILABLE = _module_available('allauth.socialaccount.providers.google')
DJANGO_CELERY_BEAT_AVAILABLE = _module_available('django_celery_beat')
WHITENOISE_AVAILABLE = _module_available('whitenoise')

def env_bool(name, default=False):
    value = os.environ.get(name, str(default))
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'si', 'sí')

def _split_env_list(name, default=''):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def _is_local_redis_url(redis_url):
    if not redis_url:
        return False
    parsed = urlparse(redis_url)
    return (parsed.hostname or '').lower() in {'localhost', '127.0.0.1'}


def _can_open_redis_socket(redis_url, timeout=0.15):
    if not redis_url:
        return False
    parsed = urlparse(redis_url)
    host = parsed.hostname
    port = parsed.port or 6379
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

PRIMARY_DOMAIN_HOST = os.environ.get('PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
EMPRENDER_SUBDOMAIN_HOST = os.environ.get('EMPRENDER_SUBDOMAIN_HOST', 'emprender.aprobado.com.co')
MARKET_SUBDOMAIN_HOST = os.environ.get('MARKET_SUBDOMAIN_HOST', 'market.aprobado.com.co')
CONTRACTORS_PORTAL_HOST = os.environ.get('CONTRACTORS_PORTAL_HOST', 'contratistas.aprobado.com.co')
CONTRACTORS_LOCAL_HOST = os.environ.get('CONTRACTORS_LOCAL_HOST', 'contratistas.localhost')
CONTRACTORS_PORTAL_HOSTS = _split_env_list(
    'CONTRACTORS_PORTAL_HOSTS',
    f'{CONTRACTORS_PORTAL_HOST},{CONTRACTORS_LOCAL_HOST}',
)
CONTACT_EMAIL = os.environ.get(
    'CONTACT_EMAIL',
    'Info@aprobado.com.co'
)
EMAIL_QA_MODE = env_bool('EMAIL_QA_MODE', False)
EMAIL_QA_REDIRECT_TO = os.environ.get('EMAIL_QA_REDIRECT_TO', '').strip()
EMAIL_QA_SUBJECT_PREFIX = os.environ.get('EMAIL_QA_SUBJECT_PREFIX', '[QA]')

# Correos internos para alertas operativas del flujo de credito.
# Formato esperado en .env:
# CREDIT_INTERNAL_NOTIFICATION_EMAILS=correo1@dominio.com,correo2@dominio.com
CREDIT_INTERNAL_NOTIFICATION_EMAILS = _split_env_list(
    'CREDIT_INTERNAL_NOTIFICATION_EMAILS',
    CONTACT_EMAIL
)

# Tasas mensuales parametrizables por linea de credito.
# Se usan en simuladores, pagare y activacion financiera.
LIBRANZA_TASA_MENSUAL = os.environ.get('LIBRANZA_TASA_MENSUAL', '1.9')
EMPRENDIMIENTO_TASA_MENSUAL = os.environ.get('EMPRENDIMIENTO_TASA_MENSUAL', '3.5')
ADELANTO_NOMINA_TASA_MENSUAL = os.environ.get('ADELANTO_NOMINA_TASA_MENSUAL', '1.9')
ADELANTO_NOMINA_COMISION_PERCENT = os.environ.get('ADELANTO_NOMINA_COMISION_PERCENT', '10')
ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE = os.environ.get('ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE', '25')
ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING = env_bool(
    'ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING',
    False
)
LIBRANZA_AUTO_MARK_MORA_ENABLED = env_bool('LIBRANZA_AUTO_MARK_MORA_ENABLED', True)
LIBRANZA_PAYMENT_REMINDERS_ENABLED = env_bool('LIBRANZA_PAYMENT_REMINDERS_ENABLED', False)
LIBRANZA_MORA_ALERTS_ENABLED = env_bool('LIBRANZA_MORA_ALERTS_ENABLED', False)
PAGADOR_MONTHLY_PENDING_NOTIFICATIONS_ENABLED = env_bool('PAGADOR_MONTHLY_PENDING_NOTIFICATIONS_ENABLED', True)
PAGARE_TEMPLATE_VERSION = os.environ.get('PAGARE_TEMPLATE_VERSION', '1.0')
WORKER_PENDING_PAYMENT_ALERTS_ENABLED = env_bool('WORKER_PENDING_PAYMENT_ALERTS_ENABLED', True)
LIBRANZA_PRORATED_INTEREST_ENABLED = env_bool('LIBRANZA_PRORATED_INTEREST_ENABLED', False)
WHATSAPP_SUPPORT_NUMBER = os.environ.get('WHATSAPP_SUPPORT_NUMBER', '573132477352')
WHATSAPP_DEFAULT_MESSAGE = os.environ.get(
    'WHATSAPP_DEFAULT_MESSAGE',
    'Hola, necesito ayuda con Aprobado.'
)
WHATSAPP_FLOATING_ENABLED = env_bool('WHATSAPP_FLOATING_ENABLED', True)
ZAPSIGN_SEND_LOCAL_EMAIL = env_bool('ZAPSIGN_SEND_LOCAL_EMAIL', False)
ZAPSIGN_SEND_COPY_EMAILS = env_bool('ZAPSIGN_SEND_COPY_EMAILS', False)

ALLOWED_HOSTS = _split_env_list(
    'ALLOWED_HOSTS',
    (
        f'{PRIMARY_DOMAIN_HOST},'
        f'www.{PRIMARY_DOMAIN_HOST},'
        f'{EMPRENDER_SUBDOMAIN_HOST},'
        f'{MARKET_SUBDOMAIN_HOST},'
        f'{CONTRACTORS_PORTAL_HOST},'
        f'{CONTRACTORS_LOCAL_HOST},'
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
        'https://market.aprobado.com.co,'
        'https://contratistas.aprobado.com.co'
    )
)

USE_X_FORWARDED_HOST = True

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CONTRACTORS_CONTRACT_AI_ENABLED = os.environ.get('CONTRACTORS_CONTRACT_AI_ENABLED', 'False').lower() in ('1', 'true', 'yes')
CONTRACTORS_CONTRACT_AI_MODEL = os.environ.get('CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4.1-mini')
CONTRACTORS_ALLOW_ID_UPLOAD_FALLBACK = os.environ.get('CONTRACTORS_ALLOW_ID_UPLOAD_FALLBACK', 'False').lower() in ('1', 'true', 'yes')
MANUAL_PAYMENT_AUTH_KEY = os.environ.get('MANUAL_PAYMENT_AUTH_KEY', 'clave-secreta-para-desarrollo')
MANUAL_PAYMENT_ROUNDING_TOLERANCE = os.environ.get('MANUAL_PAYMENT_ROUNDING_TOLERANCE', '100.00')

# DataCredito permanece apagado salvo habilitacion explicita de ambos interruptores.
DATACREDITO_ENABLED = env_bool('DATACREDITO_ENABLED', False)
DATACREDITO_REAL_ENABLED = env_bool('DATACREDITO_REAL_ENABLED', False)
DATACREDITO_ENVIRONMENT = os.environ.get('DATACREDITO_ENVIRONMENT', 'uat').strip().lower()
DATACREDITO_DEFAULT_SERVICE = os.environ.get('DATACREDITO_DEFAULT_SERVICE', 'decisor').strip().lower()
DATACREDITO_TIMEOUT_SECONDS = int(os.environ.get('DATACREDITO_TIMEOUT_SECONDS', '15'))
DATACREDITO_REUSE_DAYS = int(os.environ.get('DATACREDITO_REUSE_DAYS', '30'))
DATACREDITO_IN_PROGRESS_MINUTES = int(os.environ.get('DATACREDITO_IN_PROGRESS_MINUTES', '5'))
DATACREDITO_DOCUMENT_HASH_SECRET = os.environ.get('DATACREDITO_DOCUMENT_HASH_SECRET', '').strip()
DATACREDITO_AUTHORIZATION_TEXT_VERSION = os.environ.get(
    'DATACREDITO_AUTHORIZATION_TEXT_VERSION', ''
).strip()
DATACREDITO_AUTHORIZATION_TEXT = os.environ.get('DATACREDITO_AUTHORIZATION_TEXT', '').strip()
DATACREDITO_TOKEN_URL = os.environ.get('DATACREDITO_TOKEN_URL', '').strip()
DATACREDITO_REVOKE_TOKEN_URL = os.environ.get('DATACREDITO_REVOKE_TOKEN_URL', '').strip()
DATACREDITO_MIDECISOR_URL = os.environ.get('DATACREDITO_MIDECISOR_URL', '').strip()
DATACREDITO_HISTORIAL_URL = os.environ.get('DATACREDITO_HISTORIAL_URL', '').strip()
DATACREDITO_DECISOR_CLIENT_ID = os.environ.get('DATACREDITO_DECISOR_CLIENT_ID', '').strip()
DATACREDITO_DECISOR_CLIENT_SECRET = os.environ.get('DATACREDITO_DECISOR_CLIENT_SECRET', '').strip()
DATACREDITO_DECISOR_TOKEN_USERNAME = os.environ.get('DATACREDITO_DECISOR_TOKEN_USERNAME', '').strip()
DATACREDITO_DECISOR_TOKEN_PASSWORD = os.environ.get('DATACREDITO_DECISOR_TOKEN_PASSWORD', '').strip()
DATACREDITO_HDC_CLIENT_ID = os.environ.get('DATACREDITO_HDC_CLIENT_ID', '').strip()
DATACREDITO_HDC_CLIENT_SECRET = os.environ.get('DATACREDITO_HDC_CLIENT_SECRET', '').strip()
DATACREDITO_HDC_TOKEN_USERNAME = os.environ.get('DATACREDITO_HDC_TOKEN_USERNAME', '').strip()
DATACREDITO_HDC_TOKEN_PASSWORD = os.environ.get('DATACREDITO_HDC_TOKEN_PASSWORD', '').strip()
DATACREDITO_HDC_SERVICE_USER = os.environ.get('DATACREDITO_HDC_SERVICE_USER', '').strip()
DATACREDITO_HDC_SERVICE_PASSWORD = os.environ.get('DATACREDITO_HDC_SERVICE_PASSWORD', '').strip()
DATACREDITO_HDC_PRODUCT_ID = os.environ.get('DATACREDITO_HDC_PRODUCT_ID', '64').strip()
DATACREDITO_HDC_INFO_ACCOUNT_TYPE = os.environ.get('DATACREDITO_HDC_INFO_ACCOUNT_TYPE', '1').strip()
DATACREDITO_HDC_SERVER_IP_ADDRESS = os.environ.get('DATACREDITO_HDC_SERVER_IP_ADDRESS', '').strip()
DATACREDITO_HDC_CHANNEL_NAME = os.environ.get('DATACREDITO_HDC_CHANNEL_NAME', 'Canal-01').strip()
DATACREDITO_HDC_CHANNEL_TYPE = os.environ.get('DATACREDITO_HDC_CHANNEL_TYPE', '42').strip()
DATACREDITO_HDC_PARAMETERS_JSON = os.environ.get('DATACREDITO_HDC_PARAMETERS_JSON', '').strip()

# Compatibilidad de lectura; no habilita consumo por si sola.
DATACREDITO_CLIENT_ID = os.environ.get('DATACREDITO_CLIENT_ID', '').strip()
DATACREDITO_CLIENT_SECRET = os.environ.get('DATACREDITO_CLIENT_SECRET', '').strip()
DATACREDITO_USERNAME = os.environ.get('DATACREDITO_USERNAME', '').strip()
DATACREDITO_PASSWORD = os.environ.get('DATACREDITO_PASSWORD', '').strip()
DATACREDITO_API_PASSWORD = os.environ.get('DATACREDITO_API_PASSWORD', '').strip()
DATACREDITO_PRODUCT_ID = os.environ.get('DATACREDITO_PRODUCT_ID', '').strip()
DATACREDITO_INFO_ACCOUNT_TYPE = os.environ.get('DATACREDITO_INFO_ACCOUNT_TYPE', '1').strip()
DATACREDITO_SERVER_IP_ADDRESS = os.environ.get('DATACREDITO_SERVER_IP_ADDRESS', '').strip()

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
    'usuarios',
    'configuraciones',
    'gestion_creditos',
    'usuariocreditos',
    'contractors',
    'integrations',
]

if ALLAUTH_AVAILABLE:
    INSTALLED_APPS.append('allauth')
if ALLAUTH_ACCOUNT_AVAILABLE:
    INSTALLED_APPS.append('allauth.account')
if ALLAUTH_SOCIALACCOUNT_AVAILABLE:
    INSTALLED_APPS.append('allauth.socialaccount')
if ALLAUTH_GOOGLE_AVAILABLE:
    INSTALLED_APPS.append('allauth.socialaccount.providers.google')
if DJANGO_CELERY_BEAT_AVAILABLE:
    INSTALLED_APPS.append('django_celery_beat')

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

if ALLAUTH_ACCOUNT_AVAILABLE:
    AUTHENTICATION_BACKENDS.append(
        'allauth.account.auth_backends.AuthenticationBackend'
    )

SITE_ID = 1

# ========================================
# Django Allauth - Autenticación
# ========================================
LOGIN_URL = '/auth/login/'
LOGIN_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'optional'

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}

ACCOUNT_ADAPTER = 'usuarios.adapter.AccountAdapter'
SOCIALACCOUNT_ADAPTER = 'usuarios.adapter.CustomSocialAccountAdapter'
ACCOUNT_UNIQUE_EMAIL = True

if DEBUG:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'aprobado_web.middleware.SubdomainRoutingMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'usuarios.middleware.ProductoContextMiddleware',  # Detecta producto con auth/messages ya disponibles
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if ALLAUTH_ACCOUNT_AVAILABLE:
    MIDDLEWARE.append('allauth.account.middleware.AccountMiddleware')
if WHITENOISE_AVAILABLE:
    MIDDLEWARE.append('whitenoise.middleware.WhiteNoiseMiddleware')

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
                'usuarios.context_processors.public_whatsapp_processor',
            ],
        },
    },
]

WSGI_APPLICATION = 'aprobado_web.wsgi.application'

# ========================
# Bases de datos
# ========================

USE_SQLITE = os.environ.get('USE_SQLITE', '').lower() == 'true'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

if USE_SQLITE or not DATABASE_URL or dj_database_url is None:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': dj_database_url.config(default=DATABASE_URL)
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
if WHITENOISE_AVAILABLE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', os.path.join(BASE_DIR, 'media'))

# ========================
# Seguridad
# ========================

SECURE_SSL_REDIRECT = not DEBUG and not RUNNING_TESTS
SESSION_COOKIE_SECURE = not DEBUG and not RUNNING_TESTS
CSRF_COOKIE_SECURE = not DEBUG and not RUNNING_TESTS

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
# Configuración de Email (Gmail SMTP)
# ========================

# Backend de email - usando SMTP de Gmail
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'aprobado_web.email_backends.SafeRoutingEmailBackend')
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'noreply@aprobado.com.co')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')  # Contraseña de aplicación de Gmail
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Aprobado <noreply@aprobado.com.co>')
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', EMAIL_HOST_USER)

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
ALLOW_LOCAL_REDIS_FALLBACK = env_bool('ALLOW_LOCAL_REDIS_FALLBACK', True)
USE_LOCAL_REDIS_FALLBACK = ALLOW_LOCAL_REDIS_FALLBACK and _is_local_redis_url(REDIS_URL) and not _can_open_redis_socket(REDIS_URL)
if REDIS_URL and not USE_LOCAL_REDIS_FALLBACK:
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
ZAPSIGN_AUTH_MODE = os.environ.get('ZAPSIGN_AUTH_MODE', 'assinaturaTela')
ZAPSIGN_SEND_AUTOMATIC_EMAIL = env_bool('ZAPSIGN_SEND_AUTOMATIC_EMAIL', True)

# Para desactivar funciones avanzadas
ZAPSIGN_ENABLE_SELFIE_VALIDATION = env_bool('ZAPSIGN_ENABLE_SELFIE_VALIDATION', False)
ZAPSIGN_SELFIE_VALIDATION_TYPE = os.environ.get('ZAPSIGN_SELFIE_VALIDATION_TYPE', 'identity-verification')


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
if DJANGO_CELERY_BEAT_AVAILABLE:
    CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'


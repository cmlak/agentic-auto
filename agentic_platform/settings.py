import os
import environ
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False)
)

# Take environment variables from .env file
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY')

# FIX: Explicitly cast DEBUG from a string to a real Python Boolean
DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = [
    'agentic-platform-521063372903.asia-southeast1.run.app',
    'localhost',
    '127.0.0.1',
    '.localhost',  # Allows any subdomain locally, like cckt.localhost
    '.cambodiasmeprojects.com',
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = [
    'https://agentic-platform-521063372903.asia-southeast1.run.app',
    'https://*.cloudshell.dev',
    'https://*.cambodiasmeprojects.com',
]

# Share session and CSRF cookies across all subdomains (Multi-tenant authentication)
if not DEBUG:
    SESSION_COOKIE_DOMAIN = '.cambodiasmeprojects.com'
    CSRF_COOKIE_DOMAIN = '.cambodiasmeprojects.com'
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'
else:
    # Allow login from 127.0.0.1 by not restricting the cookie domain locally
    SESSION_COOKIE_DOMAIN = None
    CSRF_COOKIE_DOMAIN = None

# ==============================================================================
# MULTI-TENANCY APP CONFIGURATION
# ==============================================================================

# 1. SHARED APPS (Lives in the 'public' schema, shared by everyone)
SHARED_APPS = (
    'django_tenants',  # Mandatory: must be first
    'clients',         # Holds Client/Domain models
    'portal',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third Party Shared
    'storages',  
    'crispy_forms',
    'crispy_bootstrap5',
    'import_export',

    # Custom Shared Apps (Authentication, User Management, etc.)
    'register',
)

# 2. TENANT APPS (Lives in each isolated client schema like 'client_a', 'client_b')
TENANT_APPS = (
    'simple_history',  # Optional: For audit trails

    # Custom Tenant Apps (Accounting Ledgers, Invoices, Tools)
    'document',
    'tools',
    'cash',
    'account',
    'sale',
    'assets',
)

# 3. COMBINE APPS FOR DJANGO
INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

# 4. TENANT SETTINGS
TENANT_MODEL = "clients.Client"       # Point to your new Tenant model
TENANT_DOMAIN_MODEL = "clients.Domain" # Point to your new Domain model

DATABASE_ROUTERS = (
    'django_tenants.routers.TenantSyncRouter',
)

# ==============================================================================
# MIDDLEWARE
# ==============================================================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', 
    
    # CRITICAL: Tenant middleware must be at the top, after security/whitenoise
    'django_tenants.middleware.main.TenantMainMiddleware',
    
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'agentic_platform.urls'

# --- TEMPLATE FIX ---
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'templates'), 
            os.path.join(BASE_DIR, 'register', 'templates', 'register'),
            os.path.join(BASE_DIR, 'tools', 'templates', 'tools'),
            os.path.join(BASE_DIR, 'cash', 'templates', 'cash'),
            os.path.join(BASE_DIR, 'account', 'templates', 'account'),
            os.path.join(BASE_DIR, 'sale', 'templates', 'sale'),
            os.path.join(BASE_DIR, 'portal', 'templates', 'portal'),
            # os.path.join(BASE_DIR, 'document', 'templates'),
            os.path.join(BASE_DIR, 'document', 'templates', 'document'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'register.context_processors.user_info',
            ],
        },
    },
]

WSGI_APPLICATION = 'agentic_platform.wsgi.application'

# ==============================================================================
# DATABASE CONFIGURATION
# ==============================================================================
# Parse the URL from your .env
DATABASES = {
    'default': env.db('DATABASE_URL')
}

# CRITICAL OVERRIDE: django-tenants requires a specific PostgreSQL backend wrapper.
DATABASES['default']['ENGINE'] = 'django_tenants.postgresql_backend'

# Apply connection pooling
DATABASES['default']['CONN_MAX_AGE'] = 600

# ==============================================================================
# STORAGE & STATIC FILES
# ==============================================================================
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

if not DEBUG:
    # --- PRODUCTION (Cloud Run + GCS) ---
    GS_BUCKET_NAME = 'agentic-media-files'
    GS_QUERYSTRING_AUTH = False 
    GS_DEFAULT_ACL = None

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage", 
        },
    }
else:
    # --- LOCAL DEVELOPMENT ---
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Bangkok' 
USE_I18N = True
USE_TZ = True

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ==============================================================================
# CELERY PRODUCTION SETTINGS (UPSTASH REDIS COMPATIBLE)
# ==============================================================================

# 1. Base Connection URLs 
# (Now pulling the complete, parameterized rediss:// strings directly from the OS environment)
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND')

# 2. Cloud Infrastructure & Task Serialization Tuning
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIME_ZONE = 'Asia/Phnom_Penh'

# 3. Connection Pool Management (Optimized for Serverless Cloud Run)
# Prevents container tasks from exhausting connection sockets on Upstash
CELERY_REDIS_MAX_CONNECTIONS = 20
CELERY_BROKER_POOL_LIMIT = 10

# 4. Execution Visibility Logging
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_LOG_FMT = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'

# Increase the maximum number of fields allowed in a GET/POST request.
# Useful for large Django formsets (e.g., bulk AI invoice processing).
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # Default is 1000

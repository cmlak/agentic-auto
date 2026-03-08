import os
import environ
from pathlib import Path

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

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'document',
    'storages',  
    'register',
    'tools',
    'crispy_forms',
    'crispy_bootstrap4',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', 
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
            # Path 1: Your main project templates
            os.path.join(BASE_DIR, 'templates'), 
            
            # Path 2: Your explicit app templates (Optional, but safe to keep)
            os.path.join(BASE_DIR, 'register', 'templates', 'register'),

            # Path 3: Your explicit app templates (Optional, but safe to keep)
            os.path.join(BASE_DIR, 'tools', 'templates', 'tools'),
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

# 3. Database Configuration
# This single block now handles SQLite locally, Cloud Shell Proxy, AND Cloud Run
DATABASES = {
    'default': env.db(
        'DATABASE_URL', 
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'
    )
}

# Apply connection pooling only if the detected database is PostgreSQL
if DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
    DATABASES['default']['CONN_MAX_AGE'] = 600

# 4. Storage & Static Files
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# --- ADD THESE TWO LINES FOR USER UPLOADS ---
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

if not DEBUG:
    # --- PRODUCTION (Cloud Run + GCS) ---
    GS_BUCKET_NAME = 'agentic-media-files'
    GS_QUERYSTRING_AUTH = False 
    GS_DEFAULT_ACL = None

    STORAGES = {
        # 'default' is for media files. Keep this as Google Cloud Storage!
        "default": {
            "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        },
        # Change 'staticfiles' to use WhiteNoise instead of GCS
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    CSRF_TRUSTED_ORIGINS = ["https://*.a.run.app"]
else:
    # --- LOCAL DEVELOPMENT ---
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Internationalization
LANGUAGE_CODE = 'en-us'

# This is the "Magic" line for Bangkok/Phnom Penh time
TIME_ZONE = 'Asia/Bangkok' 

USE_I18N = True

# Must be True so Django converts UTC from the server to your local TIME_ZONE
USE_TZ = True

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"
import os
import environ
from pathlib import Path

# Initialize environ
env = environ.Env()
BASE_DIR = Path(__file__).resolve().parent.parent

# 1. Load local .env file
env_file = os.path.join(BASE_DIR, ".env")
if os.path.isfile(env_file):
    env.read_env(env_file)

# --- CRITICAL FIX: Robust Boolean for DEBUG ---
# Cloud Run env vars are strings. env.bool ensures "False" (string) becomes False (bool).
DEBUG = env.bool("DEBUG", default=True)

SECRET_KEY = env("SECRET_KEY", default='your-fallback-insecure-key')
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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise is kept for local dev; STORAGES handles prod
    'whitenoise.middleware.WhiteNoiseMiddleware', 
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'agentic_platform.urls'

# --- CRITICAL FIX: Template Directory ---
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # This ensures Django looks in the folder 'templates' at the root
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'agentic_platform.wsgi.application'

# 3. Database Configuration
DATABASES = {
    'default': env.db(
        'DATABASE_URL', 
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'
    )
}

# Cloud SQL logic
if os.getenv('DATABASE_URL', '').startswith('postgres://'):
    DATABASES['default']['CONN_MAX_AGE'] = 600
    if os.getenv('CLOUD_SQL_CONNECTION_NAME'):
        DATABASES['default']['HOST'] = f"/cloudsql/{os.getenv('CLOUD_SQL_CONNECTION_NAME')}"

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Bangkok'
USE_I18N = True
USE_TZ = True

# 4. Storage & Static Files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

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
            "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        },
    }
    
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Specific URL prevents CSRF errors on Admin login
    CSRF_TRUSTED_ORIGINS = [
        "https://agentic-platform-521063372903.asia-southeast1.run.app",
    ]
else:
    # --- LOCAL DEVELOPMENT ---
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
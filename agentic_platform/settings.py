import os
import environ
from pathlib import Path

# Initialize environ
env = environ.Env(DEBUG=(bool, True))
BASE_DIR = Path(__file__).resolve().parent.parent

# 1. Load local .env file if it exists (for local dev)
env_file = os.path.join(BASE_DIR, ".env")
if os.path.isfile(env_file):
    env.read_env(env_file)

# 2. Security Settings
SECRET_KEY = env("SECRET_KEY", default='your-fallback-insecure-key')
DEBUG = env("DEBUG")

# Allow all hosts for Cloud Run, but you can restrict this to your specific .a.run.app URL later
ALLOWED_HOSTS = ["*"]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'document',
    'storages',  # Required for Google Cloud Storage
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', # Keep for local/fallback, but GCS handles production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'agentic_platform.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
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

# Cloud SQL specific socket settings
if os.getenv('DATABASE_URL', '').startswith('postgres://'):
    DATABASES['default']['CONN_MAX_AGE'] = 600
    if '/cloudsql/' in os.getenv('DATABASE_URL'):
        # Ensure the connection name is passed correctly
        DATABASES['default']['HOST'] = f"/cloudsql/{os.getenv('CLOUD_SQL_CONNECTION_NAME')}"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization (Bangkok Time)
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Bangkok'
USE_I18N = True
USE_TZ = True

# 4. Storage Configuration (The Switch)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

if not DEBUG:
    # CLOUD SETTINGS
    GS_BUCKET_NAME = 'agentic-media-files' 
    
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        },
        "staticfiles": {
            "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        },
    }
    
    # CHANGE THIS TO NONE
    GS_DEFAULT_ACL = None  
    
    # Cloud Run specific security settings
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Replace with your actual Cloud Run URL to avoid CSRF errors
    CSRF_TRUSTED_ORIGINS = [env("CLOUDRUN_SERVICE_URL", default="https://*.a.run.app")]

else:
    # --- LOCAL DEVELOPMENT ---
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
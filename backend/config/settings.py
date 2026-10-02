import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-development-only-change-before-deployment')
if not DEBUG and SECRET_KEY == 'local-development-only-change-before-deployment':
    raise RuntimeError('DJANGO_SECRET_KEY is required outside development')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'rest_framework', 'drf_spectacular', 'crm','qr','intake','data_import','admissions','progression','finance']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'crm.middleware.RequestIdMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware']
MIDDLEWARE.append('django.middleware.clickjacking.XFrameOptionsMiddleware')
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': dj_database_url.config(default=f'sqlite:///{BASE_DIR / "db.sqlite3"}', conn_max_age=60)}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = 'crm.api.csrf_failure'
CSRF_TRUSTED_ORIGINS = os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')
REST_FRAMEWORK = {'URL_FORMAT_OVERRIDE':None, 'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'], 'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'], 'DEFAULT_SCHEMA_CLASS': 'crm.schema.StableAutoSchema', 'EXCEPTION_HANDLER': 'crm.api.exception_handler', 'DEFAULT_THROTTLE_RATES': {'login': '10/min'}}
SPECTACULAR_SETTINGS = {'TITLE': 'ConsMan API', 'VERSION': '0.1.0'}
MEDIA_ROOT=BASE_DIR/'private_media'
if os.getenv('S3_BUCKET'):
    STORAGES={'default':{'BACKEND':'storages.backends.s3.S3Storage','OPTIONS':{'bucket_name':os.environ['S3_BUCKET'],'region_name':os.getenv('AWS_REGION','ap-south-1'),'endpoint_url':os.getenv('S3_ENDPOINT_URL') or None,'default_acl':None,'querystring_auth':True,'file_overwrite':False,'object_parameters':{'ServerSideEncryption':'AES256'}}},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS='DENY'
if not DEBUG:
    SECURE_SSL_REDIRECT=True
    SECURE_HSTS_SECONDS=31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS=True
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
LOGGING={'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'loggers':{'consman.requests':{'handlers':['console'],'level':'INFO','propagate':False}}}
PUBLIC_FORM_ORIGIN=os.getenv('PUBLIC_FORM_ORIGIN','http://127.0.0.1:3000' if DEBUG else 'https://register.theblessingedu.com')
PUBLIC_ALLOWED_ORIGINS=os.getenv('PUBLIC_ALLOWED_ORIGINS','http://127.0.0.1:3000,http://localhost:3000' if DEBUG else PUBLIC_FORM_ORIGIN).split(',')
TURNSTILE_SITE_KEY=os.getenv('TURNSTILE_SITE_KEY','')
TURNSTILE_SECRET_KEY=os.getenv('TURNSTILE_SECRET_KEY','')
TURNSTILE_HOSTNAME=os.getenv('TURNSTILE_HOSTNAME','')
INTAKE_DEV_BYPASS=DEBUG and os.getenv('INTAKE_DEV_BYPASS','true').lower()=='true'
OTP_ALLOWED_COUNTRY_PREFIXES=os.getenv('OTP_ALLOWED_COUNTRY_PREFIXES','+977').split(',')
PUBLIC_SUBMISSIONS_PER_DAY=int(os.getenv('PUBLIC_SUBMISSIONS_PER_DAY','40'))
WHATSAPP_GATEWAY_URL=os.getenv('WHATSAPP_GATEWAY_URL','')
WHATSAPP_GATEWAY_KEY=os.getenv('WHATSAPP_GATEWAY_KEY','')
WHATSAPP_GATEWAY_READ_TIMEOUT=min(30,max(4,int(os.getenv('WHATSAPP_GATEWAY_READ_TIMEOUT','15'))))
WHATSAPP_GATEWAY_SESSION=os.getenv('WHATSAPP_GATEWAY_SESSION','')
WHATSAPP_GATEWAY_WEBHOOK_SECRET=os.getenv('WHATSAPP_GATEWAY_WEBHOOK_SECRET','')
WHATSAPP_MESSAGE_COST_MINOR=int(os.getenv('WHATSAPP_MESSAGE_COST_MINOR','0'))
SMS_PROVIDER_URL=os.getenv('SMS_PROVIDER_URL','')
SMS_PROVIDER_KEY=os.getenv('SMS_PROVIDER_KEY','')
SMS_MESSAGE_COST_MINOR=int(os.getenv('SMS_MESSAGE_COST_MINOR','1'))
DATA_UPLOAD_MAX_MEMORY_SIZE=12*1024*1024
TRUSTED_PROXY_IPS=os.getenv('TRUSTED_PROXY_IPS','127.0.0.1/32,::1/128').split(',')

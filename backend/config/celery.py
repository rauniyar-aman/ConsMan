import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
app=Celery('consman')
app.config_from_object('django.conf:settings',namespace='CELERY')
app.autodiscover_tasks()
app.conf.beat_schedule={'communications-every-minute':{'task':'communications.tasks.communication_tick','schedule':60.0}}

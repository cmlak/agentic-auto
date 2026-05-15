import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'agentic_platform.settings')
app = Celery('agentic_platform')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# --- THE BACKUP SCHEDULE ---
app.conf.beat_schedule = {
    'daily-schema-backup-2am': {
        'task': 'tools.tasks.backup_all_tenant_schemas', # Update 'tools' to wherever your tasks.py is
        'schedule': crontab(hour=2, minute=0), # Runs every day at 2:00 AM server time
    },
}
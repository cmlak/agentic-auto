# portal/management/commands/trigger_backup.py
import ssl
from django.core.management.base import BaseCommand
from celery import current_app
from tools.tasks import backup_all_tenant_schemas

class Command(BaseCommand):
    help = "Safely hands off the multi-tenant database backup task to Celery"

    def handle(self, *args, **options):
        self.stdout.write("Initializing task handoff to Upstash Redis...")

        # FORCE Python's actual SSL runtime object directly into the Celery engine
        current_app.conf.redis_backend_transport_options = {
            'ssl_cert_reqs': ssl.CERT_NONE
        }
        current_app.conf.broker_transport_options = {
            'ssl_cert_reqs': ssl.CERT_NONE
        }

        # Dispatch the task
        task = backup_all_tenant_schemas.delay()
        self.stdout.write(self.style.SUCCESS(f"Successfully queued background backup! Task ID: {task.id}"))
# portal/management/commands/trigger_backup.py
from django.core.management.base import BaseCommand
from tools.tasks import backup_all_tenant_schemas

class Command(BaseCommand):
    help = "Safely hands off the multi-tenant database backup task to Celery"

    def handle(self, *args, **options):
        self.stdout.write("Initializing task handoff to Upstash Redis...")
        task = backup_all_tenant_schemas.delay()
        self.stdout.write(self.style.SUCCESS(f"Successfully queued background backup! Task ID: {task.id}"))
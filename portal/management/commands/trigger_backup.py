# portal/management/commands/trigger_backup.py
from django.core.management.base import BaseCommand
from tools.tasks import backup_all_tenant_schemas

class Command(BaseCommand):
    help = "Triggers multi-tenant database backups synchronously"

    def handle(self, *args, **options):
        self.stdout.write("Initializing synchronous database backup sequence...")
        
        # .apply() executes the code inline immediately instead of queuing it to Redis
        task = backup_all_tenant_schemas.apply()
        
        self.stdout.write(f"Backup process completed. Status: {task.status}")
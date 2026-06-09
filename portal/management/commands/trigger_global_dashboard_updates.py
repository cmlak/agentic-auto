from django.core.management.base import BaseCommand
from clients.models import Client
from django_tenants.utils import schema_context
from account.services import generate_tenant_dashboard_snapshot

class Command(BaseCommand):
    help = "Triggers global dashboard updates synchronously across all tenants"

    def handle(self, *args, **options):
        self.stdout.write("Initializing synchronous global dashboard updates...")
        
        tenants = Client.objects.exclude(schema_name='public')
        count = 0
        
        for tenant in tenants:
            with schema_context(tenant.schema_name):
                generate_tenant_dashboard_snapshot()
                count += 1
                
        self.stdout.write(self.style.SUCCESS(f"Dashboard update process completed. Updated {count} tenants."))
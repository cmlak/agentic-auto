from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from clients.models import Client
from account.services import run_agent_rule_audit

class Command(BaseCommand):
    help = 'Triggers the AI Audit Agent to analyze RAG rules for all tenants and send dashboard notifications.'

    def add_arguments(self, parser):
        parser.add_argument('--schema', type=str, help='Run audit for a specific schema only')

    def handle(self, *args, **options):
        specific_schema = options.get('schema')

        if specific_schema:
            tenants = Client.objects.filter(schema_name=specific_schema)
            self.stdout.write(f"🎯 Targeting specific schema: {specific_schema}")
        else:
            tenants = Client.objects.exclude(schema_name='public')
            self.stdout.write(f"🌍 Starting global audit for {tenants.count()} tenants...")

        for tenant in tenants:
            self.stdout.write(f"🔍 Auditing rules for: {tenant.schema_name}...")
            with schema_context(tenant.schema_name):
                try:
                    run_agent_rule_audit()
                    self.stdout.write(self.style.SUCCESS(f"✅ Audit complete for {tenant.schema_name}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"❌ Failed audit for {tenant.schema_name}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("🏁 Multi-tenant audit pipeline finished."))
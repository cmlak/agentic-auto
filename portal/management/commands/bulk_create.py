from django.core.management.base import BaseCommand
from clients.models import ExchangeRate

class Command(BaseCommand):
    help = 'Bulk create missing exchange rates'

    def handle(self, *args, **options):
        self.stdout.write("Creating exchange rates...")
        
        exchange1, created1 = ExchangeRate.objects.get_or_create(
                id=7,
                defaults={'date': '2026-06-08', 'rate': 4027}
            )
        
        # exchange2, created2 = ExchangeRate.objects.get_or_create(
        #         id=298,
        #         defaults={'date': '2026-02-28', 'rate': 4012}
        #     )
            
        self.stdout.write(self.style.SUCCESS('Successfully created exchange rates!'))
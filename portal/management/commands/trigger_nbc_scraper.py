from django.core.management.base import BaseCommand
from clients.tasks import scrape_exchange_rate_nbc

class Command(BaseCommand):
    help = "Triggers the NBC exchange rate scraper synchronously"

    def handle(self, *args, **options):
        self.stdout.write("Initializing synchronous NBC exchange rate scraping sequence...")
        
        # .apply() executes the code inline immediately instead of queuing it to Redis
        task = scrape_exchange_rate_nbc.apply()
        
        self.stdout.write(f"Scraper process completed. Status: {task.status}")
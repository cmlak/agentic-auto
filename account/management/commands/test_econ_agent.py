import os
from django.core.management.base import BaseCommand
from tools.agents import EconAgent

class Command(BaseCommand):
    help = 'Passes raw economic data to the EconAgent for analysis and notification.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data', 
            type=str, 
            help='Raw economic data string to evaluate.',
            default="The National Bank of Cambodia has announced a sudden interest rate hike of 0.5% effective immediately. Consumer Price Index (CPI) has also shown a 2.5% increase over the last quarter."
        )

    def handle(self, *args, **options):
        raw_data = options['data']
        self.stdout.write(self.style.WARNING(f"Feeding data to EconAgent:\n{raw_data}\n"))
        
        EconAgent.evaluate_incoming_data(raw_data)
        
        self.stdout.write(self.style.SUCCESS("\nEconAgent evaluation completed! Check your dashboards for new notifications."))
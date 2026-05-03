import os
import csv
from django.core.management.base import BaseCommand
from django.db import transaction

from account.models import AccountMappingRule

class Command(BaseCommand):
    help = 'Bulk import Account Mapping Rules from a CSV file within the configured base directory.'

    def add_arguments(self, parser):
        # We changed this to ask ONLY for the filename, not the full path
        parser.add_argument('filename', type=str, help='The name of the CSV file (e.g., account_accountmappingrule.csv).')

    def handle(self, *args, **kwargs):
        filename = kwargs['filename']
        
        # --- NEW: HARDCODED BASE URL ---
        # The 'r' prefix stands for "raw string", which prevents Python from 
        # treating the backslashes (\) as escape characters.
        base_url = r"C:\bakertilly\BakerTilly\CCKT\03. Demonstrate"
        
        # Safely combine the base_url with the filename
        csv_file_path = os.path.join(base_url, filename)

        try:
            # Check if the file actually exists before trying to open it
            if not os.path.exists(csv_file_path):
                self.stdout.write(self.style.ERROR(f'❌ File not found at: {csv_file_path}'))
                return

            # Using 'utf-8-sig' safely handles any hidden BOM characters from Excel exports
            with open(csv_file_path, mode='r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                
                created_count = 0
                updated_count = 0
                
                # transaction.atomic() ensures that if one row fails, the whole batch rolls back
                with transaction.atomic():
                    for row_num, row in enumerate(reader, start=2):
                        try:
                            # update_or_create respects the unique_together constraint
                            rule, created = AccountMappingRule.objects.update_or_create(
                                client_id=row['client_id'],
                                account_id=row['account_id'],
                                defaults={
                                    'trigger_keywords': row.get('trigger_keywords', ''),
                                    'ai_guideline': row.get('ai_guideline', '')
                                }
                            )
                            if created:
                                created_count += 1
                            else:
                                updated_count += 1
                                
                        except KeyError as e:
                            self.stdout.write(self.style.ERROR(f'Missing expected column {str(e)} in CSV header.'))
                            return
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f'Error processing row {row_num}: {str(e)}'))
                            raise  # Trigger transaction rollback
                            
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Import Complete! Created: {created_count} | Updated: {updated_count}')
                )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ A fatal error occurred: {str(e)}'))
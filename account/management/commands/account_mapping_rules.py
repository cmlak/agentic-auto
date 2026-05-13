import os
import csv
from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import schema_context

from account.models import Account, AccountMappingRule

class Command(BaseCommand):
    help = 'Dynamically bulk import Account Mapping Rules matching on exact String Account IDs from CSV.'

    def add_arguments(self, parser):
        parser.add_argument('filename', type=str, help='The name of the CSV file.')
        parser.add_argument('-s', '--schema', type=str, required=True, help='The tenant schema name (e.g., CCKT)')

    def handle(self, *args, **kwargs):
        filename = kwargs['filename']
        schema_name = kwargs['schema']
        
        base_url = r"C:\bakertilly\BakerTilly\CCKT\03. Demonstrate"
        csv_file_path = os.path.join(base_url, filename)

        if not os.path.exists(csv_file_path):
            self.stdout.write(self.style.ERROR(f'❌ File not found at: {csv_file_path}'))
            return

        with schema_context(schema_name):
            self.stdout.write(self.style.SUCCESS(f'🔗 Connected to schema: {schema_name}'))
            
            # --- SAFETY CHECK ---
            if not Account.objects.exists():
                self.stdout.write(self.style.ERROR(
                    f'🛑 CRITICAL: There are NO accounts in the {schema_name} schema! '
                    f'You must import your Chart of Accounts before importing mapping rules.'
                ))
                return

            try:
                with open(csv_file_path, mode='r', encoding='utf-8-sig') as file:
                    reader = csv.DictReader(file)
                    created_count = 0
                    updated_count = 0
                    skipped_count = 0
                    
                    with transaction.atomic():
                        # Start at row 2 to account for the header
                        for row_num, row in enumerate(reader, start=2):
                            
                            # --- THE DYNAMIC LOOKUP ---
                            # We dynamically pull the string account code directly from the CSV column
                            target_string_id = str(row.get('account_id', '')).strip()
                            
                            # If the CSV has a blank account_id for this row, skip it safely
                            if not target_string_id:
                                self.stdout.write(self.style.WARNING(
                                    f"⚠️ Row {row_num}: Missing 'account_id' in CSV. Skipping."
                                ))
                                skipped_count += 1
                                continue
                                
                            try:
                                # Fetch the actual Account object using the primary key from the CSV
                                account_obj = Account.objects.get(id=target_string_id)
                                
                                # Use `account=account_obj` to let Django handle the foreign key logic safely
                                rule, created = AccountMappingRule.objects.update_or_create(
                                    account=account_obj, 
                                    defaults={
                                        'trigger_keywords': row.get('trigger_keywords', ''),
                                        'ai_guideline': row.get('ai_guideline', '')
                                    }
                                )
                                
                                if created:
                                    created_count += 1
                                else:
                                    updated_count += 1
                                    
                            except Account.DoesNotExist:
                                self.stdout.write(self.style.WARNING(
                                    f"⚠️ Row {row_num}: Account ID '{target_string_id}' not found in {schema_name}. Skipping."
                                ))
                                skipped_count += 1

                    self.stdout.write(self.style.SUCCESS(
                        f'✅ Import Complete for {schema_name}! \n'
                        f'   Created: {created_count} \n'
                        f'   Updated: {updated_count} \n'
                        f'   Skipped: {skipped_count}'
                    ))

            except KeyError as e:
                self.stdout.write(self.style.ERROR(f'❌ Missing column in CSV: {str(e)}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ A fatal error occurred: {str(e)}'))
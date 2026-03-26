import pandas as pd
import re
import os
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Transforms a raw CSV of accounts into a structured format for import.'

    def handle(self, *args, **options):
        # --- Configuration ---
        BASE_DIR = r'C:\bakertilly\BakerTilly\CCKT\Migration'
        INPUT_FILE = os.path.join(BASE_DIR, 'account_addition.csv')
        OUTPUT_FILE = os.path.join(BASE_DIR, 'transformed_accounts.csv')

        def classify_by_id(acc_id):
            """Logic to infer account type based on the first digit of the Account ID."""
            prefix = str(acc_id)[0]
            mapping = {
                '1': 'Asset',
                '2': 'Liability',
                '3': 'Equity',
                '4': 'Asset',
                '5': 'Revenue',
                '6': 'Expense',
                '7': 'Expense',
                '8': 'Expense'
            }
            return mapping.get(prefix, 'Liability')

        if not os.path.exists(INPUT_FILE):
            self.stdout.write(self.style.ERROR(f"Error: {INPUT_FILE} not found."))
            return

        self.stdout.write(f"Reading {INPUT_FILE}...")
        # Load addition file - usually raw text in the first column
        df_raw = pd.read_csv(INPUT_FILE, header=None)
        
        transformed_data = []

        for index, row in df_raw.iterrows():
            cell_value = str(row[0]).strip()
            
            # Pattern to match "100000 - Account Name"
            match = re.match(r'^(\d+)\s*-\s*(.*)$', cell_value)
            
            if match:
                acc_id = match.group(1)
                acc_name = match.group(2)
                acc_type = classify_by_id(acc_id)
                
                transformed_data.append({
                    'account_id': acc_id,
                    'name': acc_name,
                    'account_type': acc_type
                })

        # Create the new DataFrame
        df_transformed = pd.DataFrame(transformed_data)
        
        # Save to CSV matching the 'origin' format
        df_transformed.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        self.stdout.write(self.style.SUCCESS(f"✅ Success! Transformed {len(df_transformed)} accounts."))
        self.stdout.write(f"Saved to: {OUTPUT_FILE}")
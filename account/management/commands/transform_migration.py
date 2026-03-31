import os
import pandas as pd
import numpy as np
from django.core.management.base import BaseCommand

# ====================================================================
# CONFIGURATION
# ====================================================================
BASE_DIR = r'C:\bakertilly\BakerTilly\CCKT\Migration'

# Define your file names here
INPUT_FILE_NAME = "CCKT_GL_Jan_Feb_origin.xls" 
OUTPUT_FILE_NAME = "CCKT_GL_Cleaned_Output.csv"

# Automatically construct the full absolute paths
INPUT_CSV = os.path.join(BASE_DIR, INPUT_FILE_NAME)
OUTPUT_CSV = os.path.join(BASE_DIR, OUTPUT_FILE_NAME)

class Command(BaseCommand):
    help = 'Cleans and transforms historical General Ledger data into a flat CSV format.'

    def handle(self, *args, **kwargs):
        self.stdout.write(f"Reading data from:\n{INPUT_CSV}\n")
        
        try:
            # 1. Load the Excel file, skipping the first 5 rows of report metadata
            df = pd.read_excel(INPUT_CSV, skiprows=5)
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f"❌ Error: Could not find the file at {INPUT_CSV}"))
            self.stderr.write(self.style.WARNING("Please ensure the file is placed in the correct BASE_DIR folder."))
            return
        
        # 2. Drop "spacer" columns (Pandas names these 'Unnamed: X')
        valid_cols = [col for col in df.columns if not str(col).startswith('Unnamed')]
        df = df[valid_cols]
        
        # Clean up column names by stripping accidental whitespace
        df.columns = df.columns.str.strip()
        
        # 3. Extract and Forward-Fill the "Account" name
        is_account_header = df['Date'].isna() & df['No.'].astype(str).str.contains(' - ')
        df.loc[is_account_header, 'Account'] = df.loc[is_account_header, 'No.']
        df['Account'] = df['Account'].ffill()
        
        # 4. Filter for actual transaction rows
        df_clean = df.dropna(subset=['Date']).copy()

        # Drop "Openning Balance" or "Opening Balance" rows.
        # We check across ALL columns because the text might fall into 'Reference' or 'Description' instead of 'Source'.
        mask = df_clean.astype(str).apply(lambda col: col.str.contains('Openning Balance|Opening Balance', case=False, na=False)).any(axis=1)
        df_clean = df_clean[~mask]
        
        # 5. Clean Data Types
        for col in ['Debit', 'Credit']:
            if col in df_clean.columns:
                df_clean[col] = pd.to_numeric(
                    df_clean[col].astype(str).str.replace(',', ''), 
                    errors='coerce'
                ).fillna(0.0)
                
        # 6. Rename columns to match your exact requested output
        df_clean.rename(columns={
            'No.': 'ID',
            'Vendor / Customer / Employee': 'Vendor/Customer/Employee'
        }, inplace=True)
        
        # 7. Structure the final dataset
        final_columns = [
            'ID', 'Account', 'Date', 'Source', 'JV No', 'Reference', 
            'Vendor/Customer/Employee', 'Description', 'Debit', 'Credit'
        ]
        
        # Ensure all requested columns exist (if missing, create as empty strings)
        for col in final_columns:
            if col not in df_clean.columns:
                df_clean[col] = ''
                
        df_final = df_clean[final_columns]
        
        # 8. Export to CSV
        # Ensure the BASE_DIR exists, if not, create it safely
        os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
        
        df_final.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
        self.stdout.write(self.style.SUCCESS(f"✅ Success! Cleaned data saved to:\n{OUTPUT_CSV}"))
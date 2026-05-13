import os
import pandas as pd
import numpy as np
from django.core.management.base import BaseCommand


def cross_check_cash_records(client_filename, system_filename, output_filename="unprocessed_transactions.xlsx", stdout=None, style=None):
    # --- HARDCODED BASE URL ---
    base_url = r"C:\bakertilly\BakerTilly\CCKT\02. Client's Info\April 2026\Check"
    
    # Safely construct the full file paths
    client_path = os.path.join(base_url, client_filename)
    system_path = os.path.join(base_url, system_filename)
    output_path = os.path.join(base_url, output_filename)
    
    msg_loading = "🔄 Loading Excel files from:\n" + f"  ➡️ {client_path}\n" + f"  ➡️ {system_path}"
    if stdout:
        stdout.write(msg_loading)
    else:
        print(msg_loading)
    
    try:
        # Switch from read_csv to read_excel
        client_df = pd.read_excel(client_path)
        system_df = pd.read_excel(system_path)
    except FileNotFoundError as e:
        msg = f"\n❌ ERROR: Could not find the files. Please ensure they exist in {base_url}"
        if stdout:
            stdout.write(style.ERROR(msg) if style else msg)
        else:
            print(msg)
        return
    except Exception as e:
        msg = f"\n❌ ERROR reading Excel files: {e}"
        if stdout:
            stdout.write(style.ERROR(msg) if style else msg)
        else:
            print(msg)
        return

    # Clean up column names just in case Excel added trailing spaces
    client_df.columns = client_df.columns.str.strip()
    system_df.columns = system_df.columns.str.strip()
    
    # 1. Extract standard Amounts (Handling both Debit and Credit columns safely)
    client_df['Amount'] = client_df[['Debit', 'Credit']].max(axis=1).fillna(0)
    system_df['Amount'] = system_df[['Debit', 'Credit']].max(axis=1).fillna(0)
    
    client_amounts = client_df['Amount'].tolist()
    system_amounts = system_df['Amount'].tolist()
    
    matched_client_indices = set()
    sys_amt_pool = system_amounts.copy()
    
    # 2. First Pass: Exact 1-to-1 Matching
    for idx, row in client_df.iterrows():
        amt = row['Amount']
        if amt in sys_amt_pool:
            sys_amt_pool.remove(amt)
            matched_client_indices.add(idx)
            
    unmatched_client_df = client_df.drop(index=matched_client_indices)
    
    # 3. Second Pass: 1-to-N Matching (Handling Split Invoices like Rental + Driver Fee)
    truly_missing_indices = []
    
    for idx, row in unmatched_client_df.iterrows():
        amt = row['Amount']
        found_match = False
        
        # Look for a combination of 2 split items in the system pool that sum up to the client amount
        for i in range(len(sys_amt_pool)):
            for j in range(i + 1, len(sys_amt_pool)):
                if abs((sys_amt_pool[i] + sys_amt_pool[j]) - amt) < 0.01:
                    # Found a split match! Remove both from the system pool
                    sys_amt_pool.pop(j)
                    sys_amt_pool.pop(i)
                    found_match = True
                    break
            if found_match:
                break
                
        if not found_match:
            truly_missing_indices.append(idx)
            
    # 4. Final Output Compilation
    missing_transactions = client_df.loc[truly_missing_indices].copy()
    
    msg_complete = f"\n✅ Cross-check complete!\n🔍 Found {len(missing_transactions)} transactions in the Client Cash Book NOT processed by the System.\n"
    if stdout:
        stdout.write(style.SUCCESS(msg_complete) if style else msg_complete)
    else:
        print(msg_complete)
    
    if not missing_transactions.empty:
        if stdout:
            stdout.write(str(missing_transactions[['Date', 'Page', 'Description', 'Amount']]))
        else:
            print(missing_transactions[['Date', 'Page', 'Description', 'Amount']])
        
        # Save to Excel using openpyxl
        missing_transactions.to_excel(output_path, index=False, engine='openpyxl')
        msg_export = f"\n📁 Results exported to: {output_path}"
        if stdout:
            stdout.write(style.SUCCESS(msg_export) if style else msg_export)
        else:
            print(msg_export)
    else:
        msg_success = "🎉 All client transactions were successfully processed by the system!"
        if stdout:
            stdout.write(style.SUCCESS(msg_success) if style else msg_success)
        else:
            print(msg_success)

class Command(BaseCommand):
    help = 'Cross checks cash records between client and system files.'

    def handle(self, *args, **options):
        cross_check_cash_records(
            client_filename='Cash_client.xlsx', 
            system_filename='Cash_system.xlsx',
            output_filename='Unprocessed_Transactions_Report.xlsx',
            stdout=self.stdout,
            style=self.style
        )
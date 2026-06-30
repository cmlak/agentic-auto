import os
import pandas as pd
from django.core.management.base import BaseCommand
from django.conf import settings
from assets.processors import CustomAgent
import json

class Command(BaseCommand):
    help = 'Runs CustomAgent to extract and fine-tune data extraction from Commercial Invoices and Customs Declarations.'

    def handle(self, *args, **kwargs):
        pdf_dir = r"C:\bakertilly\BakerTilly\CCKT\02. Client's Info\Antigravity\Custom"
        
        # Determine API key from env or settings
        api_key = os.environ.get("GEMINI_API_KEY_2")
        if not api_key and hasattr(settings, 'GEMINI_API_KEY_2'):
            api_key = settings.GEMINI_API_KEY_2

        if not api_key:
            self.stdout.write(self.style.WARNING("GEMINI_API_KEY_2 not found in environment or settings. Ensure it is set."))
            return

        if not os.path.exists(pdf_dir):
            self.stdout.write(self.style.ERROR(f"Directory not found: {pdf_dir}"))
            return

        # Initialize the CustomAgent
        agent = CustomAgent(api_key=api_key or "")
        
        self.stdout.write(self.style.SUCCESS(f"Scanning directory: {pdf_dir}"))
        
        customs_data_list = []
        invoices_data = []
        
        all_files = sorted(os.listdir(pdf_dir))

        for filename in all_files:
            if filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
                filepath = os.path.join(pdf_dir, filename)
                mime_type = "application/pdf"
                if filename.lower().endswith(('.jpg', '.jpeg')): mime_type = "image/jpeg"
                if filename.lower().endswith('.png'): mime_type = "image/png"
                
                name_lower = filename.lower()
                
                # Only process Commercial Invoice and Customs Declaration
                if "declaration" in name_lower or "customs declaration" in name_lower or "customer declaration" in name_lower:
                    try:
                        with open(filepath, 'rb') as f:
                            file_bytes = f.read()
                            
                        self.stdout.write(f"\nProcessing Customs Declaration: {filename}...")
                        data = agent.extract_customs_declaration(file_bytes, mime_type=mime_type)
                        if data:
                            self.stdout.write(f"DEBUG {filename} Data:\n{json.dumps(data, indent=2)}")
                            if 'items' in data:
                                exchange_rate = data.get('exchange_rate', 1.0)
                                if exchange_rate == 0: exchange_rate = 1.0
                                declaration_number = data.get('customs_declaration_number', '')
                                
                                for item in data['items']:
                                    customs_data_list.append({
                                        'declaration_number': declaration_number,
                                        'exchange_rate': exchange_rate,
                                        'item_no': item.get('item_no', 0),
                                        'name': item.get('name', ''),
                                        'customs_value_riel': item.get('customs_value_riel', 0.0),
                                        'customs_duty_usd': item.get('customs_duty_riel', 0.0) / exchange_rate,
                                        'special_tax_usd': item.get('special_tax_riel', 0.0) / exchange_rate,
                                        'vat_usd': item.get('vat_riel', 0.0) / exchange_rate
                                    })
                        else:
                            self.stdout.write(self.style.WARNING(f"Failed to extract or no data for {filename}"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error processing {filename}: {e}"))

                elif "commercial invoice" in name_lower:
                    try:
                        with open(filepath, 'rb') as f:
                            file_bytes = f.read()
                            
                        self.stdout.write(f"\nProcessing Commercial Invoice: {filename}...")
                        data = agent.extract_commercial_invoice(file_bytes)
                        if data:
                            self.stdout.write(f"DEBUG {filename} Data:\n{json.dumps(data, indent=2)}")
                            invoices_data.append({'filename': filename, 'data': data})
                        else:
                            self.stdout.write(self.style.WARNING(f"Failed to extract or no data for {filename}"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error processing {filename}: {e}"))

        def find_taxes(item_name, item_no=0):
            if not item_name and item_no <= 0: return None
            
            # First, attempt an exact match by item_no if it is provided
            if item_no > 0:
                for cd in customs_data_list:
                    if cd.get('item_no') == item_no:
                        return cd
            
            import re
            def get_tokens(text):
                return set(re.findall(r'\b[a-z0-9]+\b', text.lower()))
            
            item_tokens = get_tokens(item_name)
            if not item_tokens: return None
            
            best_match = None
            highest_score = 0.0
            
            for cd in customs_data_list:
                cd_name = cd['name']
                cd_tokens = get_tokens(cd_name)
                if not cd_tokens: continue
                
                # Full substring match
                if cd_name.lower().strip() in item_name.lower() or item_name.lower().strip() in cd_name.lower():
                    return cd
                
                min_len = min(len(item_tokens), len(cd_tokens))
                if min_len == 0: continue
                
                intersection = item_tokens.intersection(cd_tokens)
                score = len(intersection) / min_len
                
                if score > highest_score:
                    highest_score = score
                    best_match = cd
            
            if highest_score >= 0.4 and best_match:
                return best_match
                
            return None

        # Build output rows
        all_items = []
        for inv in invoices_data:
            filename = inv['filename']
            data = inv['data']
            inv_number = data.get('invoice_number', '')
            date = data.get('date', '')
            total_val = data.get('total_value', 0.0)
            
            items = data.get('items', [])
            if not items:
                # Add an empty row if no items found
                all_items.append({
                    'Source File': filename,
                    'Invoice Number': inv_number,
                    'Date': date,
                    'Item Name': '',
                    'Invoice Amount (USD)': 0.0,
                    'Declaration Number': '',
                    '46 Customs Value (Riel)': 0.0,
                    '46 Customs Value (USD)': 0.0,
                    'Custom Duty - COP (USD)': 0.0,
                    'Special Tax - SOP (USD)': 0.0,
                    'Value Added Tax - VAT (USD)': 0.0,
                })
            else:
                for item in items:
                    item_name = item.get('name', '')
                    item_no = item.get('item_no', 0)
                    item_amt = item.get('amount_usd', 0.0)
                    
                    matched_cd = find_taxes(item_name, item_no)
                    if matched_cd:
                        exchange_rate = matched_cd.get('exchange_rate', 1.0)
                        customs_value_riel = matched_cd.get('customs_value_riel', 0.0)
                        customs_value_usd = customs_value_riel / exchange_rate if exchange_rate > 0 else 0.0
                        
                        all_items.append({
                            'Source File': filename,
                            'Invoice Number': inv_number,
                            'Date': date,
                            'Item Name': item_name,
                            'Invoice Amount (USD)': item_amt,
                            'Declaration Number': matched_cd.get('declaration_number', ''),
                            '46 Customs Value (Riel)': customs_value_riel,
                            '46 Customs Value (USD)': round(customs_value_usd, 2),
                            'Custom Duty - COP (USD)': round(matched_cd.get('customs_duty_usd', 0.0), 2),
                            'Special Tax - SOP (USD)': round(matched_cd.get('special_tax_usd', 0.0), 2),
                            'Value Added Tax - VAT (USD)': round(matched_cd.get('vat_usd', 0.0), 2),
                        })
                    else:
                        all_items.append({
                            'Source File': filename,
                            'Invoice Number': inv_number,
                            'Date': date,
                            'Item Name': item_name,
                            'Invoice Amount (USD)': item_amt,
                            'Declaration Number': '',
                            '46 Customs Value (Riel)': 0.0,
                            '46 Customs Value (USD)': 0.0,
                            'Custom Duty - COP (USD)': 0.0,
                            'Special Tax - SOP (USD)': 0.0,
                            'Value Added Tax - VAT (USD)': 0.0,
                        })

        if all_items:
            df = pd.DataFrame(all_items)
            output_path = os.path.join(pdf_dir, "extracted_customs_data.xlsx")
            try:
                df.to_excel(output_path, index=False)
                self.stdout.write(self.style.SUCCESS(f"\nSuccessfully exported {len(all_items)} rows to {output_path}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to save Excel file: {e}"))
        else:
            self.stdout.write(self.style.WARNING("\nNo items extracted from documents."))

import os
import pandas as pd
from django.core.management.base import BaseCommand
from django.conf import settings
from assets.processors import DocAgent

class Command(BaseCommand):
    help = 'Runs doc_agent to extract data from commercial invoices and export to Excel.'

    def handle(self, *args, **kwargs):
        pdf_dir = r"C:\bakertilly\BakerTilly\CCKT\02. Client's Info\Antigravity"
        
        # Determine API key from env or settings
        api_key = os.environ.get("GEMINI_API_KEY_2")
        if not api_key and hasattr(settings, 'GEMINI_API_KEY_2'):
            api_key = settings.GEMINI_API_KEY_2

        if not api_key:
            self.stdout.write(self.style.WARNING("GEMINI_API_KEY_2 not found in environment or settings. Ensure it is set."))

        if not os.path.exists(pdf_dir):
            self.stdout.write(self.style.ERROR(f"Directory not found: {pdf_dir}"))
            return

        # Initialize the DocAgent
        agent = DocAgent(api_key=api_key or "")
        
        self.stdout.write(self.style.SUCCESS(f"Scanning directory: {pdf_dir}"))
        
        customs_data_list = []
        invoices_data = []
        
        # Accumulators for auxiliary costs
        aux_invoice_numbers = []
        total_freight_usd = 0.0
        total_insurance_usd = 0.0
        aux_thc_usd = 0.0
        aux_port_charges_usd = 0.0
        aux_clearance_trucking_usd = 0.0
        
        # Accumulators for reimbursement
        total_reimb_amount_usd = 0.0
        reimb_thc_usd = 0.0
        reimb_port_charges_usd = 0.0
        
        global_exchange_rate = 1.0
        
        import difflib
        all_files = sorted(os.listdir(pdf_dir))
        found_invoices = False

        # Single Pass to process all files in order they appear (respects 01. / 02. prefix)
        for filename in all_files:
            if filename.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
                filepath = os.path.join(pdf_dir, filename)
                mime_type = "application/pdf"
                if filename.lower().endswith(('.jpg', '.jpeg')): mime_type = "image/jpeg"
                if filename.lower().endswith('.png'): mime_type = "image/png"
                
                try:
                    with open(filepath, 'rb') as f:
                        file_bytes = f.read()
                        
                    name_lower = filename.lower()
                    
                    if "declaration" in name_lower:
                        self.stdout.write(f"Processing Customs Declaration: {filename}...")
                        data = agent.extract_customs_declaration(file_bytes, mime_type=mime_type)
                        if data and 'items' in data:
                            exchange_rate = data.get('exchange_rate', 1.0)
                            if exchange_rate == 0: exchange_rate = 1.0
                            global_exchange_rate = exchange_rate # Save for auxiliary KHR conversion if needed
                            declaration_number = data.get('customs_declaration_number', '')
                            for item in data['items']:
                                customs_data_list.append({
                                    'declaration_number': declaration_number,
                                    'name': item.get('name', ''),
                                    'customs_duty_usd': item.get('customs_duty_riel', 0.0) / exchange_rate,
                                    'special_tax_usd': item.get('special_tax_riel', 0.0) / exchange_rate,
                                    'vat_usd': item.get('vat_riel', 0.0) / exchange_rate
                                })
                    elif "commercial" in name_lower or ("invoice" in name_lower and "freight" not in name_lower and "tax" not in name_lower):
                        found_invoices = True
                        self.stdout.write(f"Processing Commercial Invoice: {filename}...")
                        data = agent.extract_commercial_invoice(file_bytes)
                        if data:
                            invoices_data.append({'filename': filename, 'data': data})
                        else:
                            self.stdout.write(self.style.WARNING(f"Failed to extract or no data for {filename}"))
                    elif "reimbursement" in name_lower or "re-imbursement" in name_lower:
                        self.stdout.write(f"Processing Reimbursement Document: {filename}...")
                        data = agent.extract_reimbursement(file_bytes, mime_type=mime_type)
                        if data:
                            inv_num = data.get('invoice_number', '')
                            if inv_num:
                                aux_invoice_numbers.append(inv_num)
                            
                            total_reimb_amount_usd += data.get('total_reimbursement_usd', 0.0)
                            reimb_thc_usd += data.get('thc_usd', 0.0)
                            reimb_port_charges_usd += data.get('port_charges_usd', 0.0)
                    else:
                        # Process as auxiliary document
                        self.stdout.write(f"Processing Auxiliary Document: {filename}...")
                        data = agent.extract_auxiliary_costs(file_bytes, mime_type=mime_type)
                        if data:
                            inv_num = data.get('invoice_number', '')
                            if inv_num:
                                aux_invoice_numbers.append(inv_num)
                                
                            # Safeguards against LLM miscategorization based on filenames
                            if "port charge" in name_lower:
                                data['port_charges_usd'] = data.get('port_charges_usd', 0.0) + data.get('terminal_handling_charge_usd', 0.0)
                                data['terminal_handling_charge_usd'] = 0.0
                            if "terminal handling" in name_lower or "thc" in name_lower:
                                data['terminal_handling_charge_usd'] = data.get('terminal_handling_charge_usd', 0.0) + data.get('port_charges_usd', 0.0)
                                data['port_charges_usd'] = 0.0
                                
                            total_freight_usd += data.get('freight_charge_usd', 0.0)
                            total_insurance_usd += data.get('insurance_usd', 0.0)
                            aux_thc_usd += data.get('terminal_handling_charge_usd', 0.0)
                            aux_port_charges_usd += data.get('port_charges_usd', 0.0)
                            aux_clearance_trucking_usd += data.get('clearance_trucking_demurrage_usd', 0.0)
                            
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error processing {filename}: {e}"))
                    
        # Cross-check to avoid double counting. Use max to ensure we don't undercut the master reimbursement.
        actual_thc_usd = max(aux_thc_usd, reimb_thc_usd)
        actual_port_charges_usd = max(aux_port_charges_usd, reimb_port_charges_usd)
        actual_clearance_trucking_usd = aux_clearance_trucking_usd
        
        # Only deduct fees that we are sure exist as line items on the reimbursement document
        deducted_fees = reimb_thc_usd + reimb_port_charges_usd
        net_reimbursement_usd = max(0.0, total_reimb_amount_usd - deducted_fees)

        def find_taxes(item_name):
            if not item_name: return 0.0, 0.0, 0.0, ""
            
            import re
            # Extract alphanumeric tokens, ignoring small unhelpful words if needed
            def get_tokens(text):
                return set(re.findall(r'\b[a-z0-9]+\b', text.lower()))
            
            item_tokens = get_tokens(item_name)
            if not item_tokens: return 0.0, 0.0, 0.0, ""
            
            best_match = None
            highest_score = 0.0
            
            for cd in customs_data_list:
                cd_name = cd['name']
                cd_tokens = get_tokens(cd_name)
                if not cd_tokens: continue
                
                # Full substring is still a guaranteed match
                if cd_name.lower().strip() in item_name.lower() or item_name.lower().strip() in cd_name.lower():
                    return cd['customs_duty_usd'], cd['special_tax_usd'], cd['vat_usd'], cd.get('declaration_number', '')
                
                # Calculate token intersection ratio (how many tokens match relative to the shorter string)
                intersection = item_tokens.intersection(cd_tokens)
                
                # Exclude purely generic tokens from scoring heavily, but for now just raw count is fine
                min_len = min(len(item_tokens), len(cd_tokens))
                if min_len == 0: continue
                
                score = len(intersection) / min_len
                
                if score > highest_score:
                    highest_score = score
                    best_match = cd
            
            # If the token similarity is above ~40% (e.g. 3 out of 7 words match), consider it a success
            if highest_score >= 0.4 and best_match:
                return best_match['customs_duty_usd'], best_match['special_tax_usd'], best_match['vat_usd'], best_match.get('declaration_number', '')
                
            return 0.0, 0.0, 0.0, ""

        all_items = []
        aux_inv_str = ", ".join(aux_invoice_numbers)
        
        for inv in invoices_data:
            filename = inv['filename']
            data = inv['data']
            inv_number = data.get('invoice_number', '')
            date = data.get('date', '')
            total_val = data.get('total_value', 0.0)
            total_weight = data.get('total_gross_weight', 0.0)
            
            items = data.get('items', [])
            if not items:
                all_items.append({
                    'Source File': filename,
                    'Invoice Number': inv_number,
                    'Date': date,
                    'Total Invoice Value': total_val,
                    'Total Invoice Weight': total_weight,
                    'Item Name': '',
                    'CDC': '',
                    'Quantity': 0,
                    'Unit': '',
                    'Unit Price': 0.0,
                    'Amount (USD)': 0.0,
                    'Item Gross Weight (kg)': 0.0,
                    'Customs Declaration Number': '',
                    'Custom Duty (USD)': 0.0,
                    'Special Tax (USD)': 0.0,
                    'Value Added Tax (USD)': 0.0,
                    'Auxiliary Invoice Numbers': aux_inv_str,
                    'Total Freight (USD)': round(total_freight_usd, 2),
                    'Total Insurance (USD)': round(total_insurance_usd, 2),
                    'Total Terminal Handling Charge (USD)': round(actual_thc_usd, 2),
                    'Total Port Charges (USD)': round(actual_port_charges_usd, 2),
                    'Total Clearance & Trucking (USD)': round(actual_clearance_trucking_usd, 2),
                    'Net Reimbursement (USD)': round(net_reimbursement_usd, 2),
                    'Prorated Insurance (USD)': 0.0,
                    'Prorated Net Reimbursement (USD)': 0.0,
                    'Prorated Freight (USD)': 0.0,
                    'Prorated THC (USD)': 0.0,
                    'Prorated Port Charges (USD)': 0.0,
                    'Prorated Clearance & Trucking (USD)': 0.0,
                    'Capitalized Value (USD)': 0.0
                })
            else:
                for item in items:
                    item_name = item.get('name', '')
                    cd_usd, st_usd, vat_usd, declaration_no = find_taxes(item_name)
                    
                    item_amt = item.get('amount_usd', 0.0)
                    item_weight = item.get('gross_weight_kg', 0.0)
                    
                    # Ratios
                    value_ratio = (item_amt / total_val) if total_val > 0 else 0.0
                    weight_ratio = (item_weight / total_weight) if total_weight > 0 else 0.0
                    
                    # Value-based allocations
                    prorated_insurance = total_insurance_usd * value_ratio
                    prorated_net_reimb = net_reimbursement_usd * value_ratio
                    
                    # Weight-based allocations
                    prorated_freight = total_freight_usd * weight_ratio
                    prorated_thc = actual_thc_usd * weight_ratio
                    prorated_port_charges = actual_port_charges_usd * weight_ratio
                    prorated_clearance_trucking = actual_clearance_trucking_usd * weight_ratio
                    
                    # Final Capitalized Value
                    # Includes: Base Price + Prorated Value-Based Costs + Prorated Weight-Based Costs + Exact Taxes (Including VAT as requested)
                    capitalized_value = (item_amt + prorated_insurance + prorated_net_reimb + 
                                         prorated_freight + prorated_thc + prorated_port_charges + prorated_clearance_trucking + 
                                         cd_usd + st_usd + vat_usd)
                                         
                    all_items.append({
                        'Source File': filename,
                        'Invoice Number': inv_number,
                        'Date': date,
                        'Total Invoice Value': total_val,
                        'Total Invoice Weight': total_weight,
                        'Item Name': item_name,
                        'CDC': item.get('cdc', ''),
                        'Quantity': item.get('qty', 0),
                        'Unit': item.get('unit', ''),
                        'Unit Price': item.get('unit_purchase_price', 0.0),
                        'Amount (USD)': item_amt,
                        'Item Gross Weight (kg)': item_weight,
                        'Customs Declaration Number': declaration_no,
                        'Custom Duty (USD)': round(cd_usd, 2),
                        'Special Tax (USD)': round(st_usd, 2),
                        'Value Added Tax (USD)': round(vat_usd, 2),
                        'Auxiliary Invoice Numbers': aux_inv_str,
                        'Total Freight (USD)': round(total_freight_usd, 2),
                        'Total Insurance (USD)': round(total_insurance_usd, 2),
                        'Total Terminal Handling Charge (USD)': round(actual_thc_usd, 2),
                        'Total Port Charges (USD)': round(actual_port_charges_usd, 2),
                        'Total Clearance & Trucking (USD)': round(actual_clearance_trucking_usd, 2),
                        'Net Reimbursement (USD)': round(net_reimbursement_usd, 2),
                        'Prorated Insurance (USD)': round(prorated_insurance, 2),
                        'Prorated Net Reimbursement (USD)': round(prorated_net_reimb, 2),
                        'Prorated Freight (USD)': round(prorated_freight, 2),
                        'Prorated THC (USD)': round(prorated_thc, 2),
                        'Prorated Port Charges (USD)': round(prorated_port_charges, 2),
                        'Prorated Clearance & Trucking (USD)': round(prorated_clearance_trucking, 2),
                        'Capitalized Value (USD)': round(capitalized_value, 2)
                    })

        if not found_invoices:
            self.stdout.write(self.style.WARNING(f"No commercial invoices found in {pdf_dir}."))
            return

        if all_items:
            df = pd.DataFrame(all_items)
            output_path = os.path.join(pdf_dir, "extracted_commercial_invoices.xlsx")
            try:
                df.to_excel(output_path, index=False)
                self.stdout.write(self.style.SUCCESS(f"Successfully exported {len(all_items)} rows to {output_path}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to save Excel file: {e}"))
        else:
            self.stdout.write(self.style.WARNING("No items extracted from documents."))

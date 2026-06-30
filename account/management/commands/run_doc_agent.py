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
        total_freight_net_usd = 0.0
        total_freight_gross_usd = 0.0
        total_insurance_net_usd = 0.0
        total_insurance_gross_usd = 0.0
        aux_thc_net_usd = 0.0
        aux_thc_gross_usd = 0.0
        aux_port_charges_net_usd = 0.0
        aux_port_charges_gross_usd = 0.0
        aux_clearance_trucking_net_usd = 0.0
        aux_clearance_trucking_gross_usd = 0.0
        
        aux_crosscheck_thc_do_gross = 0.0
        aux_crosscheck_port_gross = 0.0
        
        # Accumulators for reimbursement
        total_reimb_amount_usd = 0.0
        reimb_thc_usd = 0.0
        reimb_port_charges_usd = 0.0
        
        # Accumulators for unscrambled final outputs based on filename
        actual_thc_usd = 0.0
        actual_port_charges_usd = 0.0
        actual_clearance_trucking_usd = 0.0
        
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
                                    'item_no': item.get('item_no', 0),
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
                            self.stdout.write(f"DEBUG REIMBURSEMENT: {data}")
                            inv_num = data.get('invoice_number', '')
                            if inv_num:
                                aux_invoice_numbers.append(inv_num)
                            
                            total_reimb_amount_usd += data.get('total_reimbursement_usd', 0.0)
                            reimb_thc_usd += data.get('thc_usd', 0.0)
                            reimb_port_charges_usd += data.get('port_charges_usd', 0.0)
                    else:
                        # Process as auxiliary document
                        self.stdout.write(f"Processing Auxiliary Document: {filename}...")
                        data = agent.extract_auxiliary_costs(file_bytes, mime_type=mime_type, filename=filename)
                        if data:
                            inv_num = data.get('invoice_number', '')
                            if inv_num:
                                aux_invoice_numbers.append(inv_num)
                                
                            self.stdout.write(f"DEBUG {filename}: {data}")
                                
                            total_freight_net_usd += data.get('freight_charge_net_usd', 0.0)
                            total_freight_gross_usd += data.get('freight_charge_gross_usd', 0.0)
                            total_insurance_net_usd += data.get('insurance_net_usd', 0.0)
                            total_insurance_gross_usd += data.get('insurance_gross_usd', 0.0)
                            
                            aux_thc_net_usd += data.get('terminal_handling_charge_net_usd', 0.0)
                            aux_thc_gross_usd += data.get('terminal_handling_charge_gross_usd', 0.0)
                            aux_port_charges_net_usd += data.get('port_charges_net_usd', 0.0)
                            aux_port_charges_gross_usd += data.get('port_charges_gross_usd', 0.0)
                            aux_clearance_trucking_net_usd += data.get('clearance_trucking_net_usd', 0.0)
                            aux_clearance_trucking_gross_usd += data.get('clearance_trucking_gross_usd', 0.0)
                            
                            doc_net_sum = (data.get('terminal_handling_charge_net_usd', 0.0) + 
                                           data.get('port_charges_net_usd', 0.0) + 
                                           data.get('clearance_trucking_net_usd', 0.0))
                            
                            doc_gross_sum = (data.get('terminal_handling_charge_gross_usd', 0.0) + 
                                             data.get('port_charges_gross_usd', 0.0) + 
                                             data.get('clearance_trucking_gross_usd', 0.0))
                            
                            if "thc" in name_lower or "do" in name_lower:
                                aux_crosscheck_thc_do_gross += doc_gross_sum
                                actual_thc_usd += doc_net_sum
                            elif "port" in name_lower:
                                aux_crosscheck_port_gross += doc_gross_sum
                                actual_port_charges_usd += doc_net_sum
                            elif "clearance" in name_lower or "truck" in name_lower:
                                actual_clearance_trucking_usd += doc_net_sum
                            
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error processing {filename}: {e}"))
                    
        # Final Output Values are purely the NET amounts from the Auxiliary invoices
        
        # Aggregate Cross-Check (Targeted: THC/DO and Port Charge ONLY)
        total_target_aux_gross = aux_crosscheck_thc_do_gross + aux_crosscheck_port_gross
        total_target_reimb_billed = reimb_thc_usd + reimb_port_charges_usd
        
        is_error = (total_target_reimb_billed > 0 and abs(total_target_aux_gross - total_target_reimb_billed) > 2.0)
        
        if is_error:
            self.stdout.write(self.style.ERROR(
                f"ERROR: Targeted Cross-Check failed! "
                f"Total Aux THC+Port Gross ({total_target_aux_gross}) vs Total Reimb THC+Port ({total_target_reimb_billed})"
            ))
            net_reimbursement_usd = "ERROR"
        else:
            net_reimbursement_usd = max(0.0, total_reimb_amount_usd - (reimb_thc_usd + reimb_port_charges_usd))

        def find_taxes(item_name, item_no=0):
            if not item_name and item_no <= 0: return 0.0, 0.0, 0.0, ""
            
            # First, attempt an exact match by item_no if it is provided
            if item_no > 0:
                for cd in customs_data_list:
                    if cd.get('item_no') == item_no:
                        return cd['customs_duty_usd'], cd['special_tax_usd'], cd['vat_usd'], cd.get('declaration_number', '')
            
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

        # First, flatten all items to calculate global totals for proration and plugging
        all_flattened_items = []
        grand_total_val = 0.0
        grand_total_weight = 0.0
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
                all_flattened_items.append({
                    'is_empty': True,
                    'filename': filename,
                    'inv_number': inv_number,
                    'date': date,
                    'total_val': total_val,
                    'total_weight': total_weight,
                })
            else:
                for item in items:
                    item_amt = item.get('amount_usd', 0.0)
                    item_weight = item.get('gross_weight_kg', 0.0)
                    grand_total_val += item_amt
                    grand_total_weight += item_weight
                    
                    all_flattened_items.append({
                        'is_empty': False,
                        'filename': filename,
                        'inv_number': inv_number,
                        'date': date,
                        'total_val': total_val,
                        'total_weight': total_weight,
                        'item': item,
                        'item_amt': item_amt,
                        'item_weight': item_weight,
                    })
                    
        # Accumulators to prevent rounding imbalance via plugging on the last item
        allocated_insurance = 0.0
        allocated_net_reimb = 0.0
        allocated_freight = 0.0
        allocated_thc = 0.0
        allocated_port_charges = 0.0
        allocated_clearance_trucking = 0.0
        
        non_empty_items = [x for x in all_flattened_items if not x['is_empty']]
        
        all_items = []
        for i, row in enumerate(all_flattened_items):
            if row['is_empty']:
                all_items.append({
                    'Source File': row['filename'],
                    'Invoice Number': row['inv_number'],
                    'Date': row['date'],
                    'Total Invoice Value': row['total_val'],
                    'Total Invoice Weight': row['total_weight'],
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
                    'Total Freight (USD)': round(total_freight_net_usd, 2),
                    'Total Insurance (USD)': round(total_insurance_net_usd, 2),
                    'Total Terminal Handling Charge (USD)': round(actual_thc_usd, 2),
                    'Total Port Charges (USD)': round(actual_port_charges_usd, 2),
                    'Total Clearance & Trucking (USD)': round(actual_clearance_trucking_usd, 2),
                    'Net Reimbursement (USD)': round(net_reimbursement_usd, 2) if net_reimbursement_usd != "ERROR" else "ERROR",
                    'Prorated Insurance (USD)': 0.0,
                    'Prorated Net Reimbursement (USD)': 0.0,
                    'Prorated Freight (USD)': 0.0,
                    'Prorated THC (USD)': 0.0,
                    'Prorated Port Charges (USD)': 0.0,
                    'Prorated Clearance & Trucking (USD)': 0.0,
                    'Capitalized Value (USD)': 0.0
                })
            else:
                item = row['item']
                item_name = item.get('name', '')
                item_no = item.get('item_no', 0)
                cd_usd, st_usd, vat_usd, declaration_no = find_taxes(item_name, item_no)
                
                item_amt = row['item_amt']
                item_weight = row['item_weight']
                
                # Check if this is the very last non-empty item for plugging
                is_last_item = (row is non_empty_items[-1]) if non_empty_items else False
                
                if is_last_item:
                    prorated_insurance = round(total_insurance_net_usd - allocated_insurance, 2)
                    
                    if net_reimbursement_usd == "ERROR":
                        prorated_net_reimb = "ERROR"
                    else:
                        prorated_net_reimb = round(net_reimbursement_usd - allocated_net_reimb, 2)
                        
                    prorated_freight = round(total_freight_net_usd - allocated_freight, 2)
                    prorated_thc = round(actual_thc_usd - allocated_thc, 2)
                    prorated_port_charges = round(actual_port_charges_usd - allocated_port_charges, 2)
                    prorated_clearance_trucking = round(actual_clearance_trucking_usd - allocated_clearance_trucking, 2)
                else:
                    value_ratio = (item_amt / grand_total_val) if grand_total_val > 0 else 0.0
                    weight_ratio = (item_weight / grand_total_weight) if grand_total_weight > 0 else 0.0
                    
                    prorated_insurance = round(total_insurance_net_usd * value_ratio, 2)
                    allocated_insurance += prorated_insurance
                    
                    if net_reimbursement_usd == "ERROR":
                        prorated_net_reimb = "ERROR"
                    else:
                        prorated_net_reimb = round(net_reimbursement_usd * value_ratio, 2)
                        allocated_net_reimb += prorated_net_reimb
                        
                    prorated_freight = round(total_freight_net_usd * weight_ratio, 2)
                    allocated_freight += prorated_freight
                    
                    prorated_thc = round(actual_thc_usd * weight_ratio, 2)
                    allocated_thc += prorated_thc
                    
                    prorated_port_charges = round(actual_port_charges_usd * weight_ratio, 2)
                    allocated_port_charges += prorated_port_charges
                    
                    prorated_clearance_trucking = round(actual_clearance_trucking_usd * weight_ratio, 2)
                    allocated_clearance_trucking += prorated_clearance_trucking
                
                # Final Capitalized Value
                if prorated_net_reimb == "ERROR":
                    capitalized_value = "ERROR"
                else:
                    capitalized_value = (item_amt + prorated_insurance + prorated_net_reimb + 
                                         prorated_freight + prorated_thc + prorated_port_charges + prorated_clearance_trucking + 
                                         cd_usd + st_usd + vat_usd)
                                     
                all_items.append({
                    'Source File': row['filename'],
                    'Invoice Number': row['inv_number'],
                    'Date': row['date'],
                    'Total Invoice Value': row['total_val'],
                    'Total Invoice Weight': row['total_weight'],
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
                    'Total Freight (USD)': round(total_freight_net_usd, 2),
                    'Total Insurance (USD)': round(total_insurance_net_usd, 2),
                    'Total Terminal Handling Charge (USD)': round(actual_thc_usd, 2),
                    'Total Port Charges (USD)': round(actual_port_charges_usd, 2),
                    'Total Clearance & Trucking (USD)': round(actual_clearance_trucking_usd, 2),
                    'Net Reimbursement (USD)': round(net_reimbursement_usd, 2) if net_reimbursement_usd != "ERROR" else "ERROR",
                    'Prorated Insurance (USD)': round(prorated_insurance, 2),
                    'Prorated Net Reimbursement (USD)': round(prorated_net_reimb, 2) if prorated_net_reimb != "ERROR" else "ERROR",
                    'Prorated Freight (USD)': round(prorated_freight, 2),
                    'Prorated THC (USD)': round(prorated_thc, 2),
                    'Prorated Port Charges (USD)': round(prorated_port_charges, 2),
                    'Prorated Clearance & Trucking (USD)': round(prorated_clearance_trucking, 2),
                    'Capitalized Value (USD)': round(capitalized_value, 2) if capitalized_value != "ERROR" else "ERROR"
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

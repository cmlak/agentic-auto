import os
import uuid
import json
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from .models import Capitalization, AssetBatch
from .forms import CapitalizationUploadForm, CapitalizationForm, AssetBatchForm
from .filters import CapitalizationFilter, AssetBatchFilter
from django.forms import formset_factory
from agentic_orchestration.capitalization_agent import CapitalizationAgent
from django.core.paginator import Paginator
from django.http import HttpResponse
from .resources import CapitalizationResource, AssetBatchResource
import datetime
from tools.models import Vendor
import re

@login_required(login_url="register:login")
def capitalization_agent_upload_view(request):
    if request.method == 'POST':
        form = CapitalizationUploadForm(request.POST, request.FILES)
        if form.is_valid():
            commercial_files = request.FILES.getlist('commercial_invoices')
            customs_files = request.FILES.getlist('customs_declarations')
            freight_files = request.FILES.getlist('freight_insurance')
            aux_files = request.FILES.getlist('auxiliary_documents')
            
            batch_name = form.cleaned_data.get('batch_name') or f"BATCH-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_capitalization_v2')
            os.makedirs(temp_dir, exist_ok=True)
            
            saved_files = [] # List of (filepath, original_name, category)
            
            def save_category_files(file_list, category):
                for f in file_list:
                    unique_filename = f"{uuid.uuid4().hex}_{f.name}"
                    filepath = os.path.join(temp_dir, unique_filename)
                    with open(filepath, 'wb') as destination:
                        for chunk in f.chunks():
                            destination.write(chunk)
                    saved_files.append((filepath, f.name, category))

            save_category_files(commercial_files, 'commercial')
            save_category_files(customs_files, 'customs')
            save_category_files(freight_files, 'freight')
            save_category_files(aux_files, 'auxiliary')

            api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
            agent = CapitalizationAgent(api_key=api_key or "")
            
            customs_data_list = []
            invoices_data = []
            aux_invoice_numbers = []
            
            total_freight_net_usd = 0.0
            total_insurance_net_usd = 0.0
            actual_thc_usd = 0.0
            actual_port_charges_usd = 0.0
            actual_clearance_trucking_usd = 0.0
            
            total_wht_usd = 0.0
            
            total_reimb_amount_usd = 0.0
            reimb_thc_usd = 0.0
            reimb_port_charges_usd = 0.0
            
            global_exchange_rate = 1.0

            # Helper to find matching vendor
            def get_matching_vendor(vendor_name):
                if not vendor_name:
                    return None
                name_str = str(vendor_name).lower().replace('&', ' and ')
                target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()
                if not target_norm: return None
                
                for v in Vendor.objects.all():
                    v_norm = re.sub(r'[\W_]+', ' ', str(v.name).lower().replace('&', ' and ')).strip()
                    if target_norm in v_norm or v_norm in target_norm:
                        return v.id
                return None

            # Step 1: Process each file
            capitalization_aux_data = []  # For anything that DOES NOT attach to CI item
            
            for filepath, original_name, category in saved_files:
                print(f"[{category.upper()}] Starting processing for file: {original_name}...")
                name_lower = original_name.lower()
                mime_type = "application/pdf"
                if name_lower.endswith(('.jpg', '.jpeg')): mime_type = "image/jpeg"
                elif name_lower.endswith('.png'): mime_type = "image/png"
                
                try:
                    with open(filepath, 'rb') as f_obj:
                        file_bytes = f_obj.read()
                        
                    if category == 'customs':
                        print(f"[{category.upper()}] Extracting customs declaration...")
                        data = agent.extract_customs_declaration(file_bytes, mime_type=mime_type)
                        if data and 'items' in data:
                            exchange_rate = data.get('exchange_rate', 1.0)
                            if exchange_rate == 0: exchange_rate = 1.0
                            global_exchange_rate = exchange_rate
                            declaration_number = data.get('customs_declaration_number', '')
                            for item in data['items']:
                                customs_data_list.append({
                                    'declaration_number': declaration_number,
                                    'name': item.get('name', ''),
                                    'customs_duty_usd': item.get('customs_duty_riel', 0.0) / exchange_rate,
                                    'special_tax_usd': item.get('special_tax_riel', 0.0) / exchange_rate,
                                    'vat_usd': item.get('vat_riel', 0.0) / exchange_rate
                                })
                            # We no longer create a separate capitalization item for customs duty and VAT
                            print(f"[{category.upper()}] Successfully parsed Customs Declaration {declaration_number}.")

                    elif category == 'commercial':
                        print(f"[{category.upper()}] Extracting commercial invoice...")
                        data = agent.extract_commercial_invoice(file_bytes)
                        if data:
                            invoices_data.append({'filename': original_name, 'data': data})
                            print(f"[{category.upper()}] Successfully parsed Commercial Invoice: {data.get('invoice_number', 'Unknown')}.")
                            
                    elif "reimbursement" in name_lower or "re-imbursement" in name_lower:
                        print(f"[{category.upper()}] Extracting reimbursement document...")
                        data = agent.extract_reimbursement(file_bytes, mime_type=mime_type)
                        if data:
                            inv_num = data.get('invoice_number', '')
                            if inv_num: aux_invoice_numbers.append(inv_num)
                            
                            r_total = data.get('total_reimbursement_usd', 0.0)
                            r_thc = data.get('thc_usd', 0.0)
                            r_port = data.get('port_charges_usd', 0.0)
                            
                            total_reimb_amount_usd += r_total
                            reimb_thc_usd += r_thc
                            reimb_port_charges_usd += r_port
                            
                            net_r = max(0.0, r_total - (r_thc + r_port))
                            if net_r > 0:
                                capitalization_aux_data.append({
                                    'batch': batch_name,
                                    'date': None,
                                    'company': 'Reimbursement Provider',
                                    'vendor': None,
                                    'invoice_no': inv_num,
                                    'description': 'Reimbursement',
                                    'instruction': '',
                                    'capitalization': 'Reimbursement',
                                    'total_usd': round(net_r, 2),
                                    'vat_usd': 0.0,
                                    'vat_base_usd': 0.0,
                                    'wht_usd': 0.0,
                                    'vat_debit_account_id': 115010,
                                    'wht_debit_account_id': 725420,
                                    'debit_account_id': 181000,
                                    'credit_account_id': 200000,
                                })
                            if r_thc > 0:
                                capitalization_aux_data.append({
                                    'batch': batch_name,
                                    'date': None,
                                    'company': 'Reimbursement Provider',
                                    'vendor': None,
                                    'invoice_no': inv_num,
                                    'description': 'THC/DO',
                                    'instruction': '',
                                    'capitalization': 'THC / DO',
                                    'total_usd': round(r_thc, 2),
                                    'vat_usd': 0.0,
                                    'vat_base_usd': 0.0,
                                    'wht_usd': 0.0,
                                    'vat_debit_account_id': 115010,
                                    'wht_debit_account_id': 725420,
                                    'debit_account_id': 181000,
                                    'credit_account_id': 200000,
                                })
                            if r_port > 0:
                                capitalization_aux_data.append({
                                    'batch': batch_name,
                                    'date': None,
                                    'company': 'Reimbursement Provider',
                                    'vendor': None,
                                    'invoice_no': inv_num,
                                    'description': 'Port charges',
                                    'instruction': '',
                                    'capitalization': 'Port Charges',
                                    'total_usd': round(r_port, 2),
                                    'vat_usd': 0.0,
                                    'vat_base_usd': 0.0,
                                    'wht_usd': 0.0,
                                    'vat_debit_account_id': 115010,
                                    'wht_debit_account_id': 725420,
                                    'debit_account_id': 181000,
                                    'credit_account_id': 200000,
                                })
                            
                            # Reimbursement is attached prorated, so we just add to prorated bucket
                            print(f"[{category.upper()}] Successfully parsed Reimbursement {inv_num}.")
                            
                    else:
                        print(f"[{category.upper()}] Extracting auxiliary document (Freight/Insurance/THC)...")
                        data = agent.extract_auxiliary_costs(file_bytes, mime_type=mime_type)
                        if data and 'invoices' in data:
                            for inv in data['invoices']:
                                inv_num = inv.get('invoice_number', '')
                                if inv_num: aux_invoice_numbers.append(inv_num)
                                
                                provider_name = inv.get('provider_name', '')
                                provider_type = inv.get('provider_type', '')
                                local_agent = inv.get('local_agent_name', '')
                                
                                f_net = inv.get('freight_charge_net_usd', 0.0)
                                i_net = inv.get('insurance_net_usd', 0.0)
                                thc_net = inv.get('terminal_handling_charge_net_usd', 0.0)
                                port_net = inv.get('port_charges_net_usd', 0.0)
                                clear_net = inv.get('clearance_trucking_net_usd', 0.0)
                                
                                total_freight_net_usd += f_net
                                total_insurance_net_usd += i_net
                                actual_thc_usd += thc_net
                                actual_port_charges_usd += port_net
                                actual_clearance_trucking_usd += clear_net
                                
                                wht_amount = 0.0
                                wht_f = 0.0
                                wht_thc = 0.0
                                wht_port = 0.0
                                
                                # Rule 3c: 3% WHT
                                # If the invoice header does not include local agent name, the owner is obligated to withhold 3%WHT... and is recorded
                                # This only applies to non-resident/international carriers.
                                is_intl_carrier = ("International Carrier" in provider_type)
                                if is_intl_carrier and not local_agent:
                                    # Applies to non-resident, assume if no local agent, we pay 3% WHT on THC, DO, Container Imbalance, Freight
                                    # Calculate 3% of the international service amounts
                                    wht_f = f_net * 0.03
                                    wht_thc = thc_net * 0.03
                                    wht_port = port_net * 0.03
                                    wht_amount = wht_f + wht_thc + wht_port
                                    total_wht_usd += wht_amount
                                    
                                vendor_id = get_matching_vendor(provider_name)
                                
                                if f_net > 0 or i_net > 0:
                                    capitalization_aux_data.append({
                                        'batch': batch_name,
                                        'date': None,
                                        'company': provider_name,
                                        'vendor': vendor_id,
                                        'invoice_no': inv_num,
                                        'description': 'Freight and insurance',
                                        'instruction': '',
                                        'capitalization': 'Freight and Insurance',
                                        'total_usd': round(f_net + i_net, 2),
                                        'vat_usd': 0.0,
                                        'vat_base_usd': 0.0,
                                        'wht_usd': round(wht_f, 2),
                                        'vat_debit_account_id': 115010,
                                        'wht_debit_account_id': 725420,
                                        'debit_account_id': 181000,
                                        'credit_account_id': 200000,
                                    })
                                if thc_net > 0:
                                    capitalization_aux_data.append({
                                        'batch': batch_name,
                                        'date': None,
                                        'company': provider_name,
                                        'vendor': vendor_id,
                                        'invoice_no': inv_num,
                                        'description': 'THC/DO',
                                        'instruction': '',
                                        'capitalization': 'THC / DO',
                                        'total_usd': round(thc_net, 2),
                                        'vat_usd': 0.0,
                                        'vat_base_usd': 0.0,
                                        'wht_usd': round(wht_thc, 2),
                                        'vat_debit_account_id': 115010,
                                        'wht_debit_account_id': 725420,
                                        'debit_account_id': 181000,
                                        'credit_account_id': 200000,
                                    })
                                if port_net > 0:
                                    capitalization_aux_data.append({
                                        'batch': batch_name,
                                        'date': None,
                                        'company': provider_name,
                                        'vendor': vendor_id,
                                        'invoice_no': inv_num,
                                        'description': 'Port charges',
                                        'instruction': '',
                                        'capitalization': 'Port Charges',
                                        'total_usd': round(port_net, 2),
                                        'vat_usd': 0.0,
                                        'vat_base_usd': 0.0,
                                        'wht_usd': round(wht_port, 2),
                                        'vat_debit_account_id': 115010,
                                        'wht_debit_account_id': 725420,
                                        'debit_account_id': 181000,
                                        'credit_account_id': 200000,
                                    })
                                if clear_net > 0:
                                    capitalization_aux_data.append({
                                        'batch': batch_name,
                                        'date': None,
                                        'company': provider_name,
                                        'vendor': vendor_id,
                                        'invoice_no': inv_num,
                                        'description': 'Clearance and Trucking',
                                        'instruction': '',
                                        'capitalization': 'Clearance and Trucking',
                                        'total_usd': round(clear_net, 2),
                                        'vat_usd': 0.0,
                                        'vat_base_usd': 0.0,
                                        'wht_usd': 0.0,
                                        'vat_debit_account_id': 115010,
                                        'wht_debit_account_id': 725420,
                                        'debit_account_id': 181000,
                                        'credit_account_id': 200000,
                                    })
                                
                                # The 10% VAT on domestic services is excluded from capitalization.
                                # Since we use net amount for capitalization, VAT is automatically excluded.
                                
                            print(f"[{category.upper()}] Successfully parsed Auxiliary Document with invoices.")

                except Exception as e:
                    print(f"[ERROR] Failed to process {original_name}: {str(e)}")
                    pass

            net_reimbursement_usd = max(0.0, total_reimb_amount_usd - (reimb_thc_usd + reimb_port_charges_usd))

            def find_taxes(item_name):
                if not item_name: return 0.0, 0.0, 0.0, ""
                import re
                def get_tokens(text): return set(re.findall(r'\b[a-z0-9]+\b', text.lower()))
                item_tokens = get_tokens(item_name)
                if not item_tokens: return 0.0, 0.0, 0.0, ""
                best_match = None
                highest_score = 0.0
                for cd in customs_data_list:
                    cd_name = cd['name']
                    cd_tokens = get_tokens(cd_name)
                    if not cd_tokens: continue
                    if cd_name.lower().strip() in item_name.lower() or item_name.lower().strip() in cd_name.lower():
                        return cd['customs_duty_usd'], cd['special_tax_usd'], cd['vat_usd'], cd.get('declaration_number', '')
                    min_len = min(len(item_tokens), len(cd_tokens))
                    if min_len == 0: continue
                    score = len(item_tokens.intersection(cd_tokens)) / min_len
                    if score > highest_score:
                        highest_score = score
                        best_match = cd
                if highest_score >= 0.4 and best_match:
                    return best_match['customs_duty_usd'], best_match['special_tax_usd'], best_match['vat_usd'], best_match.get('declaration_number', '')
                return 0.0, 0.0, 0.0, ""

            asset_batch_data = []
            capitalization_ci_data = []
            
            for inv in invoices_data:
                filename = inv['filename']
                data = inv['data']
                inv_number = data.get('invoice_number', '')
                date_val = data.get('date', '')
                total_val = data.get('total_value', 0.0)
                total_weight = data.get('total_gross_weight', 0.0)
                vendor_name = data.get('vendor_name', '')
                reasoning = data.get('reasoning', '')
                
                matched_vendor_id = get_matching_vendor(vendor_name)
                
                # Try to parse date
                parsed_date = None
                if date_val:
                    try:
                        from dateutil import parser
                        parsed_date = parser.parse(date_val).date().isoformat()
                    except:
                        parsed_date = None
                
                items = data.get('items', [])
                for idx, item in enumerate(items):
                    item_name = item.get('name', '')
                    cd_usd, st_usd, vat_usd, declaration_no = find_taxes(item_name)
                    item_amt = item.get('amount_usd', 0.0)
                    item_weight = item.get('gross_weight_kg', 0.0)
                    
                    value_ratio = (item_amt / total_val) if total_val > 0 else 0.0
                    weight_ratio = (item_weight / total_weight) if total_weight > 0 else 0.0
                    
                    prorated_insurance = total_insurance_net_usd * value_ratio
                    prorated_net_reimb = net_reimbursement_usd * value_ratio
                    
                    if total_weight > 0 and item_weight > 0:
                        prorated_freight = total_freight_net_usd * weight_ratio
                        prorated_thc = actual_thc_usd * weight_ratio
                        prorated_port = actual_port_charges_usd * weight_ratio
                        prorated_truck = actual_clearance_trucking_usd * weight_ratio
                        prorated_wht = total_wht_usd * weight_ratio
                    else:
                        prorated_freight = total_freight_net_usd * value_ratio
                        prorated_thc = actual_thc_usd * value_ratio
                        prorated_port = actual_port_charges_usd * value_ratio
                        prorated_truck = actual_clearance_trucking_usd * value_ratio
                        prorated_wht = total_wht_usd * value_ratio

                    cap_value = round((item_amt + cd_usd + st_usd + prorated_insurance + 
                                prorated_net_reimb + prorated_freight + prorated_thc + 
                                prorated_port + prorated_truck + prorated_wht), 2)
                                
                    vat_base_usd = round((item_amt + cd_usd + st_usd), 2) if vat_usd > 0 else 0.0

                    # BATCH ID rules: Same base batch_id, with -X for items.
                    batch_id_suffix = f"{batch_name}-{len(asset_batch_data) + 1}"
                    
                    # Store Asset Batch Data
                    asset_batch_data.append({
                        'batch_id': batch_id_suffix,
                        'source_file': filename,
                        'invoice_number': inv_number,
                        'date': date_val if date_val else None,
                        'total_invoice_value': round(total_val, 2),
                        'total_invoice_weight': total_weight,
                        'item_name': item_name,
                        'cdc': item.get('cdc', ''),
                        'quantity': item.get('qty', 0.0),
                        'unit': item.get('unit', ''),
                        'unit_price': round(item.get('unit_purchase_price', 0.0), 2),
                        'amount_usd': round(item_amt, 2),
                        'item_gross_weight_kg': item_weight,
                        'customs_declaration_number': declaration_no,
                        'custom_duty_usd': round(cd_usd, 2),
                        'special_tax_usd': round(st_usd, 2),
                        'value_added_tax_usd': round(vat_usd, 2), # Note: VAT is separated
                        'auxiliary_invoice_numbers': ", ".join(set(aux_invoice_numbers)),
                        'total_freight_usd': round(total_freight_net_usd, 2),
                        'total_insurance_usd': round(total_insurance_net_usd, 2),
                        'total_thc_usd': round(actual_thc_usd, 2),
                        'total_port_charges_usd': round(actual_port_charges_usd, 2),
                        'total_clearance_trucking_usd': round(actual_clearance_trucking_usd, 2),
                        'net_reimbursement_usd': round(net_reimbursement_usd, 2),
                        'prorated_insurance_usd': round(prorated_insurance, 2),
                        'prorated_net_reimb_usd': round(prorated_net_reimb, 2),
                        'prorated_freight_usd': round(prorated_freight, 2),
                        'prorated_thc_usd': round(prorated_thc, 2),
                        'prorated_port_charges_usd': round(prorated_port, 2),
                        'prorated_clearance_trucking_usd': round(prorated_truck, 2),
                        'capitalized_value_usd': cap_value
                    })

                    # Create a Capitalization instance for this CI line item
                    # Attaching COP, VOP, WHT
                    ci_item_total_usd = round(item_amt + cd_usd + st_usd, 2)
                    capitalization_ci_data.append({
                        'batch': batch_name, # Base batch
                        'date': parsed_date,
                        'company': vendor_name,
                        'vendor': matched_vendor_id,
                        'invoice_no': inv_number,
                        'description': item_name,
                        'instruction': reasoning,
                        'capitalization': 'Commercial Invoice and Custom declaration',
                        'total_usd': ci_item_total_usd,
                        'vat_usd': round(vat_usd, 2),
                        'vat_base_usd': vat_base_usd,
                        'wht_usd': 0.0,
                        'vat_debit_account_id': 115010, # VAT Input
                        'wht_debit_account_id': 725420,
                        'debit_account_id': 181000, # Factory construction in progress
                        'credit_account_id': 200000, # Trade Payable
                    })

            # Clean up temp files
            for filepath, _, _ in saved_files:
                if os.path.exists(filepath):
                    try: os.remove(filepath)
                    except: pass

            request.session['capitalization_agent_results'] = {
                'capitalization_data': capitalization_ci_data + capitalization_aux_data,
                'asset_batch_data': asset_batch_data
            }
            messages.success(request, "Documents processed successfully via Agentic Pipeline.")
            return redirect('assets:capitalization_agent_review')
    else:
        form = CapitalizationUploadForm()
        
    return render(request, 'assets/capitalization_agent_upload.html', {'form': form})

@login_required(login_url="register:login")
def capitalization_agent_review_view(request):
    results = request.session.get('capitalization_agent_results', {})
    cap_data = results.get('capitalization_data', [])
    ab_data = results.get('asset_batch_data', [])
    
    CapFormSet = formset_factory(CapitalizationForm, extra=0, can_delete=True)
    ABFormSet = formset_factory(AssetBatchForm, extra=0)
    
    if request.method == 'POST':
        cap_formset = CapFormSet(request.POST, prefix='cap')
        ab_formset = ABFormSet(request.POST, prefix='ab')
        
        if cap_formset.is_valid() and ab_formset.is_valid():
            with transaction.atomic():
                for form in cap_formset:
                    if cap_formset._should_delete_form(form):
                        continue
                    if form.has_changed() or True:
                        instance = form.save(commit=False)
                        instance.user = request.user
                        instance.save()
                for form in ab_formset:
                    if form.has_changed() or True: # always save generated forms
                        instance = form.save(commit=False)
                        instance.user = request.user
                        instance.save()
            request.session.pop('capitalization_agent_results', None)
            messages.success(request, "Capitalization and AssetBatch instances created successfully via Agent.")
            return redirect('assets:capitalization_agent_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        cap_formset = CapFormSet(initial=cap_data, prefix='cap')
        ab_formset = ABFormSet(initial=ab_data, prefix='ab')
        
    return render(request, 'assets/capitalization_agent_review.html', {
        'cap_formset': cap_formset,
        'ab_formset': ab_formset
    })

@login_required(login_url="register:login")
def capitalization_agent_list_view(request):
    cap_qs = Capitalization.objects.all().order_by('-created_at')
    cap_filter = CapitalizationFilter(request.GET, queryset=cap_qs)
    cap_qs = cap_filter.qs
    
    cap_paginator = Paginator(cap_qs, 20)
    cap_page = request.GET.get('cap_page', 1)
    cap_page_obj = cap_paginator.get_page(cap_page)
    
    ab_qs = AssetBatch.objects.all().order_by('-created_at')
    ab_filter = AssetBatchFilter(request.GET, queryset=ab_qs)
    ab_qs = ab_filter.qs
    
    ab_paginator = Paginator(ab_qs, 20)
    ab_page = request.GET.get('ab_page', 1)
    ab_page_obj = ab_paginator.get_page(ab_page)
    
    if 'export_cap' in request.GET:
        dataset = CapitalizationResource().export(cap_qs)
        response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="Capitalization_Export_Agent.xlsx"'
        return response
        
    if 'export_ab' in request.GET:
        dataset = AssetBatchResource().export(ab_qs)
        response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="AssetBatch_Export_Agent.xlsx"'
        return response

    return render(request, 'assets/capitalization_agent_list.html', {
        'cap_filter': cap_filter,
        'cap_page_obj': cap_page_obj,
        'ab_filter': ab_filter,
        'ab_page_obj': ab_page_obj
    })

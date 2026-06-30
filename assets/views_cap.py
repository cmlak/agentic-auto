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
from .processors import DocAgent
from django.core.paginator import Paginator
from django.http import HttpResponse
from .resources import CapitalizationResource, AssetBatchResource
import datetime

@login_required(login_url="register:login")
def capitalization_upload_view(request):
    if request.method == 'POST':
        form = CapitalizationUploadForm(request.POST, request.FILES)
        if form.is_valid():
            commercial_files = request.FILES.getlist('commercial_invoices')
            customs_files = request.FILES.getlist('customs_declarations')
            freight_files = request.FILES.getlist('freight_insurance')
            aux_files = request.FILES.getlist('auxiliary_documents')
            
            batch_name = form.cleaned_data.get('batch_name') or f"BATCH-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_capitalization')
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
            agent = DocAgent(api_key=api_key or "")
            
            customs_data_list = []
            invoices_data = []
            aux_invoice_numbers = []
            total_freight_net_usd = 0.0
            total_insurance_net_usd = 0.0
            aux_thc_net_usd = 0.0
            aux_port_charges_net_usd = 0.0
            aux_clearance_trucking_net_usd = 0.0
            
            total_reimb_amount_usd = 0.0
            reimb_thc_usd = 0.0
            reimb_port_charges_usd = 0.0
            
            actual_thc_usd = 0.0
            actual_port_charges_usd = 0.0
            actual_clearance_trucking_usd = 0.0
            
            global_exchange_rate = 1.0

            # Step 1: Process each file
            capitalization_aux_data = []
            
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
                            capitalization_aux_data.append({
                                'batch': batch_name,
                                'description': f"Customs Declaration {declaration_number}",
                                'capitalization': 'Customs Tax & Duty',
                                'total_usd': sum([cd['customs_duty_usd'] + cd['special_tax_usd'] + cd['vat_usd'] for cd in customs_data_list[-len(data['items']):]]),
                                'vat_usd': sum([cd['vat_usd'] for cd in customs_data_list[-len(data['items']):]]),
                            })
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
                            total_reimb_amount_usd += data.get('total_reimbursement_usd', 0.0)
                            reimb_thc_usd += data.get('thc_usd', 0.0)
                            reimb_port_charges_usd += data.get('port_charges_usd', 0.0)
                            capitalization_aux_data.append({
                                'batch': batch_name,
                                'invoice_no': inv_num,
                                'description': "Reimbursement",
                                'capitalization': 'Reimbursement Cost',
                                'total_usd': data.get('total_reimbursement_usd', 0.0)
                            })
                            print(f"[{category.upper()}] Successfully parsed Reimbursement {inv_num}.")
                            
                    else:
                        print(f"[{category.upper()}] Extracting auxiliary document (Freight/Insurance/THC)...")
                        data = agent.extract_auxiliary_costs(file_bytes, mime_type=mime_type)
                        if data:
                            inv_num = data.get('invoice_number', '')
                            if inv_num: aux_invoice_numbers.append(inv_num)
                            
                            total_freight_net_usd += data.get('freight_charge_net_usd', 0.0)
                            total_insurance_net_usd += data.get('insurance_net_usd', 0.0)
                            
                            doc_net_sum = (data.get('terminal_handling_charge_net_usd', 0.0) + 
                                           data.get('port_charges_net_usd', 0.0) + 
                                           data.get('clearance_trucking_net_usd', 0.0))
                            
                            if "thc" in name_lower or "do" in name_lower:
                                actual_thc_usd += doc_net_sum
                            elif "port" in name_lower:
                                actual_port_charges_usd += doc_net_sum
                            elif "clearance" in name_lower or "truck" in name_lower:
                                actual_clearance_trucking_usd += doc_net_sum

                            capitalization_aux_data.append({
                                'batch': batch_name,
                                'invoice_no': inv_num,
                                'description': f"Auxiliary Document ({original_name})",
                                'capitalization': 'Auxiliary Costs',
                                'total_usd': data.get('freight_charge_net_usd', 0.0) + data.get('insurance_net_usd', 0.0) + doc_net_sum
                            })
                            print(f"[{category.upper()}] Successfully parsed Auxiliary Document {inv_num}.")

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
                    else:
                        prorated_freight = total_freight_net_usd * value_ratio
                        prorated_thc = actual_thc_usd * value_ratio
                        prorated_port = actual_port_charges_usd * value_ratio
                        prorated_truck = actual_clearance_trucking_usd * value_ratio

                    cap_value = (item_amt + cd_usd + st_usd + prorated_insurance + 
                                prorated_net_reimb + prorated_freight + prorated_thc + 
                                prorated_port + prorated_truck)

                    batch_id_suffix = f"{inv_number}-{idx+1}" if inv_number else f"{batch_name}-{idx+1}"
                    
                    # Store Asset Batch Data
                    asset_batch_data.append({
                        'batch_id': batch_id_suffix,
                        'source_file': filename,
                        'invoice_number': inv_number,
                        'date': date_val if date_val else None,
                        'total_invoice_value': total_val,
                        'total_invoice_weight': total_weight,
                        'item_name': item_name,
                        'cdc': item.get('cdc', ''),
                        'quantity': item.get('qty', 0.0),
                        'unit': item.get('unit', ''),
                        'unit_price': item.get('unit_purchase_price', 0.0),
                        'amount_usd': item_amt,
                        'item_gross_weight_kg': item_weight,
                        'customs_declaration_number': declaration_no,
                        'custom_duty_usd': cd_usd,
                        'special_tax_usd': st_usd,
                        'value_added_tax_usd': vat_usd,
                        'auxiliary_invoice_numbers': ", ".join(aux_invoice_numbers),
                        'total_freight_usd': total_freight_net_usd,
                        'total_insurance_usd': total_insurance_net_usd,
                        'total_thc_usd': actual_thc_usd,
                        'total_port_charges_usd': actual_port_charges_usd,
                        'total_clearance_trucking_usd': actual_clearance_trucking_usd,
                        'net_reimbursement_usd': net_reimbursement_usd,
                        'prorated_insurance_usd': prorated_insurance,
                        'prorated_net_reimb_usd': prorated_net_reimb,
                        'prorated_freight_usd': prorated_freight,
                        'prorated_thc_usd': prorated_thc,
                        'prorated_port_charges_usd': prorated_port,
                        'prorated_clearance_trucking_usd': prorated_truck,
                        'capitalized_value_usd': cap_value
                    })

                    # Create a Capitalization instance for this CI line item
                    capitalization_ci_data.append({
                        'batch': batch_id_suffix,
                        'invoice_no': inv_number,
                        'description': item_name,
                        'capitalization': 'Commercial Invoice Line Item',
                        'total_usd': item_amt
                    })

            # Clean up temp files
            for filepath, _, _ in saved_files:
                if os.path.exists(filepath):
                    try: os.remove(filepath)
                    except: pass

            request.session['capitalization_results'] = {
                'capitalization_data': capitalization_ci_data + capitalization_aux_data,
                'asset_batch_data': asset_batch_data
            }
            messages.success(request, "Documents processed successfully.")
            return redirect('assets:capitalization_review')
    else:
        form = CapitalizationUploadForm()
        
    return render(request, 'assets/capitalization_upload.html', {'form': form})

@login_required(login_url="register:login")
def capitalization_review_view(request):
    results = request.session.get('capitalization_results', {})
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
            request.session.pop('capitalization_results', None)
            messages.success(request, "Capitalization and AssetBatch instances created successfully.")
            return redirect('assets:capitalization_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        cap_formset = CapFormSet(initial=cap_data, prefix='cap')
        ab_formset = ABFormSet(initial=ab_data, prefix='ab')
        
    return render(request, 'assets/capitalization_review.html', {
        'cap_formset': cap_formset,
        'ab_formset': ab_formset
    })

@login_required(login_url="register:login")
def capitalization_list_view(request):
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
        response['Content-Disposition'] = 'attachment; filename="Capitalization_Export.xlsx"'
        return response
        
    if 'export_ab' in request.GET:
        dataset = AssetBatchResource().export(ab_qs)
        response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="AssetBatch_Export.xlsx"'
        return response

    return render(request, 'assets/capitalization_list.html', {
        'cap_filter': cap_filter,
        'cap_page_obj': cap_page_obj,
        'ab_filter': ab_filter,
        'ab_page_obj': ab_page_obj
    })

@login_required(login_url="register:login")
def capitalization_edit_view(request, pk):
    cap_instance = get_object_or_404(Capitalization, pk=pk)
    if request.method == 'POST':
        form = CapitalizationForm(request.POST, instance=cap_instance)
        if form.is_valid():
            form.save()
            messages.success(request, 'Capitalization updated successfully.')
            return redirect('assets:capitalization_list')
    else:
        form = CapitalizationForm(instance=cap_instance)
    return render(request, 'assets/capitalization_form.html', {'form': form, 'title': 'Edit Capitalization'})

@login_required(login_url="register:login")
def capitalization_delete_view(request, pk):
    cap_instance = get_object_or_404(Capitalization, pk=pk)
    if request.method == 'POST':
        cap_instance.delete()
        messages.success(request, 'Capitalization deleted successfully.')
        return redirect('assets:capitalization_list')
    return render(request, 'assets/capitalization_confirm_delete.html', {'object': cap_instance})


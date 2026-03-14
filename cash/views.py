import os
import tempfile
import json
import pandas as pd
from datetime import date
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required

from .forms import BankBatchUploadForm, BankFormSet, CashBatchUploadForm, CashReviewForm, CashFormSet
from .processors import GeminiABABankProcessor, GeminiCanadiaBankProcessor, ClientBCustomBankProcessor, \
    CashStandardExcelProcessor, GeminiReconciliationEngine
from .models import Bank, Cash
from tools.models import AICostLog, Client, Vendor, Purchase
from account.models import Account, JournalEntry, JournalLine, ClientPromptMemo, AccountMappingRule

BANK_PROCESSOR_MAP = {
    'aba_standard': GeminiABABankProcessor,
    'canadia_standard': GeminiCanadiaBankProcessor,
    'client_b_custom': ClientBCustomBankProcessor,
}

@login_required
def bank_ai_upload_view(request):
    """Upload Statement, Route via Strategy Map, Process, Reconcile, and Store."""
    if request.method == 'POST':
        request.session.pop('bank_report_path', None)
        
        form = BankBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            uploaded_pdf = form.cleaned_data['bank_pdf']
            batch_name = form.cleaned_data['batch_name']
            custom_prompt = form.cleaned_data.get('ai_prompt', '')
            selected_config = form.cleaned_data['processor_config']
            
            ProcessorStrategyClass = BANK_PROCESSOR_MAP.get(selected_config)
            
            if not ProcessorStrategyClass:
                messages.error(request, "Invalid processor configuration.")
                return redirect('cash:bank_upload')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                print("\n" + "="*50)
                print(f"🚀 STARTING BANK AI PROCESSING for {selected_client.name}")
                print("="*50)

                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = ProcessorStrategyClass(api_key=api_key)
                
                # 1. EXTRACT TRANSACTIONS
                print("\n[1/4] EXTRACTING TRANSACTIONS FROM PDF...")
                extracted_data, total_pages, costs = processor.process(
                    pdf_path=tmp_pdf_path, 
                    batch_name=batch_name,
                    custom_prompt=custom_prompt
                )
                print(f"✅ Extracted {len(extracted_data)} transactions across {total_pages} pages.")
                
                # 2. FETCH OPEN PURCHASES (SUBLEDGER)
                print("\n[2/4] FETCHING OPEN PURCHASES (SUBLEDGER)...")
                open_purchases = list(Purchase.objects.filter(
                    client=selected_client,
                    payment_status__in=['Open', 'Prepayment']
                ).values(
                    'id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status'
                ))
                print(f"✅ Found {len(open_purchases)} open purchase invoices for {selected_client.name}.")
                
                # 3. AI RECONCILIATION WITH 3-TIER PROMPT
                recon_costs = {"flash_cost": 0.0, "pro_cost": 0.0}
                print("\n[3/4] AI RECONCILIATION WITH 3-TIER PROMPT...")
                if extracted_data and open_purchases:
                    print(f"⚖️ Reconciling {len(extracted_data)} transactions against {len(open_purchases)} Open Invoices...")
                    reconciler = GeminiReconciliationEngine(api_key=api_key, context_account='100010')
                    
                    tx_data_str = json.dumps(extracted_data, default=str)
                    pur_data_str = json.dumps(open_purchases, default=str)
                    
                    # --- CONSTRUCT TIER 2 FROM DATABASE MODELS ---
                    tier_2_rules = ""
                    
                    if custom_prompt:
                        tier_2_rules += f"User Override Instructions:\n{custom_prompt}\n\n"
                        
                    client_memos = ClientPromptMemo.objects.filter(client=selected_client)
                    if client_memos.exists():
                        tier_2_rules += "CLIENT SPECIFIC ACCOUNTING MEMOS:\n"
                        for memo in client_memos:
                            tier_2_rules += f"- {memo.memo_text}\n"
                        tier_2_rules += "\n"

                    mapping_rules = AccountMappingRule.objects.filter(client=selected_client).select_related('account')
                    if mapping_rules.exists():
                        tier_2_rules += "MANDATORY KEYWORD MAPPINGS:\n"
                        for rule in mapping_rules:
                            tier_2_rules += f"- If description contains '{rule.trigger_keywords}', you MUST consider Account: {rule.account.account_id}. Reasoning: {rule.ai_guideline}\n"
                    # ----------------------------------------------

                    mappings, recon_costs = reconciler.reconcile(
                        transactions_data=tx_data_str, 
                        open_purchases_data=pur_data_str,
                        prompt_memo=tier_2_rules
                    )
                    print(f"✅ AI returned {len(mappings)} reconciliation mappings.")
                    mapping_dict = {str(m.transaction_id): m for m in mappings}
                    
                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            item['matched_purchase_id'] = match.matched_purchase_id
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            # Strict Fallback if AI fails to map a row
                            item['credit_account_id'] = '100010'
                            item['debit_account_id'] = '120000' if item.get('credit', 0) > 0 else '400000'
                else:
                    print("⚠️ Skipping reconciliation: No extracted data or no open purchases.")
                    for item in extracted_data:
                        item['credit_account_id'] = '100010'
                        item['debit_account_id'] = '120000' if item.get('credit', 0) > 0 else '400000'

                # 4. LOG COST TO CENTRALIZED TABLE
                print("\n[4/4] LOGGING AI COSTS AND FINALIZING...")
                total_flash = costs.get('flash_cost', 0) + recon_costs.get('flash_cost', 0)
                total_pro = costs.get('pro_cost', 0) + recon_costs.get('pro_cost', 0)

                AICostLog.objects.create(
                    file_name=uploaded_pdf.name, 
                    total_pages=total_pages, 
                    flash_cost=total_flash, 
                    pro_cost=total_pro, 
                    total_cost=total_flash + total_pro
                )
                
                request.session['extracted_bank'] = extracted_data
                request.session['bank_metadata'] = {
                    'file_name': uploaded_pdf.name,
                    'batch_name': batch_name, 
                    'client_id': selected_client.id,     
                    'client_name': selected_client.name,
                    'config_used': dict(form.fields['processor_config'].choices).get(selected_config),
                    'total_pages': total_pages,
                    'costs': {'flash_cost': total_flash, 'pro_cost': total_pro}
                }
                print("✅ Process complete. Redirecting to review screen.")
                print("="*50 + "\n")
                return redirect('cash:bank_review')
                
            except Exception as e:
                messages.error(request, f"Bank AI Error: {str(e)}")
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BankBatchUploadForm()
    return render(request, 'bank_upload.html', {'form': form})


@login_required
def bank_review_view(request):
    """Review Bank Extracted Data, Link Purchases, and Post explicitly defined Journal Entries."""
    extracted_data = request.session.get('extracted_bank', [])
    metadata = request.session.get('bank_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('cash:bank_upload')

    client_id = metadata.get('client_id')
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        formset = BankFormSet(request.POST, form_kwargs={'account_choices': account_choices}) 
        if formset.is_valid():
            saved_instances = []
            
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    instance = form.save(commit=False)
                    instance.client_id = client_id 
                    instance.batch = metadata.get('batch_name')
                    
                    # --- STATUS TRIGGER ---
                    matched_id = form.cleaned_data.get('matched_purchase_id')
                    if matched_id:
                        try:
                            purchase_to_pay = Purchase.objects.get(id=matched_id, client_id=client_id)
                            instance.matched_purchase = purchase_to_pay
                            purchase_to_pay.payment_status = 'Paid'
                            purchase_to_pay.save()
                        except Purchase.DoesNotExist:
                            pass 

                    instance.save()
                    saved_instances.append(instance)

                    # --- BALANCED DOUBLE-ENTRY POSTING ---
                    dr_acct_id = str(form.cleaned_data.get('debit_account_id') or '120000')
                    cr_acct_id = str(form.cleaned_data.get('credit_account_id') or '100010')
                    
                    dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Liability'})
                    cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Asset'})

                    amount = instance.debit if instance.debit > 0 else instance.credit

                    je = JournalEntry.objects.create(
                        client_id=client_id,
                        date=instance.date or date.today(),
                        description=f"Bank Transaction: {instance.counterparty or instance.purpose}",
                        reference_number=instance.bank_ref_id,
                        bank=instance
                    )

                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description="Debit leg")
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description="Credit leg")

            if saved_instances:
                report_data = list(Bank.objects.filter(id__in=[p.id for p in saved_instances]).values())
                df_report = pd.DataFrame(report_data)
                for col in df_report.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_report[col]) and df_report[col].dt.tz is not None:
                        df_report[col] = df_report[col].dt.tz_localize(None)

                media_dir = os.path.join(settings.BASE_DIR, 'media')
                os.makedirs(media_dir, exist_ok=True)
                report_path = os.path.join(media_dir, 'bank_process_report.xlsx')
                df_report.to_excel(report_path, index=False, engine='openpyxl')
                request.session['bank_report_path'] = report_path 
            
            request.session.pop('extracted_bank', None)
            request.session.pop('bank_metadata', None)
            messages.success(request, f"Successfully saved {len(saved_instances)} bank transactions and posted Journal Entries!")
            return redirect('cash:bank_download') 
        else:
            messages.error(request, "Validation failed. Please check the form for errors.")
            
    else:
        formset = BankFormSet(initial=extracted_data, form_kwargs={'account_choices': account_choices})

    return render(request, 'bank_review.html', {'formset': formset, 'metadata': metadata})

def bank_download_view(request):
    file_path = request.session.get('bank_report_path')
    return render(request, 'bank_download.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_bank_report(request):
    file_path = request.session.get('bank_report_path')
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = 'attachment; filename="bank_process_report.xlsx"'
            return response
    return redirect('cash:bank_upload')

###

CASH_PROCESSOR_MAP = {
    'standard_excel': CashStandardExcelProcessor,
}

# ====================================================================
# --- CASH BOOK WORKFLOW ---
# ====================================================================

@login_required
def cash_upload_view(request):
    """Upload Cash Excel, Route via Strategy Map, Process, Reconcile, and Store."""
    if request.method == 'POST':
        request.session.pop('cash_report_path', None)
        
        form = CashBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            uploaded_file = form.cleaned_data['cash_file']
            batch_name = form.cleaned_data['batch_name']
            selected_config = form.cleaned_data['processor_config']
            
            ProcessorStrategyClass = CASH_PROCESSOR_MAP.get(selected_config)
            
            if not ProcessorStrategyClass:
                messages.error(request, "Invalid processor configuration.")
                return redirect('cash:cash_upload')
            
            _, file_ext = os.path.splitext(uploaded_file.name)
            if file_ext.lower() == '.xls': ext = '.xls'
            elif file_ext.lower() == '.csv': ext = '.csv'
            else: ext = '.xlsx'

            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                for chunk in uploaded_file.chunks():
                    tmp_file.write(chunk)
                tmp_file_path = tmp_file.name

            try:
                print("\n" + "="*50)
                print(f"🚀 STARTING CASH BOOK PROCESSING for {selected_client.name}")
                print("="*50)
                
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = ProcessorStrategyClass(api_key=api_key)
                
                # 1. EXTRACT TRANSACTIONS
                print("\n[1/4] EXTRACTING CASH TRANSACTIONS FROM EXCEL...")
                extracted_data, total_pages, costs = processor.process(
                    file_path=tmp_file_path, 
                    client_id=selected_client.id,
                    batch_name=batch_name
                )
                print(f"✅ Extracted {len(extracted_data)} cash transactions.")
                
                # 2. FETCH OPEN PURCHASES (SUBLEDGER)
                print("\n[2/4] FETCHING OPEN PURCHASES (SUBLEDGER)...")
                for i, item in enumerate(extracted_data):
                    if not item.get('sys_id'):
                        item['sys_id'] = f"CASH-{i+1}"
                        
                open_purchases = list(Purchase.objects.filter(
                    client=selected_client,
                    payment_status__in=['Open', 'Prepayment']
                ).values(
                    'id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status'
                ))
                print(f"✅ Found {len(open_purchases)} open purchase invoices for {selected_client.name}.")
                
                # 3. AI RECONCILIATION WITH 3-TIER PROMPT
                recon_costs = {"flash_cost": 0.0, "pro_cost": 0.0}
                print("\n[3/4] AI RECONCILIATION WITH 3-TIER PROMPT...")
                if extracted_data and open_purchases:
                    print(f"⚖️ Reconciling {len(extracted_data)} cash lines against {len(open_purchases)} Open Invoices...")
                    reconciler = GeminiReconciliationEngine(api_key=api_key, context_account='100000')
                    
                    tx_data_str = json.dumps(extracted_data, default=str)
                    pur_data_str = json.dumps(open_purchases, default=str)
                    
                    # --- CONSTRUCT TIER 2 FROM DATABASE MODELS ---
                    tier_2_rules = ""
                    
                    client_memos = ClientPromptMemo.objects.filter(client=selected_client)
                    if client_memos.exists():
                        tier_2_rules += "CLIENT SPECIFIC ACCOUNTING MEMOS:\n"
                        for memo in client_memos:
                            tier_2_rules += f"- {memo.memo_text}\n"
                        tier_2_rules += "\n"

                    mapping_rules = AccountMappingRule.objects.filter(client=selected_client).select_related('account')
                    if mapping_rules.exists():
                        tier_2_rules += "MANDATORY KEYWORD MAPPINGS:\n"
                        for rule in mapping_rules:
                            tier_2_rules += f"- If description contains '{rule.trigger_keywords}', you MUST consider Account: {rule.account.account_id}. Reasoning: {rule.ai_guideline}\n"
                    # ----------------------------------------------
                    
                    mappings, recon_costs = reconciler.reconcile(
                        transactions_data=tx_data_str, 
                        open_purchases_data=pur_data_str,
                        prompt_memo=tier_2_rules
                    )
                    print(f"✅ AI returned {len(mappings)} reconciliation mappings.")
                    mapping_dict = {str(m.transaction_id): m for m in mappings}
                    
                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            item['matched_purchase_id'] = match.matched_purchase_id
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            item['credit_account_id'] = '100000'
                            item['debit_account_id'] = '120000' if item.get('credit', 0) > 0 else '400000'
                else:
                    print("⚠️ Skipping reconciliation: No extracted data or no open purchases.")
                    for item in extracted_data:
                        item['credit_account_id'] = '100000'
                        item['debit_account_id'] = '120000' if item.get('credit', 0) > 0 else '400000'
                
                print("\n[4/4] LOGGING AI COSTS AND FINALIZING...")
                total_flash = costs.get('flash_cost', 0) + recon_costs.get('flash_cost', 0)
                total_pro = costs.get('pro_cost', 0) + recon_costs.get('pro_cost', 0)

                AICostLog.objects.create(
                    file_name=uploaded_file.name, 
                    total_pages=total_pages, 
                    flash_cost=total_flash, 
                    pro_cost=total_pro, 
                    total_cost=total_flash + total_pro
                )

                request.session['extracted_cash'] = extracted_data
                request.session['cash_metadata'] = {
                    'file_name': uploaded_file.name,
                    'batch_name': batch_name, 
                    'client_id': selected_client.id,
                    'client_name': selected_client.name,
                    'total_pages': total_pages,
                    'costs': {'flash_cost': total_flash, 'pro_cost': total_pro}
                }
                print("✅ Process complete. Redirecting to review screen.")
                print("="*50 + "\n")
                return redirect('cash:cash_review')
                
            except Exception as e:
                messages.error(request, f"Processing Error: {str(e)}")
            finally:
                if os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)
    else:
        form = CashBatchUploadForm()
    return render(request, 'cash_upload.html', {'form': form})


@login_required
def cash_review_view(request):
    """Review Cash Extracted Data, Resolve Vendors, Link Purchases, and Post explicitly defined Journal Entries."""
    extracted_data = request.session.get('extracted_cash', [])
    metadata = request.session.get('cash_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('cash:cash_upload')

    client_id = metadata.get('client_id')
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item.get('company', 'Unknown')}"))
    
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors
    
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    page_number = request.GET.get('page', 1)
    items_per_page = 20
    paginator = Paginator(extracted_data, items_per_page)
    page_obj = paginator.get_page(page_number)
    current_slice = page_obj.object_list
    start_sequence = (page_obj.number - 1) * items_per_page

    if request.method == 'POST':
        formset = CashFormSet(request.POST, form_kwargs={'dynamic_choices': dynamic_choices, 'account_choices': account_choices, 'start_sequence': start_sequence})
        
        if formset.is_valid():
            saved_instances = []
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    instance = form.save(commit=False)
                    instance.client_id = client_id
                    
                    # 1. Resolve Vendor
                    vc = form.cleaned_data.get('vendor_choice')
                    raw_name = form.cleaned_data.get('company', 'Unknown Vendor')
                    if str(vc).startswith('TEMP_'):
                        new_vid = vc.replace('TEMP_', '')
                        new_vendor, _ = Vendor.objects.get_or_create(client_id=client_id, vendor_id=new_vid, defaults={'name': raw_name})
                        instance.vendor = new_vendor
                    elif vc:
                        try: instance.vendor = Vendor.objects.get(id=int(vc), client_id=client_id)
                        except (ValueError, Vendor.DoesNotExist): pass
                            
                    # --- 2. THE TRIGGER: LINK INVOICE & UPDATE STATUS ---
                    matched_id = form.cleaned_data.get('matched_purchase_id')
                    if matched_id:
                        try:
                            purchase_to_pay = Purchase.objects.get(id=matched_id, client_id=client_id)
                            instance.matched_purchase = purchase_to_pay
                            purchase_to_pay.payment_status = 'Paid'
                            purchase_to_pay.save()
                        except Purchase.DoesNotExist:
                            pass

                    instance.save()
                    saved_instances.append(instance)

                    # --- 3. BALANCED DOUBLE-ENTRY POSTING ---
                    dr_acct_id = str(form.cleaned_data.get('debit_account_id') or '120000') 
                    cr_acct_id = str(form.cleaned_data.get('credit_account_id') or '100000') 
                    
                    dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Liability'})
                    cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Asset'})

                    amount = instance.debit if instance.debit > 0 else instance.credit

                    je = JournalEntry.objects.create(
                        client_id=client_id,
                        date=instance.date or date.today(),
                        description=f"Cash Transaction: {instance.description or 'Cash Book Entry'}",
                        reference_number=instance.voucher_no,
                        cash=instance
                    )

                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description="Debit leg")
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description="Credit leg")

            if saved_instances:
                report_data = list(Cash.objects.filter(id__in=[p.id for p in saved_instances]).values())
                df_report = pd.DataFrame(report_data)
                media_dir = os.path.join(settings.BASE_DIR, 'media')
                os.makedirs(media_dir, exist_ok=True)
                report_path = os.path.join(media_dir, 'cash_process_report.xlsx')
                df_report.to_excel(report_path, index=False, engine='openpyxl')
                request.session['cash_report_path'] = report_path 

            # Remove processed items from session list
            try: current_page_num = int(request.GET.get('page', 1))
            except ValueError: current_page_num = 1
            
            start_index = (current_page_num - 1) * items_per_page
            end_index = start_index + items_per_page
            del extracted_data[start_index:end_index]
            
            request.session['extracted_cash'] = extracted_data
            request.session.modified = True

            if not extracted_data:
                request.session.pop('extracted_cash', None)
                request.session.pop('cash_metadata', None)
                messages.success(request, f"Success! All items saved. Process Complete.")
                return redirect('cash:cash_download') 
            else:
                messages.success(request, f"Saved {len(saved_instances)} items. {len(extracted_data)} remaining.")
                return redirect('cash:cash_review')
        else:
            messages.error(request, "Validation failed. Please check the form for errors.")
            
    else:
        formset = CashFormSet(initial=current_slice, form_kwargs={'dynamic_choices': dynamic_choices, 'account_choices': account_choices, 'start_sequence': start_sequence})

    return render(request, 'cash_review.html', {'formset': formset, 'metadata': metadata, 'page_obj': page_obj})
    
def cash_download_view(request):
    file_path = request.session.get('cash_report_path')
    return render(request, 'cash_download.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_cash_report(request):
    file_path = request.session.get('cash_report_path')
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = 'attachment; filename="cash_process_report.xlsx"'
            return response
    return redirect('cash:cash_upload')
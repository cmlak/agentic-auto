import os
import tempfile
import json
import pandas as pd
from datetime import date
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib import messages
from django.http import HttpResponse
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse_lazy, reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, UpdateView, DeleteView
from django.db import transaction

from .forms import BankBatchUploadForm, BankFormSet, CashBatchUploadForm, CashReviewForm, CashFormSet, ManualBankEntryForm, ManualCashEntryForm
from .processors import GeminiABABankProcessor, GeminiCanadiaBankProcessor, ClientBCustomBankProcessor, \
    CashStandardExcelProcessor, GeminiReconciliationEngine
from .models import Bank, Cash
from .resources import BankResource, CashResource
from .filters import BankFilter, CashFilter
from tools.models import AICostLog, Client, Vendor, Purchase
from tools.forms import ClientSelectionForm
from account.models import Account, JournalEntry, JournalLine, ClientPromptMemo, AccountMappingRule
from register.models import Profile

BANK_PROCESSOR_MAP = {
    'aba_standard': GeminiABABankProcessor,
    'canadia_standard': GeminiCanadiaBankProcessor,
    'client_b_custom': ClientBCustomBankProcessor,
}

@login_required
def bank_ai_upload_view(request):
    user = request.user

    if request.method == 'POST':
        request.session.pop('bank_report_path', None)
        
        form = BankBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=selected_client.id).exists():
                        has_access = True
                except Profile.DoesNotExist:
                    pass
            if not has_access:
                messages.error(request, "You do not have permission to upload data for this client.")
                return redirect('cash:bank_upload')
                
            uploaded_pdf = form.cleaned_data['bank_pdf']
            batch_name = form.cleaned_data['batch_name']
            custom_prompt = form.cleaned_data.get('ai_prompt', '')
            selected_config = form.cleaned_data['processor_config']
            
            # --- 1. PARSE BANK PAYMENT EXPLANATION FILE ---
            custom_rules_file = form.cleaned_data.get('custom_rules_file')
            if custom_rules_file:
                print(f"📄 Received bank explanation file: {custom_rules_file.name}")
                try:
                    if custom_rules_file.name.endswith('.csv'): 
                        df_rules = pd.read_csv(custom_rules_file)
                    elif custom_rules_file.name.endswith('.xls'): 
                        df_rules = pd.read_excel(custom_rules_file, engine='xlrd')
                    else: 
                        df_rules = pd.read_excel(custom_rules_file, engine='openpyxl')
                    
                    rules_md = df_rules.to_csv(index=False)
                    custom_prompt += f"\n\n[SUPPLEMENTARY ROUTING DATA]\n{rules_md}"
                    print("✅ Successfully parsed supplementary rules file.")
                except Exception as e:
                    messages.warning(request, f"Warning: Could not parse supplementary file. {str(e)}")

            # --- 2. PARSE HISTORICAL GL FILE ---
            historical_gl_file = form.cleaned_data.get('historical_gl_file')
            hist_gl_md = ""
            if historical_gl_file:
                print(f"📚 Received Historical GL file: {historical_gl_file.name}")
                try:
                    if historical_gl_file.name.endswith('.csv'): 
                        df_gl = pd.read_csv(historical_gl_file)
                    elif historical_gl_file.name.endswith('.xls'): 
                        df_gl = pd.read_excel(historical_gl_file, engine='xlrd')
                    else: 
                        df_gl = pd.read_excel(historical_gl_file, engine='openpyxl')
                    
                    hist_gl_md = df_gl.to_csv(index=False)
                    print("✅ Successfully parsed Historical General Ledger.")
                except Exception as e:
                    messages.warning(request, f"Warning: Could not parse Historical GL. {str(e)}")

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
                
                # 3. AI RECONCILIATION (WITH HISTORICAL GL)
                recon_costs = {"flash_cost": 0.0, "pro_cost": 0.0}
                print("\n[3/4] AI RECONCILIATION WITH 3-TIER PROMPT & HISTORICAL DATA...")
                if extracted_data:
                    reconciler = GeminiReconciliationEngine(api_key=api_key, context_account='100010')
                    
                    tx_data_str = json.dumps(extracted_data, default=str)
                    pur_data_str = json.dumps(open_purchases, default=str)
                    
                    tier_2_rules = ""
                    if custom_prompt:
                        tier_2_rules += f"User Override & Data Instructions:\n{custom_prompt}\n\n"
                        
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

                    # ---> PASS THE HISTORICAL GL DATA TO THE RECONCILER <---
                    mappings, recon_costs = reconciler.reconcile(
                        transactions_data=tx_data_str, 
                        open_purchases_data=pur_data_str,
                        historical_gl_data=hist_gl_md,
                        prompt_memo=tier_2_rules
                    )
                    print(f"\n✅ AI returned {len(mappings)} reconciliation mappings. Processing transactions...")
                    mapping_dict = {str(m.transaction_id): m for m in mappings}
                    
                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        print(f"   🔹 Processing Transaction [Sys ID: {sys_id}] | Counterparty: {item.get('counterparty', 'N/A')} | In: {item.get('debit', 0)} | Out: {item.get('credit', 0)}")
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            print(f"      ✨ AI Reconciled -> Dr: {match.debit_account_id} | Cr: {match.credit_account_id} | Reason: {match.reasoning}")
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            if hasattr(match, 'matched_purchase_ids') and match.matched_purchase_ids:
                                item['matched_purchase_ids'] = ",".join(map(str, match.matched_purchase_ids))
                            else:
                                item['matched_purchase_ids'] = ""
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            print(f"      ⚠️ No exact AI mapping. Applying default accounts.")
                            if item.get('credit', 0) > 0:  
                                item['credit_account_id'] = '100010'
                                item['debit_account_id'] = '120000'
                            else:  
                                item['debit_account_id'] = '100010'
                                item['credit_account_id'] = '400000'

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
                return redirect('cash:bank_review')
                
            except Exception as e:
                messages.error(request, f"Bank AI Error: {str(e)}")
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BankBatchUploadForm()
        
        # Dynamically limit the dropdown to ONLY the clients the user manages
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()
    return render(request, 'bank_upload.html', {'form': form})

@login_required
def bank_review_view(request):
    """Review Bank Extracted Data, Link Purchases, and Post explicitly defined Journal Entries."""
    extracted_data = request.session.get('extracted_bank', [])
    metadata = request.session.get('bank_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('cash:bank_upload')

    client_id = metadata.get('client_id')
    
    user = request.user
    has_access = user.is_staff or user.is_superuser
    if not has_access and client_id:
        try:
            if user.profile.clients.filter(id=client_id).exists():
                has_access = True
        except Profile.DoesNotExist:
            pass
    if not has_access:
        return HttpResponseForbidden("You do not have permission to review this client's data.")

    # Ensure default accounts exist so the frontend dropdown choices won't be blank
    Account.objects.get_or_create(client_id=client_id, account_id='100010', defaults={'name': 'Cash in Bank', 'account_type': 'Asset'})
    Account.objects.get_or_create(client_id=client_id, account_id='120000', defaults={'name': 'Prepayment', 'account_type': 'Asset'})
    Account.objects.get_or_create(client_id=client_id, account_id='400000', defaults={'name': 'Accounts Receivable', 'account_type': 'Asset'})

    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        formset = BankFormSet(request.POST, form_kwargs={'account_choices': account_choices}) 
        if formset.is_valid():
            saved_instances = []
            
            try:
                with transaction.atomic():
                    for form in formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            instance = form.save(commit=False)
                            instance.client_id = client_id 
                            instance.batch = metadata.get('batch_name')
                            
                            # --- SANITIZE REMARK ---
                            # Enforce maximum character limit to prevent database errors
                            if instance.remark and len(instance.remark) > 250:
                                instance.remark = instance.remark[:247] + '...'
                            
                            # --- STATUS TRIGGER ---
                            matched_ids_str = form.cleaned_data.get('matched_purchase_ids')
                            if matched_ids_str:
                                matched_ids = [int(id_str) for id_str in matched_ids_str.split(',') if id_str.isdigit()]
                                
                                if matched_ids:
                                    # Link the first purchase to the bank record for reference
                                    try:
                                        first_purchase = Purchase.objects.get(id=matched_ids[0], client_id=client_id)
                                        instance.matched_purchase = first_purchase
                                    except Purchase.DoesNotExist:
                                        pass
                                
                                # Mark ALL matched purchases as 'Paid'
                                purchases_to_pay = Purchase.objects.filter(id__in=matched_ids, client_id=client_id)
                                purchases_to_pay.update(payment_status='Paid')
 
                            instance.save()
                            saved_instances.append(instance)

                            # --- BALANCED DOUBLE-ENTRY POSTING ---
                            is_money_out = instance.credit > 0
                            default_dr = '120000' if is_money_out else '100010'
                            default_cr = '100010' if is_money_out else '400000'
                            
                            dr_acct_id = str(instance.debit_account_id or default_dr)
                            cr_acct_id = str(instance.credit_account_id or default_cr)
                            
                            dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Asset'})
                            cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Liability'})

                            amount = instance.debit if instance.debit > 0 else instance.credit
                            
                            je_desc = f"Bank Transaction: {instance.counterparty or instance.purpose}"
                            if instance.instruction:
                                clean_reason = str(instance.instruction).replace('AI Reconciled: ', '').strip()
                                je_desc = f"Reason: {clean_reason}"
                                if matched_ids_str:
                                    je_desc += f", matched with open purchase IDs {matched_ids_str}."

                            # Ensure descriptions safely fit within database column limits
                            safe_je_desc = je_desc[:500] if je_desc else "Bank Transaction"

                            je = JournalEntry.objects.create(
                                client_id=client_id,
                                date=instance.date or date.today(),
                                description=safe_je_desc,
                                reference_number=instance.bank_ref_id,
                                bank=instance
                            )

                            JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=safe_je_desc[:255])
                            JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=safe_je_desc[:255])
                            print(f"   💾 Saved Bank Transaction [Ref: {instance.bank_ref_id}] -> Dr: {dr_acct_id} | Cr: {cr_acct_id}")
            except Exception as e:
                messages.error(request, f"Database transaction failed. Nothing was saved. Error: {str(e)}")
                return render(request, 'bank_review.html', {'formset': formset, 'metadata': metadata})

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

@login_required
def export_bank_transactions(request, client_id):
    """Exports Bank instances to an Excel file using URL parameter for client routing."""
    client = get_object_or_404(Client, id=client_id)
    
    user = request.user
    has_access = user.is_staff or user.is_superuser
    if not has_access:
        try:
            if user.profile.clients.filter(id=client.id).exists():
                has_access = True
        except Profile.DoesNotExist:
            pass
    if not has_access:
        return HttpResponseForbidden("You do not have permission to export this client's data.")

    queryset = Bank.objects.filter(client_id=client.id).order_by('id')

    resource = BankResource(client_id=client.id)
    dataset = resource.export(queryset=queryset)

    today_str = date.today().strftime("%Y%m%d")
    safe_client_name = "".join([c for c in client.name if c.isalpha() or c.isdigit()]).rstrip()
    filename = f"bank_transactions_{safe_client_name}_{today_str}.xlsx"
    
    media_dir = os.path.join(settings.BASE_DIR, 'media')
    os.makedirs(media_dir, exist_ok=True)
    report_path = os.path.join(media_dir, filename)
    
    with open(report_path, 'wb') as f:
        f.write(dataset.xlsx)
        
    request.session['export_bank_report_path'] = report_path
    request.session['export_bank_filename'] = filename
    
    messages.success(request, f"Successfully exported bank transactions for {client.name}!")
    return redirect('cash:bank_export_success')

def bank_export_success_view(request):
    file_path = request.session.get('export_bank_report_path')
    return render(request, 'bank_export_success.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_exported_banks(request):
    file_path = request.session.get('export_bank_report_path')
    filename = request.session.get('export_bank_filename', 'exported_banks.xlsx')
    
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    messages.error(request, "The export file has expired or could not be found.")
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
    user = request.user

    if request.method == 'POST':
        request.session.pop('cash_report_path', None)
        
        form = CashBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=selected_client.id).exists():
                        has_access = True
                except Profile.DoesNotExist:
                    pass
            if not has_access:
                messages.error(request, "You do not have permission to upload data for this client.")
                return redirect('cash:cash_upload')
                
            uploaded_file = form.cleaned_data['cash_file']
            batch_name = form.cleaned_data['batch_name']
            selected_config = form.cleaned_data['processor_config']
            
            print(f"📥 Received Cash Book file: {uploaded_file.name} (Size: {uploaded_file.size} bytes)")

            ProcessorStrategyClass = CASH_PROCESSOR_MAP.get(selected_config)
            
            if not ProcessorStrategyClass:
                messages.error(request, "Invalid processor configuration.")
                return redirect('cash:cash_upload')
            
            _, file_ext = os.path.splitext(uploaded_file.name)
            if file_ext.lower() == '.xls': ext = '.xls'
            elif file_ext.lower() == '.csv': ext = '.csv'
            else: ext = '.xlsx'

            print(f"💾 Saving temporary file with extension: {ext}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                for chunk in uploaded_file.chunks():
                    tmp_file.write(chunk)
                tmp_file_path = tmp_file.name
            print(f"✅ Temporary file saved at: {tmp_file_path}")

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
                    print(f"\n✅ AI returned {len(mappings)} reconciliation mappings. Processing transactions...")
                    mapping_dict = {str(m.transaction_id): m for m in mappings}
                    
                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        print(f"   🔹 Processing Cash Transaction [Sys ID: {sys_id}] | Description: {str(item.get('description', 'N/A'))[:30]} | In: {item.get('debit', 0)} | Out: {item.get('credit', 0)}")
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            print(f"      ✨ AI Reconciled -> Dr: {match.debit_account_id} | Cr: {match.credit_account_id} | Reason: {match.reasoning}")
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            if hasattr(match, 'matched_purchase_ids') and match.matched_purchase_ids:
                                item['matched_purchase_ids'] = ",".join(map(str, match.matched_purchase_ids))
                            else:
                                item['matched_purchase_ids'] = ""
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            print(f"      ⚠️ No exact AI mapping. Applying default accounts.")
                            if item.get('credit', 0) > 0:  # Money Out
                                item['credit_account_id'] = '100000'
                                item['debit_account_id'] = '120000'
                            else:  # Money In
                                item['debit_account_id'] = '100000'
                                item['credit_account_id'] = '400000'
                else:
                    print("⚠️ Skipping reconciliation: No extracted data or no open purchases.")
                    for item in extracted_data:
                        print(f"   🔹 Processing Cash Transaction [Sys ID: {item.get('sys_id')}] | Description: {str(item.get('description', 'N/A'))[:30]} | In: {item.get('debit', 0)} | Out: {item.get('credit', 0)}")
                        print(f"      ⚠️ Applying default accounts.")
                        if item.get('credit', 0) > 0:  # Money Out
                            item['credit_account_id'] = '100000'
                            item['debit_account_id'] = '120000'
                        else:  # Money In
                            item['debit_account_id'] = '100000'
                            item['credit_account_id'] = '400000'
                
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
        
        # Dynamically limit the dropdown to ONLY the clients the user manages
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()
    return render(request, 'cash_upload.html', {'form': form})


@login_required
def cash_review_view(request):
    """Review Cash Extracted Data, Resolve Vendors, Link Purchases, and Post explicitly defined Journal Entries."""
    extracted_data = request.session.get('extracted_cash', [])
    metadata = request.session.get('cash_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('cash:cash_upload')

    client_id = metadata.get('client_id')
    
    user = request.user
    has_access = user.is_staff or user.is_superuser
    if not has_access and client_id:
        try:
            if user.profile.clients.filter(id=client_id).exists():
                has_access = True
        except Profile.DoesNotExist:
            pass
    if not has_access:
        return HttpResponseForbidden("You do not have permission to review this client's data.")

    # Ensure default accounts exist so the frontend dropdown choices won't be blank
    Account.objects.get_or_create(client_id=client_id, account_id='100000', defaults={'name': 'Cash on Hand', 'account_type': 'Asset'})
    Account.objects.get_or_create(client_id=client_id, account_id='120000', defaults={'name': 'Prepayment', 'account_type': 'Asset'})
    Account.objects.get_or_create(client_id=client_id, account_id='400000', defaults={'name': 'Accounts Receivable', 'account_type': 'Asset'})

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
            try:
                with transaction.atomic():
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
                            matched_ids_str = form.cleaned_data.get('matched_purchase_ids')
                            if matched_ids_str:
                                matched_ids = [int(id_str) for id_str in matched_ids_str.split(',') if id_str.isdigit()]

                                if matched_ids:
                                    try:
                                        first_purchase = Purchase.objects.get(id=matched_ids[0], client_id=client_id)
                                        instance.matched_purchase = first_purchase
                                    except Purchase.DoesNotExist:
                                        pass
                                
                                # Mark ALL matched purchases as 'Paid'
                                purchases_to_pay = Purchase.objects.filter(id__in=matched_ids, client_id=client_id)
                                purchases_to_pay.update(payment_status='Paid')
 
                            instance.save()
                            saved_instances.append(instance)

                            # --- 3. BALANCED DOUBLE-ENTRY POSTING ---
                            is_money_out = instance.credit > 0
                            default_dr = '120000' if is_money_out else '100000'
                            default_cr = '100000' if is_money_out else '400000'

                            dr_acct_id = str(instance.debit_account_id or default_dr)
                            cr_acct_id = str(instance.credit_account_id or default_cr)
                            
                            dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Asset'})
                            cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen Acct', 'account_type': 'Liability'})

                            amount = instance.debit if instance.debit > 0 else instance.credit
                            
                            je_desc = f"Cash Transaction: {instance.description or 'Cash Book Entry'}"
                            if instance.instruction:
                                clean_reason = str(instance.instruction).replace('AI Reconciled: ', '').strip()
                                je_desc = f"Reason: {clean_reason}"
                                if matched_ids_str:
                                    je_desc += f", matched with open purchase IDs {matched_ids_str}."

                            # Ensure descriptions safely fit within database column limits
                            safe_je_desc = je_desc[:500] if je_desc else "Cash Transaction"

                            je = JournalEntry.objects.create(
                                client_id=client_id,
                                date=instance.date or date.today(),
                                description=safe_je_desc,
                                reference_number=instance.voucher_no,
                                cash=instance
                            )

                            JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=safe_je_desc[:255])
                            JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=safe_je_desc[:255])
                            print(f"   💾 Saved Cash Transaction [Voucher: {instance.voucher_no}] -> Dr: {dr_acct_id} | Cr: {cr_acct_id}")
            except Exception as e:
                messages.error(request, f"Database transaction failed. Nothing was saved. Error: {str(e)}")
                return render(request, 'cash_review.html', {'formset': formset, 'metadata': metadata, 'page_obj': page_obj})

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

@login_required
def export_cash_transactions(request, client_id):
    """Exports Cash instances to an Excel file using URL parameter for client routing."""
    client = get_object_or_404(Client, id=client_id)
    
    user = request.user
    has_access = user.is_staff or user.is_superuser
    if not has_access:
        try:
            if user.profile.clients.filter(id=client.id).exists():
                has_access = True
        except Profile.DoesNotExist:
            pass
    if not has_access:
        return HttpResponseForbidden("You do not have permission to export this client's data.")

    queryset = Cash.objects.filter(client_id=client.id).order_by('id')

    resource = CashResource(client_id=client.id)
    dataset = resource.export(queryset=queryset)

    today_str = date.today().strftime("%Y%m%d")
    safe_client_name = "".join([c for c in client.name if c.isalpha() or c.isdigit()]).rstrip()
    filename = f"cash_transactions_{safe_client_name}_{today_str}.xlsx"
    
    media_dir = os.path.join(settings.BASE_DIR, 'media')
    os.makedirs(media_dir, exist_ok=True)
    report_path = os.path.join(media_dir, filename)
    
    with open(report_path, 'wb') as f:
        f.write(dataset.xlsx)
        
    request.session['export_cash_report_path'] = report_path
    request.session['export_cash_filename'] = filename
    
    messages.success(request, f"Successfully exported cash transactions for {client.name}!")
    return redirect('cash:cash_export_success')

def cash_export_success_view(request):
    file_path = request.session.get('export_cash_report_path')
    return render(request, 'cash_export_success.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_exported_cash(request):
    file_path = request.session.get('export_cash_report_path')
    filename = request.session.get('export_cash_filename', 'exported_cash.xlsx')
    
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    messages.error(request, "The export file has expired or could not be found.")
    return redirect('cash:cash_upload')


# ====================================================================
# --- BANK CRUD SYSTEM ---
# ====================================================================

@login_required(login_url="register:login")
def BankListView(request):
    user = request.user

    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('cash:bank_list')

    client_id = request.session.get('active_client_id')

    if client_id:
        base_queryset = Bank.objects.filter(client_id=client_id)
        if user.is_staff or user.is_superuser:
            banks = base_queryset
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=client_id).exists():
                    banks = base_queryset
                else:
                    banks = Bank.objects.none()
                    messages.error(request, "You do not have permission to view bank transactions for this client.")
            except Profile.DoesNotExist:
                banks = Bank.objects.none()
        client_form = ClientSelectionForm(initial={'client': client_id})
    else:
        banks = Bank.objects.none()
        client_form = ClientSelectionForm()
        messages.info(request, "Please select a client to view bank transactions.")

    banks = banks.order_by('-date', '-id')
    bank_filter = BankFilter(request.GET, queryset=banks)
    paginator = Paginator(bank_filter.qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'cash/bank_list.html', {
        'filter': bank_filter, 'banks': page_obj, 'page_obj': page_obj, 'client_form': client_form
    })

@login_required(login_url="register:login")
def manual_bank_entry_view(request):
    client_id = request.session.get('active_client_id')
    
    if client_id:
        user = request.user
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to manage this client.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = ManualBankEntryForm(request.POST, account_choices=account_choices)
        if form.is_valid():
            with transaction.atomic():
                bank = form.save(commit=False)
                bank.client_id = client_id
                bank.user = request.user
                bank.batch = "MANUAL_ENTRY"
                bank.save()

                dr_acct_id = str(bank.debit_account_id)
                cr_acct_id = str(bank.credit_account_id)
                dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Asset'})
                cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Liability'})

                amount = bank.debit if bank.debit > 0 else bank.credit
                je_desc = f"Manual Bank Txn: {bank.counterparty or bank.purpose}"[:500]

                je = JournalEntry.objects.create(client_id=client_id, date=bank.date, description=je_desc, reference_number=bank.bank_ref_id, bank=bank)
                JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=je_desc[:255])
                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                
            messages.success(request, f"Manual Bank transaction {bank.bank_ref_id} posted securely!")
            return redirect('cash:bank_list')
    else:
        form = ManualBankEntryForm(account_choices=account_choices)
    return render(request, 'cash/manual_bank_entry.html', {'form': form})

class BankDetailView(LoginRequiredMixin, DetailView):
    model = Bank
    template_name = 'cash/bank_detail.html'
    context_object_name = 'bank'

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        obj = self.get_object()
        is_owner = user.is_staff or user.is_superuser
        try:
            if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                is_owner = True
        except: pass
        context['is_owner'] = is_owner
        return context

class BankUpdateView(LoginRequiredMixin, UpdateView):
    model = Bank
    form_class = ManualBankEntryForm 
    template_name = 'cash/bank_update.html'
    
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.request.session.get('active_client_id')
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            bank = form.save()
            JournalEntry.objects.filter(bank=bank).delete()
            client_id = self.request.session.get('active_client_id')
            
            dr_acct_id = str(bank.debit_account_id)
            cr_acct_id = str(bank.credit_account_id)
            dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Asset'})
            cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Liability'})

            amount = bank.debit if bank.debit > 0 else bank.credit
            je_desc = f"Updated Bank Txn: {bank.counterparty or bank.purpose}"[:500]

            je = JournalEntry.objects.create(client_id=client_id, date=bank.date, description=je_desc, reference_number=bank.bank_ref_id, bank=bank)
            JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=je_desc[:255])
            JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
            
        messages.success(self.request, "Bank transaction updated securely!")
        return HttpResponseRedirect(reverse('cash:bank_detail', kwargs={'pk': self.object.pk}))

class BankDeleteView(LoginRequiredMixin, DeleteView):
    model = Bank
    template_name = 'cash/bank_confirm_delete.html'
    success_url = reverse_lazy('cash:bank_list')

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        JournalEntry.objects.filter(bank=self.object).delete()
        messages.success(self.request, 'Bank transaction deleted.')
        return super().form_valid(form)

@login_required(login_url="register:login")
def export_bank_csv(request):
    client_id = request.session.get('active_client_id')
    if not client_id: return HttpResponse("No active client selected.", status=400)
    
    base_queryset = Bank.objects.filter(client_id=client_id)
    user = request.user
    if not (user.is_staff or user.is_superuser):
        try:
            if not Profile.objects.get(user=user).clients.filter(id=client_id).exists():
                base_queryset = Bank.objects.none()
        except Profile.DoesNotExist:
            base_queryset = Bank.objects.none()

    bank_filter = BankFilter(request.GET, queryset=base_queryset.order_by('-date'))
    resource = BankResource(client_id=client_id)
    dataset = resource.export(queryset=bank_filter.qs)
    
    response = HttpResponse(dataset.csv, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="filtered_bank_transactions.csv"'
    return response


# ====================================================================
# --- CASH CRUD SYSTEM ---
# ====================================================================

@login_required(login_url="register:login")
def CashListView(request):
    user = request.user

    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('cash:cash_list')

    client_id = request.session.get('active_client_id')

    if client_id:
        base_queryset = Cash.objects.filter(client_id=client_id)
        if user.is_staff or user.is_superuser:
            cash_qs = base_queryset
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=client_id).exists():
                    cash_qs = base_queryset
                else:
                    cash_qs = Cash.objects.none()
                    messages.error(request, "You do not have permission to view cash transactions for this client.")
            except Profile.DoesNotExist:
                cash_qs = Cash.objects.none()
        client_form = ClientSelectionForm(initial={'client': client_id})
        vendor_queryset = Vendor.objects.filter(client_id=client_id).order_by('vendor_id')
    else:
        cash_qs = Cash.objects.none()
        client_form = ClientSelectionForm()
        vendor_queryset = Vendor.objects.none()
        messages.info(request, "Please select a client to view cash transactions.")

    cash_qs = cash_qs.order_by('-date', '-id')
    cash_filter = CashFilter(request.GET, queryset=cash_qs)
    cash_filter.form.fields['vendor'].queryset = vendor_queryset
    paginator = Paginator(cash_filter.qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'cash/cash_list.html', {
        'filter': cash_filter, 'cash_objs': page_obj, 'page_obj': page_obj, 'client_form': client_form
    })

@login_required(login_url="register:login")
def manual_cash_entry_view(request):
    client_id = request.session.get('active_client_id')
    
    if client_id:
        user = request.user
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to manage this client.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = ManualCashEntryForm(request.POST, vendor_choices=vendor_choices, account_choices=account_choices)
        if form.is_valid():
            with transaction.atomic():
                cash = form.save(commit=False)
                cash.client_id = client_id
                cash.user = request.user
                cash.batch = "MANUAL_ENTRY"
                vc = form.cleaned_data.get('vendor_choice')
                if vc: cash.vendor_id = int(vc)
                cash.save()

                dr_acct_id = str(cash.debit_account_id)
                cr_acct_id = str(cash.credit_account_id)
                dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Asset'})
                cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Liability'})

                amount = cash.debit if cash.debit > 0 else cash.credit
                je_desc = f"Manual Cash Txn: {cash.description}"[:500]

                je = JournalEntry.objects.create(client_id=client_id, date=cash.date, description=je_desc, reference_number=cash.voucher_no, cash=cash)
                JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=je_desc[:255])
                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                
            messages.success(request, f"Manual Cash transaction posted securely!")
            return redirect('cash:cash_list')
    else:
        form = ManualCashEntryForm(vendor_choices=vendor_choices, account_choices=account_choices)
    return render(request, 'cash/manual_cash_entry.html', {'form': form})

class CashDetailView(LoginRequiredMixin, DetailView):
    model = Cash
    template_name = 'cash/cash_detail.html'
    context_object_name = 'cash'

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        obj = self.get_object()
        is_owner = user.is_staff or user.is_superuser
        try:
            if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                is_owner = True
        except: pass
        context['is_owner'] = is_owner
        return context

class CashUpdateView(LoginRequiredMixin, UpdateView):
    model = Cash
    form_class = ManualCashEntryForm 
    template_name = 'cash/cash_update.html'
    
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.request.session.get('active_client_id')
        db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
        kwargs['vendor_choices'] = [('', '--- Select Existing Vendor ---')] + db_vendors
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        return kwargs
        
    def get_initial(self):
        initial = super().get_initial()
        if self.object.vendor:
            initial['vendor_choice'] = self.object.vendor.id
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            cash = form.save(commit=False)
            vc = form.cleaned_data.get('vendor_choice')
            if vc: cash.vendor_id = int(vc)
            cash.save()
            
            JournalEntry.objects.filter(cash=cash).delete()
            client_id = self.request.session.get('active_client_id')
            
            dr_acct_id = str(cash.debit_account_id)
            cr_acct_id = str(cash.credit_account_id)
            dr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=dr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Asset'})
            cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=cr_acct_id, defaults={'name': 'System Gen', 'account_type': 'Liability'})

            amount = cash.debit if cash.debit > 0 else cash.credit
            je_desc = f"Updated Cash Txn: {cash.description}"[:500]

            je = JournalEntry.objects.create(client_id=client_id, date=cash.date, description=je_desc, reference_number=cash.voucher_no, cash=cash)
            JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=je_desc[:255])
            JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
            
        messages.success(self.request, "Cash transaction updated securely!")
        return HttpResponseRedirect(reverse('cash:cash_detail', kwargs={'pk': self.object.pk}))

class CashDeleteView(LoginRequiredMixin, DeleteView):
    model = Cash
    template_name = 'cash/cash_confirm_delete.html'
    success_url = reverse_lazy('cash:cash_list')

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        obj = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=obj.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized: return HttpResponseForbidden("You do not have permission.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        JournalEntry.objects.filter(cash=self.object).delete()
        messages.success(self.request, 'Cash transaction deleted.')
        return super().form_valid(form)

@login_required(login_url="register:login")
def export_cash_csv(request):
    client_id = request.session.get('active_client_id')
    if not client_id: return HttpResponse("No active client selected.", status=400)
    
    base_queryset = Cash.objects.filter(client_id=client_id)
    user = request.user
    if not (user.is_staff or user.is_superuser):
        try:
            if not Profile.objects.get(user=user).clients.filter(id=client_id).exists():
                base_queryset = Cash.objects.none()
        except Profile.DoesNotExist:
            base_queryset = Cash.objects.none()

    cash_filter = CashFilter(request.GET, queryset=base_queryset.order_by('-date'))
    resource = CashResource(client_id=client_id)
    dataset = resource.export(queryset=cash_filter.qs)
    
    response = HttpResponse(dataset.csv, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="filtered_cash_transactions.csv"'
    return response
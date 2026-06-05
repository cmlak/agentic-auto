import os
import sys
import io
import tempfile
import json
import pandas as pd
import re
import difflib
from datetime import date, datetime
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
from django.db.models import Max

from .forms import BankBatchUploadForm, BankFormSet, CashBatchUploadForm, CashReviewForm, CashFormSet, ManualBankEntryForm, ManualCashEntryForm
from .processors import GeminiABABankProcessor, GeminiCanadiaBankProcessor, ClientBCustomBankProcessor, \
    CashStandardExcelProcessor, GeminiReconciliationEngine
from .models import Bank, Cash
from sale.models import Sale
from .resources import BankResource, CashResource
from .filters import BankFilter, CashFilter
from tools.models import AICostLog, Vendor, Purchase
from account.models import Account, JournalEntry, JournalLine, ClientPromptMemo, AccountMappingRule
from register.models import Profile

BANK_PROCESSOR_MAP = {
    'aba_standard': GeminiABABankProcessor,
    'canadia_standard': GeminiCanadiaBankProcessor,
    'client_b_custom': ClientBCustomBankProcessor,
}

def _distribute_settlement_lines(je, amount, dr_acct, cr_acct, je_desc):
    """Helper to generate standard double-entry JournalLines."""
    if dr_acct:
        JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=amount, description=je_desc[:255])
    if cr_acct:
        JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])

@login_required
def bank_ai_upload_view(request):
    user = request.user

    if request.method == 'POST':
        request.session.pop('bank_report_path', None)
        
        form = BankBatchUploadForm(request.POST, request.FILES)

        if form.is_valid():
                
            uploaded_pdf = form.cleaned_data['bank_pdf']
            batch_name = form.cleaned_data['batch_name']
            selected_config = form.cleaned_data['processor_config']
            
            # --- 1. PARSE BANK PAYMENT EXPLANATION FILE (SUPPLEMENTARY EXCEL/CSV) ---
            supplementary_data_md = ""
            custom_rules_file = form.cleaned_data.get('custom_rules_file')
            if custom_rules_file:
                print(f"📄 Received bank explanation file: {custom_rules_file.name}", flush=True)
                try:
                    if custom_rules_file.name.endswith('.csv'): 
                        df_rules = pd.read_csv(custom_rules_file)
                    else: 
                        df_rules = pd.read_excel(custom_rules_file, engine='openpyxl')
                    
                    supplementary_data_md = df_rules.to_csv(index=False)
                    print("✅ Successfully parsed supplementary rules file.", flush=True)
                except Exception as e:
                    messages.warning(request, f"Warning: Could not parse supplementary file. {str(e)}")

            # --- BUILD TIER 2 PROMPT FOR BANK EXTRACTION ---
            bank_extraction_memo = form.cleaned_data.get('ai_prompt', '')
            if supplementary_data_md:
                bank_extraction_memo += f"\n\n[SUPPLEMENTARY ROUTING DATA]\n{supplementary_data_md}"
            
            bank_memos = ClientPromptMemo.objects.filter(
                category__in=['BANK_EXTRACTION', 'GENERAL']
            )
            if bank_memos.exists():
                bank_extraction_memo += "\n\n--- CLIENT-SPECIFIC DATABASE RULES ---\n"
                for memo in bank_memos:
                    bank_extraction_memo += f"- {memo.memo_text}\n"

            # --- 2. PARSE HISTORICAL GL FILE ---
            historical_gl_file = form.cleaned_data.get('historical_gl_file')
            hist_gl_md = ""
            if historical_gl_file:
                print(f"📚 Received Historical GL file: {historical_gl_file.name}", flush=True)
                try:
                    if historical_gl_file.name.endswith('.csv'): 
                        df_gl = pd.read_csv(historical_gl_file)
                    else: 
                        df_gl = pd.read_excel(historical_gl_file, engine='openpyxl')
                    
                    hist_gl_md = df_gl.to_csv(index=False)
                    print("✅ Successfully parsed Historical General Ledger.", flush=True)
                except Exception as e:
                    messages.warning(request, f"Warning: Could not parse Historical GL. {str(e)}")

            # --- 3. EXTRACT TEXT FROM REMITTANCE SLIPS (FOR SPLITTING HIDDEN FEES) ---
            slips_pdf = form.cleaned_data.get('slips_pdf')
            slips_text = ""
            if slips_pdf:
                print("📄 Extracting text from supplementary bank slips...", flush=True)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_slip:
                    for chunk in slips_pdf.chunks():
                        tmp_slip.write(chunk)
                    tmp_slip_path = tmp_slip.name
                try:
                    slip_reader = PdfReader(tmp_slip_path)
                    for page in slip_reader.pages:
                        slips_text += page.extract_text(extraction_strategy="layout") + "\n"
                    print(f"✅ Extracted text from {len(slip_reader.pages)} slip pages.", flush=True)
                except Exception as e:
                    messages.warning(request, f"Warning: Could not parse slips PDF. {str(e)}")
                finally:
                    if os.path.exists(tmp_slip_path):
                        os.remove(tmp_slip_path)

            ProcessorStrategyClass = BANK_PROCESSOR_MAP.get(selected_config)
            
            if not ProcessorStrategyClass:
                messages.error(request, "Invalid processor configuration.")
                return redirect('cash:bank_upload')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                print("\n" + "="*50, flush=True)
                print(f"🚀 STARTING BANK AI PROCESSING", flush=True)
                print("="*50, flush=True)

                api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2")) 
                processor = ProcessorStrategyClass(api_key=api_key)
                
                # =========================================================
                # PHASE 1: EXTRACT TRANSACTIONS
                # =========================================================
                print("\n[1/4] EXTRACTING TRANSACTIONS AND VENDORS FROM PDF...", flush=True)
                
                # We dynamically check if the processor accepts 'slips_text' to maintain backward compatibility
                # with ABA processors that might not have their signatures updated yet.
                import inspect
                processor_sig = inspect.signature(processor.process)
                
                if 'slips_text' in processor_sig.parameters:
                    extracted_data, total_pages, costs = processor.process(
                        pdf_path=tmp_pdf_path, 
                        batch_name=batch_name,
                        custom_prompt=bank_extraction_memo,
                        slips_text=slips_text
                    )
                else:
                    extracted_data, total_pages, costs = processor.process(
                        pdf_path=tmp_pdf_path, 
                        batch_name=batch_name,
                        custom_prompt=bank_extraction_memo
                    )
                
                print(f"✅ Extracted {len(extracted_data)} transactions across {total_pages} pages.", flush=True)
                
                # =========================================================
                # PHASE 2: FETCH SUBLEDGERS & COA
                # =========================================================
                print("\n[2/4] FETCHING SUBLEDGERS & CHART OF ACCOUNTS...", flush=True)
                
                # 2A. Open Purchases
                open_purchases = list(Purchase.objects.filter(
                    payment_status__in=['Open', 'Prepayment']
                ).values('id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status', 'page'))
                print(f"✅ Found {len(open_purchases)} open purchase invoices.", flush=True)
                
                # 2B. Open Sales
                open_sales = []
                try:
                    from sale.models import Sale
                    open_sales = list(Sale.objects.filter(
                        payment_status__in=['Open', 'Prepayment']
                    ).values('id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status'))
                except ImportError:
                    pass
                print(f"✅ Found {len(open_sales)} open sales invoices.", flush=True)

                # 2C. Chart of Accounts (COA)
                client_accounts = Account.objects.all().values_list('account_id', 'name')
                coa_list_str = "\n".join([f"{acct[0]} - {acct[1]}" for acct in client_accounts])
                print(f"✅ Loaded {len(client_accounts)} GL accounts for AI mapping.", flush=True)
                
                # =========================================================
                # PHASE 3: AI RECONCILIATION
                # =========================================================
                recon_costs = {"flash_cost": 0.0, "pro_cost": 0.0}
                print("\n[3/4] AI RECONCILIATION WITH 3-TIER PROMPT & HISTORICAL DATA...", flush=True)
                
                if extracted_data:
                    reconciler = GeminiReconciliationEngine(api_key=api_key, context_account='100010')
                    
                    pur_data_str = json.dumps(open_purchases, default=str)
                    sal_data_str = json.dumps(open_sales, default=str)
                    print(f"⚖️ Reconciling {len(extracted_data)} bank transactions...", flush=True)
                    
                    # --- BUILD TIER 2 PROMPT FOR RECONCILIATION ENGINE ---
                    tier_2_recon_rules = ""
                    recon_memos = ClientPromptMemo.objects.filter(
                        category__in=['RECONCILIATION', 'GENERAL']
                    )
                    if recon_memos.exists():
                        tier_2_recon_rules += "CLIENT SPECIFIC ACCOUNTING MEMOS:\n"
                        for memo in recon_memos:
                            tier_2_recon_rules += f"- {memo.memo_text}\n"
                        tier_2_recon_rules += "\n"

                    mapping_rules = AccountMappingRule.objects.all().select_related('account')
                    if mapping_rules.exists():
                        tier_2_recon_rules += "MANDATORY KEYWORD MAPPINGS:\n"
                        for rule in mapping_rules:
                            tier_2_recon_rules += f"- If description contains '{rule.trigger_keywords}', you MUST consider Account: {rule.account.account_id}. Reasoning: {rule.ai_guideline}\n"

                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        counterparty = str(item.get('counterparty', 'N/A'))[:30]
                        print(f"   🔹 Processing Bank Transaction [Sys ID: {sys_id}] | Counterparty: {counterparty} | In: {item.get('debit', 0)} | Out: {item.get('credit', 0)}", flush=True)
                        
                        tx_data_str = json.dumps([item], default=str)
                        
                        # INJECT THE COA ALONG WITH SUBLEDGERS AND MEMOS
                        mappings, step_costs = reconciler.reconcile(
                            transactions_data=tx_data_str, 
                            open_purchases_data=pur_data_str,
                            open_sales_data=sal_data_str,
                            historical_gl_data=hist_gl_md,
                            prompt_memo=tier_2_recon_rules,
                            chart_of_accounts_data=coa_list_str
                        )
                        
                        recon_costs['flash_cost'] += step_costs.get('flash_cost', 0)
                        recon_costs['pro_cost'] += step_costs.get('pro_cost', 0)
                        
                        mapping_dict = {str(m.transaction_id): m for m in mappings} if mappings else {}
                        
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            print(f"      ✨ AI Reconciled -> Dr: {match.debit_account_id} | Cr: {match.credit_account_id} | Reason: {match.reasoning}", flush=True)
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            
                            if hasattr(match, 'matched_purchase_ids') and match.matched_purchase_ids:
                                item['matched_purchase_ids'] = ",".join(map(str, match.matched_purchase_ids))
                            else:
                                item['matched_purchase_ids'] = ""
                                
                            if hasattr(match, 'matched_sale_ids') and match.matched_sale_ids:
                                item['matched_sale_ids'] = ",".join(map(str, match.matched_sale_ids))
                            else:
                                item['matched_sale_ids'] = ""
                                
                            if hasattr(match, 'fee_account_id') and match.fee_account_id:
                                item['fee_account_id'] = match.fee_account_id
                            if hasattr(match, 'fee_amount') and match.fee_amount:
                                item['fee_amount'] = match.fee_amount
                                
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            print(f"      ⚠️ No exact AI mapping. Applying default accounts.", flush=True)
                            if item.get('credit', 0) > 0:  
                                item['credit_account_id'] = '100010'
                                item['debit_account_id'] = '120000'
                            else:  
                                item['debit_account_id'] = '100010'
                                item['credit_account_id'] = '400000'
                                
                    print(f"\n✅ Completed AI reconciliation for all {len(extracted_data)} transactions.")

                # =========================================================
                # PHASE 4: LOG COST TO CENTRALIZED TABLE
                # =========================================================
                print("\n[4/4] LOGGING AI COSTS AND FINALIZING...")
                total_flash = costs.get('flash_cost', 0) + recon_costs.get('flash_cost', 0)
                total_pro = costs.get('pro_cost', 0) + recon_costs.get('pro_cost', 0)

                try:
                    AICostLog.objects.create(
                        file_name=uploaded_pdf.name, 
                        total_pages=total_pages, 
                        flash_cost=total_flash, 
                        pro_cost=total_pro, 
                        total_cost=total_flash + total_pro
                    )
                except NameError:
                    pass
                
                request.session['extracted_bank'] = extracted_data
                request.session['bank_metadata'] = {
                    'file_name': uploaded_pdf.name,
                    'batch_name': batch_name, 
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
            print(f"❌ Bank Form Validation Failed: {form.errors}")
            messages.error(request, "Validation failed. Please check the form for errors.")
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

    # Ensure default accounts exist so the frontend dropdown choices won't be blank
    Account.objects.get_or_create(account_id='100010', defaults={'name': 'Cash in Bank', 'account_type': 'Asset'})
    Account.objects.get_or_create(account_id='120000', defaults={'name': 'Prepayment', 'account_type': 'Asset'})
    Account.objects.get_or_create(account_id='400000', defaults={'name': 'Accounts Receivable', 'account_type': 'Asset'})

    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
    
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item.get('company', 'Unknown')} ({item.get('temp_vid', '')})"))
    
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors
    
    try: from sale.models import Customer
    except ImportError: Customer = None

    db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
    temp_customers = []
    for item in extracted_data:
        if item.get('is_new_customer'):
            temp_customers.append((item['customer_temp_id'], f"✨ NEW: {item.get('customer_company', 'Unknown')} ({item.get('customer_temp_cid', '')})"))
    
    temp_customers = list(dict.fromkeys(temp_customers))
    dynamic_customer_choices = [('', '--- Select Customer ---')] + db_customers + temp_customers

    # --- PRE-FILL VENDOR & CUSTOMER FROM AI RECONCILIATION MATCHES ---
    modified_session = False
    for item in extracted_data:
        matched_p_ids = item.get('matched_purchase_ids')
        if matched_p_ids:
            try:
                first_p_id = int(str(matched_p_ids).split(',')[0])
                purchase = Purchase.objects.filter(id=first_p_id).first()
                if purchase and purchase.vendor_id and not item.get('vendor_choice'):
                    item['vendor_choice'] = purchase.vendor_id
                    modified_session = True
            except ValueError: pass
        
        matched_s_ids = item.get('matched_sale_ids')
        if matched_s_ids:
            try:
                first_s_id = int(str(matched_s_ids).split(',')[0])
                sale = Sale.objects.filter(id=first_s_id).first()
                if sale and sale.customer_id and not item.get('customer_choice'):
                    item['customer_choice'] = sale.customer_id
                    modified_session = True
            except (ValueError, ImportError): pass
                
    if modified_session:
        request.session['extracted_bank'] = extracted_data
        request.session.modified = True

    if request.method == 'POST':
        formset = BankFormSet(request.POST, form_kwargs={'account_choices': account_choices, 'dynamic_choices': dynamic_choices, 'dynamic_customer_choices': dynamic_customer_choices}) 
        if formset.is_valid():
            saved_instances = []
            
            try:
                with transaction.atomic():
                    for form in formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            instance = form.save(commit=False)
                            instance.batch = metadata.get('batch_name')
                            instance.user = request.user
                            
                            # 1. Resolve Vendor
                            vc = form.cleaned_data.get('vendor_choice')
                            if str(vc).startswith('TEMP_'):
                                new_vid = vc.replace('TEMP_', '')
                                raw_name = 'Unknown Vendor'
                                for choice_val, choice_label in dynamic_choices:
                                    if choice_val == vc:
                                        raw_name = choice_label.replace('✨ NEW: ', '')
                                        raw_name = re.sub(r'\s*\([^)]+\)$', '', raw_name).strip()
                                        break
                                new_vendor, _ = Vendor.objects.get_or_create(
                                    vendor_id=new_vid, defaults={'name': raw_name.title()}
                                )
                                instance.vendor = new_vendor
                            elif vc:
                                try:
                                    instance.vendor_id = int(vc)
                                except ValueError:
                                    pass
                                    
                            # 1.5 Resolve Customer
                            cc = form.cleaned_data.get('customer_choice')
                            if cc:
                                if str(cc).startswith('TEMP_'):
                                    new_cid = cc.replace('TEMP_', '')
                                    raw_cname = 'Unknown Customer'
                                    for choice_val, choice_label in dynamic_customer_choices:
                                        if choice_val == cc:
                                            raw_cname = choice_label.replace('✨ NEW: ', '')
                                            raw_cname = re.sub(r'\s*\([^)]+\)$', '', raw_cname).strip()
                                            break
                                    if Customer:
                                        new_customer, _ = Customer.objects.get_or_create(customer_id=new_cid, defaults={'name': raw_cname.title()})
                                        instance.customer = new_customer
                                else:
                                    try: instance.customer_id = int(cc)
                                    except ValueError: pass

                            # --- SANITIZE REMARK ---
                            # Enforce maximum character limit to prevent database errors
                            if instance.remark and len(instance.remark) > 250:
                                instance.remark = instance.remark[:247] + '...'
                            
                            # --- STATUS TRIGGER ---
                            matched_ids_str = form.cleaned_data.get('matched_purchase_ids')
                            if matched_ids_str:
                                instance.matched_purchase_ids = matched_ids_str
                                matched_ids = [int(id_str) for id_str in matched_ids_str.split(',') if id_str.isdigit()]
                                
                                if matched_ids:
                                    # Link the first purchase to the bank record for reference
                                    try:
                                        first_purchase = Purchase.objects.get(id=matched_ids[0])
                                        instance.matched_purchase = first_purchase
                                    except Purchase.DoesNotExist:
                                        pass
                                
                                # Mark ALL matched purchases as 'Paid'
                                purchases_to_pay = Purchase.objects.filter(id__in=matched_ids)
                                purchases_to_pay.update(payment_status='Paid')
                                
                            # --- STATUS TRIGGER FOR SALES ---
                            matched_s_ids_str = form.cleaned_data.get('matched_sale_ids')
                            if matched_s_ids_str:
                                instance.matched_sale_ids = matched_s_ids_str
                                matched_s_ids = [int(id_str) for id_str in matched_s_ids_str.split(',') if id_str.isdigit()]
                                if matched_s_ids:
                                    try:
                                        from sale.models import Sale
                                        first_sale = Sale.objects.get(id=matched_s_ids[0])
                                        instance.matched_sale = first_sale
                                        sales_to_pay = Sale.objects.filter(id__in=matched_s_ids)
                                        sales_to_pay.update(payment_status='Paid')
                                    except (ImportError, Exception): pass
 
                            instance.save()
                            saved_instances.append(instance)

                            # --- BALANCED DOUBLE-ENTRY POSTING ---
                            is_money_out = instance.credit > 0
                            default_dr = '120000' if is_money_out else '100010'
                            default_cr = '100010' if is_money_out else '400000'
                            
                            dr_acct_id = str(instance.debit_account_id or default_dr)
                            cr_acct_id = str(instance.credit_account_id or default_cr)
                            
                            dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
                            cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

                            amount = instance.debit if instance.debit > 0 else instance.credit
                            fee_amt = getattr(instance, 'fee_amount', 0.0) or 0.0
                            
                            je_desc = f"Bank Transaction: {instance.counterparty or instance.purpose}"
                            if instance.instruction:
                                clean_reason = str(instance.instruction).replace('AI Reconciled: ', '').strip()
                                je_desc = f"Reason: {clean_reason}"
                                if matched_ids_str:
                                    je_desc += f", matched with open purchase IDs {matched_ids_str}."

                            # Ensure descriptions safely fit within database column limits
                            safe_je_desc = je_desc[:500] if je_desc else "Bank Transaction"

                            je = JournalEntry.objects.create(
                                date=instance.date or date.today(),
                                description=safe_je_desc,
                                reference_number=instance.bank_ref_id,
                                bank=instance
                            )

                            if is_money_out and fee_amt > 0:
                                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=safe_je_desc[:255])
                                
                                principal_debit = amount - fee_amt
                                if principal_debit > 0:
                                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=safe_je_desc[:255])
                                
                                fee_acct_id = str(instance.fee_account_id or '725080')
                                fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                                JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
                                print(f"   💾 Saved Split Bank Transaction [Ref: {instance.bank_ref_id}] -> Dr Principal: {dr_acct_id} | Dr Fee: {fee_acct_id} | Cr: {cr_acct_id}")
                            else:
                                _distribute_settlement_lines(je, amount, dr_acct, cr_acct, safe_je_desc)
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
        formset = BankFormSet(initial=extracted_data, form_kwargs={'account_choices': account_choices, 'dynamic_choices': dynamic_choices, 'dynamic_customer_choices': dynamic_customer_choices})

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
def export_bank_transactions(request):
    """Exports Bank instances to an Excel file."""

    queryset = Bank.objects.all().order_by('id')

    resource = BankResource()
    dataset = resource.export(queryset=queryset)

    today_str = date.today().strftime("%Y%m%d")
    filename = f"bank_transactions_{today_str}.xlsx"
    
    media_dir = os.path.join(settings.BASE_DIR, 'media')
    os.makedirs(media_dir, exist_ok=True)
    report_path = os.path.join(media_dir, filename)
    
    with open(report_path, 'wb') as f:
        f.write(dataset.xlsx)
        
    request.session['export_bank_report_path'] = report_path
    request.session['export_bank_filename'] = filename
    
    messages.success(request, f"Successfully exported bank transactions!")
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
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔥 cash_upload_view triggered | Method: {request.method}", flush=True)
    user = request.user

    if request.method == 'POST':
        print(f"📥 POST keys: {list(request.POST.keys())}", flush=True)
        print(f"📥 FILES keys: {list(request.FILES.keys())}", flush=True)
        
        form = CashBatchUploadForm(request.POST, request.FILES)

        if form.is_valid():
                
            uploaded_file = form.cleaned_data['cash_file']
            batch_name = form.cleaned_data['batch_name']
            selected_config = form.cleaned_data['processor_config']
            
            ProcessorStrategyClass = CASH_PROCESSOR_MAP.get(selected_config)
            
            if not ProcessorStrategyClass:
                print(f"❌ INVALID CONFIG: '{selected_config}' not found in CASH_PROCESSOR_MAP", flush=True)
                messages.error(request, "Invalid processor configuration.")
                return redirect('cash:cash_upload')
            
            tmp_file_path = None

            try:
                print(f"📥 Received Cash Book file: {uploaded_file.name} (Size: {uploaded_file.size} bytes)")
                print(f"📥 Received Cash Book file: {uploaded_file.name} (Size: {uploaded_file.size} bytes)", flush=True)
                
                _, file_ext = os.path.splitext(uploaded_file.name)
                if file_ext.lower() == '.xls': ext = '.xls'
                elif file_ext.lower() == '.csv': ext = '.csv'
                else: ext = '.xlsx'

                print(f"💾 Saving temporary file with extension: {ext}")
                print(f"💾 Saving temporary file with extension: {ext}", flush=True)
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                    for chunk in uploaded_file.chunks():
                        tmp_file.write(chunk)
                    tmp_file_path = tmp_file.name
                print(f"✅ Temporary file saved at: {tmp_file_path}")

                print("\n" + "="*50)
                print(f"🚀 STARTING CASH BOOK PROCESSING")
                print("="*50)
                
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = ProcessorStrategyClass(api_key=api_key)
                
                # 1. EXTRACT TRANSACTIONS
                print("\n[1/4] EXTRACTING CASH TRANSACTIONS FROM EXCEL...")
                extracted_data, total_pages, costs = processor.process(
                    file_path=tmp_file_path, 
                    batch_name=batch_name
                )
                print(f"✅ Extracted {len(extracted_data)} cash transactions.")
                
                # 2. FETCH OPEN PURCHASES (SUBLEDGER)
                print("\n[2/4] FETCHING OPEN PURCHASES (SUBLEDGER)...")
                for i, item in enumerate(extracted_data):
                    if not item.get('sys_id'):
                        item['sys_id'] = f"CASH-{i+1}"
                        
                open_purchases = list(Purchase.objects.filter(
                    payment_status__in=['Open', 'Prepayment']
                ).values(
                    'id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status', 'page'
                ))
                print(f"✅ Found {len(open_purchases)} open purchase invoices.")
                
                open_sales = []
                try:
                    from sale.models import Sale
                    open_sales = list(Sale.objects.filter(
                        payment_status__in=['Open', 'Prepayment']
                    ).values(
                        'id', 'date', 'invoice_no', 'company', 'total_usd', 'payment_status'
                    ))
                except ImportError: pass
                
                # Fetch COA
                client_accounts = Account.objects.all().values_list('account_id', 'name')
                coa_list_str = "\n".join([f"{acct[0]} - {acct[1]}" for acct in client_accounts])
                
                # 3. AI RECONCILIATION WITH 3-TIER PROMPT
                recon_costs = {"flash_cost": 0.0, "pro_cost": 0.0}
                print("\n[3/4] AI RECONCILIATION WITH 3-TIER PROMPT...")
                if extracted_data and open_purchases:
                    print(f"⚖️ Reconciling {len(extracted_data)} cash lines against {len(open_purchases)} Open Invoices...")
                    reconciler = GeminiReconciliationEngine(api_key=api_key, context_account='100000')
                    
                    pur_data_str = json.dumps(open_purchases, default=str)
                    sal_data_str = json.dumps(open_sales, default=str)
                    
                    # --- CONSTRUCT TIER 2 FROM DATABASE MODELS ---
                    tier_2_rules = ""
                    recon_memos = ClientPromptMemo.objects.filter(
                        category__in=['RECONCILIATION', 'GENERAL']
                    )
                    if recon_memos.exists():
                        tier_2_rules += "CLIENT SPECIFIC ACCOUNTING MEMOS:\n"
                        for memo in recon_memos:
                            tier_2_rules += f"- {memo.memo_text}\n"
                        tier_2_rules += "\n"

                    mapping_rules = AccountMappingRule.objects.all().select_related('account')
                    if mapping_rules.exists():
                        tier_2_rules += "MANDATORY KEYWORD MAPPINGS:\n"
                        for rule in mapping_rules:
                            tier_2_rules += f"- If description contains '{rule.trigger_keywords}', you MUST consider Account: {rule.account.account_id}. Reasoning: {rule.ai_guideline}\n"
                    # ----------------------------------------------
                    
                    for item in extracted_data:
                        sys_id = str(item.get('sys_id'))
                        desc = str(item.get('description', 'N/A'))[:30]
                        print(f"   🔹 Processing Cash Transaction [Sys ID: {sys_id}] | Description: {desc} | In: {item.get('debit', 0)} | Out: {item.get('credit', 0)}", flush=True)
                        
                        tx_data_str = json.dumps([item], default=str)
                        mappings, step_costs = reconciler.reconcile(
                            transactions_data=tx_data_str, 
                            open_purchases_data=pur_data_str,
                            open_sales_data=sal_data_str,
                            prompt_memo=tier_2_rules,
                            chart_of_accounts_data=coa_list_str
                        )
                        
                        recon_costs['flash_cost'] += step_costs.get('flash_cost', 0)
                        recon_costs['pro_cost'] += step_costs.get('pro_cost', 0)
                        
                        mapping_dict = {str(m.transaction_id): m for m in mappings} if mappings else {}
                        
                        if sys_id in mapping_dict:
                            match = mapping_dict[sys_id]
                            print(f"      ✨ AI Reconciled -> Dr: {match.debit_account_id} | Cr: {match.credit_account_id} | Reason: {match.reasoning}", flush=True)
                            item['debit_account_id'] = match.debit_account_id
                            item['credit_account_id'] = match.credit_account_id
                            if hasattr(match, 'matched_purchase_ids') and match.matched_purchase_ids:
                                item['matched_purchase_ids'] = ",".join(map(str, match.matched_purchase_ids))
                            else:
                                item['matched_purchase_ids'] = ""
                            if hasattr(match, 'matched_sale_ids') and match.matched_sale_ids:
                                item['matched_sale_ids'] = ",".join(map(str, match.matched_sale_ids))
                            else:
                                item['matched_sale_ids'] = ""
                            if hasattr(match, 'fee_account_id') and match.fee_account_id:
                                item['fee_account_id'] = match.fee_account_id
                            if hasattr(match, 'fee_amount') and match.fee_amount:
                                item['fee_amount'] = match.fee_amount
                            item['instruction'] = f"AI Reconciled: {match.reasoning}"
                        else:
                            print(f"      ⚠️ No exact AI mapping. Applying default accounts.", flush=True)
                            if item.get('credit', 0) > 0:  # Money Out
                                item['credit_account_id'] = '100000'
                                item['debit_account_id'] = '120000'
                            else:  # Money In
                                item['debit_account_id'] = '100000'
                                item['credit_account_id'] = '400000'
                                
                    print(f"\n✅ Completed AI reconciliation for all {len(extracted_data)} transactions.")
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
                    'total_pages': total_pages,
                    'costs': {'flash_cost': total_flash, 'pro_cost': total_pro}
                }
                print("✅ Process complete. Redirecting to review screen.")
                print("="*50 + "\n")
                
                return redirect('cash:cash_review')
                
            except Exception as e:
                messages.error(request, f"Processing Error: {str(e)}")
            finally:
                if tmp_file_path and os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)
        else:
            print(f"❌ Cash Form Validation Failed: {form.errors}")
            print(f"❌ Cash Form Validation Failed: {form.errors}", flush=True)
            messages.error(request, "Validation failed. Please check the form for errors.")
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

    # Ensure default accounts exist so the frontend dropdown choices won't be blank
    Account.objects.get_or_create(account_id='100000', defaults={'name': 'Cash on Hand', 'account_type': 'Asset'})
    Account.objects.get_or_create(account_id='120000', defaults={'name': 'Prepayment', 'account_type': 'Asset'})
    Account.objects.get_or_create(account_id='400000', defaults={'name': 'Accounts Receivable', 'account_type': 'Asset'})

    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
    
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item.get('company', 'Unknown')}"))
    
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors
    
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    # --- PRE-FILL VOUCHER NO & INVOICE NO FOR PREVIEW ---
    modified_session = False
    seq_tracker_preview = {}
    for item in extracted_data:
        # 1. Pre-fill invoice_no and vendor/customer from matched purchase/sale
        matched_p_ids = item.get('matched_purchase_ids')
        if matched_p_ids:
            try:
                first_p_id = int(str(matched_p_ids).split(',')[0])
                purchase = Purchase.objects.filter(id=first_p_id).first()
                if purchase:
                    if not item.get('invoice_no') and purchase.invoice_no:
                        item['invoice_no'] = purchase.invoice_no
                        modified_session = True
                    if not item.get('vendor_choice') and purchase.vendor_id:
                        item['vendor_choice'] = purchase.vendor_id
                        modified_session = True
            except ValueError:
                pass
        else:
            matched_s_ids = item.get('matched_sale_ids')
            if matched_s_ids:
                try:
                    from sale.models import Sale
                    first_s_id = int(str(matched_s_ids).split(',')[0])
                    sale = Sale.objects.filter(id=first_s_id).first()
                    if sale:
                        if not item.get('invoice_no') and sale.invoice_no:
                            item['invoice_no'] = sale.invoice_no
                            modified_session = True
                        if not item.get('customer_choice') and sale.customer_id:
                            item['customer_choice'] = sale.customer_id
                            modified_session = True
                except (ValueError, ImportError):
                    pass

        # 2. Pre-fill voucher_no
        if not item.get('voucher_no') or str(item.get('voucher_no')).strip() == '':
            tx_date_str = item.get('date')
            if tx_date_str:
                if isinstance(tx_date_str, str):
                    try:
                        tx_date = datetime.strptime(tx_date_str[:10], '%Y-%m-%d').date()
                    except ValueError:
                        tx_date = date.today()
                else:
                    tx_date = tx_date_str
            else:
                tx_date = date.today()
                
            ym_prefix = tx_date.strftime("CPV-%Y-%m-")
            if ym_prefix not in seq_tracker_preview:
                existing_vouchers = Cash.objects.filter(
                    voucher_no__startswith=ym_prefix
                ).values_list('voucher_no', flat=True)
                max_v = 0
                for v in existing_vouchers:
                    try:
                        num = int(v.split('-')[-1])
                        if num > max_v: max_v = num
                    except (ValueError, IndexError): pass
                seq_tracker_preview[ym_prefix] = max_v
            
            seq_tracker_preview[ym_prefix] += 1
            item['voucher_no'] = f"{ym_prefix}{seq_tracker_preview[ym_prefix]}"
            modified_session = True

    if modified_session:
        request.session['extracted_cash'] = extracted_data
        request.session.modified = True

    page_number = request.GET.get('page', 1)
    items_per_page = 20
    paginator = Paginator(extracted_data, items_per_page)
    page_obj = paginator.get_page(page_number)
    current_slice = page_obj.object_list
    start_sequence = (page_obj.number - 1) * items_per_page

    try: from sale.models import Customer
    except ImportError: Customer = None

    db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
    temp_customers = []
    for item in extracted_data:
        if item.get('is_new_customer'):
            temp_customers.append((item['customer_temp_id'], f"✨ NEW: {item.get('customer_company', 'Unknown')}"))
    
    temp_customers = list(dict.fromkeys(temp_customers))
    dynamic_customer_choices = [('', '--- Select Customer ---')] + db_customers + temp_customers

    if request.method == 'POST':
        formset = CashFormSet(request.POST, form_kwargs={'dynamic_choices': dynamic_choices, 'dynamic_customer_choices': dynamic_customer_choices, 'account_choices': account_choices, 'start_sequence': start_sequence})
        
        if formset.is_valid():
            saved_instances = []
            seq_tracker = {}
            try:
                with transaction.atomic():
                    for form in formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            instance = form.save(commit=False)
                            instance.user = request.user
                            
                            # --- NEW ANTI-DOUBLE ENTRY CHECK ---
                            if instance.debit_account_id == 'DUPLICATE' or instance.credit_account_id == 'DUPLICATE':
                                print(f"   ⏭️ SKIPPING Transaction {instance.voucher_no}: Flagged as Cash Replenishment duplicate.")
                                continue # Skips creating the Journal Entry and Cash record for this row entirely
                            
                            # --- SEQUENCE GENERATION (CPV-Year-Month-{1}) ---
                            if not instance.voucher_no or str(instance.voucher_no).strip() == '':
                                tx_date = instance.date or date.today()
                                ym_prefix = tx_date.strftime("CPV-%Y-%m-")
                                if ym_prefix not in seq_tracker:
                                    existing_vouchers = Cash.objects.filter(
                                        voucher_no__startswith=ym_prefix
                                    ).values_list('voucher_no', flat=True)
                                    max_v = 0
                                    for v in existing_vouchers:
                                        try:
                                            num = int(v.split('-')[-1])
                                            if num > max_v: max_v = num
                                        except (ValueError, IndexError): pass
                                    seq_tracker[ym_prefix] = max_v
                                seq_tracker[ym_prefix] += 1
                                instance.voucher_no = f"{ym_prefix}{seq_tracker[ym_prefix]}"
                            
                            # 1. Resolve Vendor
                            vc = form.cleaned_data.get('vendor_choice')
                            raw_name = form.cleaned_data.get('company', 'Unknown Vendor')
                            if str(vc).startswith('TEMP_'):
                                new_vid = vc.replace('TEMP_', '')
                                new_vendor, _ = Vendor.objects.get_or_create(vendor_id=new_vid, defaults={'name': raw_name.title()})
                                instance.vendor = new_vendor
                            elif vc:
                                try: instance.vendor = Vendor.objects.get(id=int(vc))
                                except (ValueError, Vendor.DoesNotExist): pass

                            # 1.5 Resolve Customer
                            cc = form.cleaned_data.get('customer_choice')
                            if cc:
                                if str(cc).startswith('TEMP_'):
                                    new_cid = cc.replace('TEMP_', '')
                                    raw_cname = 'Unknown Customer'
                                    for choice_val, choice_label in dynamic_customer_choices:
                                        if choice_val == cc:
                                            raw_cname = choice_label.replace('✨ NEW: ', '')
                                            break
                                    if Customer:
                                        new_customer, _ = Customer.objects.get_or_create(customer_id=new_cid, defaults={'name': raw_cname.title()})
                                        instance.customer = new_customer
                                else:
                                    try: instance.customer_id = int(cc)
                                    except ValueError: pass
                                    
                                    
                            # --- 2. THE TRIGGER: LINK INVOICE & UPDATE STATUS ---
                            matched_ids_str = form.cleaned_data.get('matched_purchase_ids')
                            if matched_ids_str:
                                instance.matched_purchase_ids = matched_ids_str
                                matched_ids = [int(id_str) for id_str in matched_ids_str.split(',') if id_str.isdigit()]

                                if matched_ids:
                                    try:
                                        first_purchase = Purchase.objects.get(id=matched_ids[0])
                                        instance.matched_purchase = first_purchase
                                        if not instance.invoice_no:
                                            instance.invoice_no = first_purchase.invoice_no
                                    except Purchase.DoesNotExist:
                                        pass
                                
                                    # Mark ALL matched purchases as 'Paid'
                                    purchases_to_pay = Purchase.objects.filter(id__in=matched_ids)
                                    purchases_to_pay.update(payment_status='Paid')
                                
                            matched_s_ids_str = form.cleaned_data.get('matched_sale_ids')
                            if matched_s_ids_str:
                                instance.matched_sale_ids = matched_s_ids_str
                                matched_s_ids = [int(id_str) for id_str in matched_s_ids_str.split(',') if id_str.isdigit()]
                                if matched_s_ids:
                                    try:
                                        from sale.models import Sale
                                        first_sale = Sale.objects.get(id=matched_s_ids[0])
                                        instance.matched_sale = first_sale
                                        if not instance.invoice_no:
                                            instance.invoice_no = first_sale.invoice_no
                                        sales_to_pay = Sale.objects.filter(id__in=matched_s_ids)
                                        sales_to_pay.update(payment_status='Paid')
                                    except (ImportError, Exception): pass
 
                            instance.save()
                            saved_instances.append(instance)

                            # --- 3. BALANCED DOUBLE-ENTRY POSTING ---
                            is_money_out = instance.credit > 0
                            default_dr = '120000' if is_money_out else '100000'
                            default_cr = '100000' if is_money_out else '400000'

                            dr_acct_id = str(instance.debit_account_id or default_dr)
                            cr_acct_id = str(instance.credit_account_id or default_cr)
                            
                            dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
                            cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

                            amount = instance.debit if instance.debit > 0 else instance.credit
                            fee_amt = getattr(instance, 'fee_amount', 0.0) or 0.0
                            
                            je_desc = f"Cash Transaction: {instance.description or 'Cash Book Entry'}"
                            if instance.instruction:
                                clean_reason = str(instance.instruction).replace('AI Reconciled: ', '').strip()
                                je_desc = f"Reason: {clean_reason}"
                                if matched_ids_str:
                                    je_desc += f", matched with open purchase IDs {matched_ids_str}."

                            # Ensure descriptions safely fit within database column limits
                            safe_je_desc = je_desc[:500] if je_desc else "Cash Transaction"

                            je = JournalEntry.objects.create(
                                date=instance.date or date.today(),
                                description=safe_je_desc,
                                reference_number=instance.voucher_no,
                                cash=instance
                            )

                            if is_money_out and fee_amt > 0:
                                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=safe_je_desc[:255])
                                
                                principal_debit = amount - fee_amt
                                if principal_debit > 0:
                                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=safe_je_desc[:255])
                                
                                fee_acct_id = str(instance.fee_account_id or '725080')
                                fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                                JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
                                print(f"   💾 Saved Split Cash Transaction [Voucher: {instance.voucher_no}] -> Dr Principal: {dr_acct_id} | Dr Fee: {fee_acct_id} | Cr: {cr_acct_id}")
                            else:
                                _distribute_settlement_lines(
                                    je, amount, dr_acct, cr_acct, safe_je_desc
                                )
                                print(f"   💾 Saved Cash Transaction [Voucher: {instance.voucher_no}] -> Dr: {dr_acct_id} | Cr: {cr_acct_id}")
            except Exception as e:
                messages.error(request, f"Database transaction failed. Nothing was saved. Error: {str(e)}")
                return render(request, 'cash_review.html', {'formset': formset, 'metadata': metadata, 'page_obj': page_obj})

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
        formset = CashFormSet(initial=current_slice, form_kwargs={'dynamic_choices': dynamic_choices, 'dynamic_customer_choices': dynamic_customer_choices, 'account_choices': account_choices, 'start_sequence': start_sequence})

    return render(request, 'cash_review.html', {
        'formset': formset, 
        'metadata': metadata, 
        'page_obj': page_obj,
        'has_preliminary': len(extracted_data) > 0
    })
    
def cash_download_view(request):
    return render(request, 'cash_download.html')

def download_preliminary_cash_report(request):
    """Generates and serves an Excel file containing the preliminary un-saved cash data from the session."""
    extracted_data = request.session.get('extracted_cash', [])
    if extracted_data:
        df = pd.DataFrame(extracted_data)
        
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]) and df[col].dt.tz is not None:
                df[col] = df[col].dt.tz_localize(None)
                
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        output.seek(0)
        response = HttpResponse(
            output.read(), 
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="preliminary_cash_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        return response
        
    messages.error(request, "No preliminary data available to download. Please upload a cash book first.")
    return redirect('cash:cash_upload')

@login_required
def export_cash_transactions(request):
    """Exports Cash instances to an Excel file."""

    queryset = Cash.objects.all().order_by('id')

    resource = CashResource()
    dataset = resource.export(queryset=queryset)

    today_str = date.today().strftime("%Y%m%d")
    filename = f"cash_transactions_{today_str}.xlsx"
    
    media_dir = os.path.join(settings.BASE_DIR, 'media')
    os.makedirs(media_dir, exist_ok=True)
    report_path = os.path.join(media_dir, filename)
    
    with open(report_path, 'wb') as f:
        f.write(dataset.xlsx)
        
    request.session['export_cash_report_path'] = report_path
    request.session['export_cash_filename'] = filename
    
    messages.success(request, f"Successfully exported cash transactions!")
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
    banks = Bank.objects.all().order_by('-id')
    vendor_queryset = Vendor.objects.all().order_by('vendor_id')
    
    bank_filter = BankFilter(request.GET, queryset=banks)
    bank_filter.form.fields['vendor'].queryset = vendor_queryset
    paginator = Paginator(bank_filter.qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'cash/bank_list.html', {
        'filter': bank_filter, 'banks': page_obj, 'page_obj': page_obj
    })

@login_required(login_url="register:login")
def manual_bank_entry_view(request):
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors

    try:
        from sale.models import Customer
    except ImportError:
        Customer = None
    db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
    customer_choices = [('', '--- Select Existing Customer ---')] + db_customers

    if request.method == 'POST':
        form = ManualBankEntryForm(request.POST, account_choices=account_choices, vendor_choices=vendor_choices, customer_choices=customer_choices)
        if form.is_valid():
            with transaction.atomic():
                bank = form.save(commit=False)
                bank.user = request.user
                bank.batch = "MANUAL_ENTRY"
                vc = form.cleaned_data.get('vendor_choice')
                if vc: bank.vendor_id = int(vc)
                
                matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
                if matched_p_ids:
                    bank.matched_purchase_ids = matched_p_ids
                    try: bank.matched_purchase_id = int(str(matched_p_ids).split(',')[0].strip())
                    except ValueError: pass
                        
                matched_s_ids = form.cleaned_data.get('matched_sale_ids')
                if matched_s_ids:
                    bank.matched_sale_ids = matched_s_ids
                    try: bank.matched_sale_id = int(str(matched_s_ids).split(',')[0].strip())
                    except ValueError: pass
                
                matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
                if matched_jv_ids:
                    bank.matched_jv_ids = matched_jv_ids
                    try: bank.matched_jv_id = int(str(matched_jv_ids).split(',')[0].strip())
                    except ValueError: pass

                bank.save()

                dr_acct_id = str(bank.debit_account_id) if bank.debit_account_id else None
                cr_acct_id = str(bank.credit_account_id) if bank.credit_account_id else None
                
                dr_acct = None
                cr_acct = None
                if dr_acct_id and dr_acct_id.lower() != 'none':
                    dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
                if cr_acct_id and cr_acct_id.lower() != 'none':
                    cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

                amount = bank.debit if bank.debit > 0 else bank.credit
                fee_amt = getattr(bank, 'fee_amount', 0.0) or 0.0
                
                je_desc = f"Manual Bank Txn: {bank.counterparty or bank.purpose}"
                if matched_p_ids:
                    je_desc += f", matched with purchase IDs {matched_p_ids}."
                if matched_s_ids:
                    je_desc += f", matched with sale IDs {matched_s_ids}."
                if matched_jv_ids:
                    je_desc += f", matched with JV IDs {matched_jv_ids}."
                je_desc = je_desc[:500]

                je = JournalEntry.objects.create(date=bank.date, description=je_desc, reference_number=bank.bank_ref_id, bank=bank)
                
                is_money_out = bank.credit > 0
                if is_money_out and fee_amt > 0:
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                    
                    principal_debit = amount - fee_amt
                    if principal_debit > 0:
                        JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=je_desc[:255])
                    
                    fee_acct_id = str(bank.fee_account_id or '725080')
                    fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                    JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
                else:
                    _distribute_settlement_lines(
                        je, amount, dr_acct, cr_acct, je_desc
                    )
                
            messages.success(request, f"Manual Bank transaction {bank.bank_ref_id} posted securely!")
            return redirect('cash:bank_list')
        else:
            print(f"❌ Manual Bank Entry Form Validation Failed: {form.errors}")
            messages.error(request, "Validation failed. Please check the form for errors.")
    else:
        form = ManualBankEntryForm(account_choices=account_choices, vendor_choices=vendor_choices, customer_choices=customer_choices)
    return render(request, 'cash/manual_bank_entry.html', {'form': form})

class BankDetailView(LoginRequiredMixin, DetailView):
    model = Bank
    template_name = 'cash/bank_detail.html'
    context_object_name = 'bank'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_owner'] = True
        return context

class BankUpdateView(LoginRequiredMixin, UpdateView):
    model = Bank
    form_class = ManualBankEntryForm 
    template_name = 'cash/bank_update.html'
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
        kwargs['vendor_choices'] = [('', '--- Select Existing Vendor ---')] + db_vendors
        
        try:
            from sale.models import Customer
        except ImportError:
            Customer = None
        db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
        kwargs['customer_choices'] = [('', '--- Select Existing Customer ---')] + db_customers
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.object.vendor:
            initial['vendor_choice'] = self.object.vendor.id
        if self.object.customer:
            initial['customer_choice'] = self.object.customer.id
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            bank = form.save(commit=False)
            vc = form.cleaned_data.get('vendor_choice')
            if vc: bank.vendor_id = int(vc)
            cc = form.cleaned_data.get('customer_choice')
            if cc: bank.customer_id = int(cc)
            
            matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
            if matched_p_ids:
                bank.matched_purchase_ids = matched_p_ids
                try: bank.matched_purchase_id = int(str(matched_p_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                bank.matched_purchase_ids = ""
                bank.matched_purchase = None
                
            matched_s_ids = form.cleaned_data.get('matched_sale_ids')
            if matched_s_ids:
                bank.matched_sale_ids = matched_s_ids
                try: bank.matched_sale_id = int(str(matched_s_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                bank.matched_sale_ids = ""
                bank.matched_sale = None
                
            matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
            if matched_jv_ids:
                bank.matched_jv_ids = matched_jv_ids
                try: bank.matched_jv_id = int(str(matched_jv_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                bank.matched_jv_ids = ""
                bank.matched_jv = None
                
            bank.save()
            
            dr_acct_id = str(bank.debit_account_id) if bank.debit_account_id else None
            cr_acct_id = str(bank.credit_account_id) if bank.credit_account_id else None
            
            dr_acct = None
            cr_acct = None
            if dr_acct_id and dr_acct_id.lower() != 'none':
                dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
            if cr_acct_id and cr_acct_id.lower() != 'none':
                cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

            amount = bank.debit if bank.debit > 0 else bank.credit
            fee_amt = getattr(bank, 'fee_amount', 0.0) or 0.0
            
            je_desc = f"Updated Bank Txn: {bank.counterparty or bank.purpose}"
            if matched_p_ids:
                je_desc += f", matched with purchase IDs {matched_p_ids}."
            if matched_s_ids:
                je_desc += f", matched with sale IDs {matched_s_ids}."
            if matched_jv_ids:
                je_desc += f", matched with JV IDs {matched_jv_ids}."
            je_desc = je_desc[:500]

            je, created = JournalEntry.objects.get_or_create(
                bank=bank,
                defaults={
                    'date': bank.date or date.today(),
                    'description': je_desc,
                    'reference_number': bank.bank_ref_id,
                }
            )
            if not created:
                je.date = bank.date or date.today()
                je.description = je_desc
                je.reference_number = bank.bank_ref_id
                je.save(update_fields=['date', 'description', 'reference_number'])
                je.lines.all().delete()
            
            is_money_out = bank.credit > 0
            if is_money_out and fee_amt > 0:
                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                
                principal_debit = amount - fee_amt
                if principal_debit > 0:
                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=je_desc[:255])
                
                fee_acct_id = str(bank.fee_account_id or '725080')
                fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
            else:
                _distribute_settlement_lines(
                    je, amount, dr_acct, cr_acct, je_desc
                )
            
        messages.success(self.request, "Bank transaction updated securely!")
        return HttpResponseRedirect(reverse('cash:bank_detail', kwargs={'pk': self.object.pk}))

class BankDeleteView(LoginRequiredMixin, DeleteView):
    model = Bank
    template_name = 'cash/bank_confirm_delete.html'
    success_url = reverse_lazy('cash:bank_list')

    def form_valid(self, form):
        JournalEntry.objects.filter(bank=self.object).delete()
        messages.success(self.request, 'Bank transaction deleted.')
        return super().form_valid(form)

@login_required(login_url="register:login")
def export_bank_csv(request):
    bank_filter = BankFilter(request.GET, queryset=Bank.objects.all().order_by('-date'))
    resource = BankResource()
    dataset = resource.export(queryset=bank_filter.qs)
    
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="filtered_bank_transactions.csv"'
    response.write('\ufeff') # Add BOM so Excel reads UTF-8 characters (like Chinese) correctly
    response.write(dataset.csv)
    return response


# ====================================================================
# --- CASH CRUD SYSTEM ---
# ====================================================================

@login_required(login_url="register:login")
def CashListView(request):
    cash_qs = Cash.objects.all().order_by('-id')
    vendor_queryset = Vendor.objects.all().order_by('vendor_id')
    
    cash_filter = CashFilter(request.GET, queryset=cash_qs)
    cash_filter.form.fields['vendor'].queryset = vendor_queryset
    paginator = Paginator(cash_filter.qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'cash/cash_list.html', {
        'filter': cash_filter, 'cash_objs': page_obj, 'page_obj': page_obj
    })

@login_required(login_url="register:login")
def manual_cash_entry_view(request):
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    try:
        from sale.models import Customer
    except ImportError:
        Customer = None
    db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
    customer_choices = [('', '--- Select Existing Customer ---')] + db_customers

    if request.method == 'POST':
        form = ManualCashEntryForm(request.POST, vendor_choices=vendor_choices, customer_choices=customer_choices, account_choices=account_choices)
        if form.is_valid():
            
            matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
            if matched_p_ids:
                p_ids = [int(x.strip()) for x in str(matched_p_ids).split(',') if x.strip().isdigit()]
                if p_ids:
                    from tools.models import Purchase
                    invalid_p = Purchase.objects.filter(id__in=p_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                    if invalid_p.exists():
                        form.add_error('matched_purchase_ids', f"Purchase IDs {', '.join(str(p.id) for p in invalid_p)} are already paid or not Open.")

            matched_s_ids = form.cleaned_data.get('matched_sale_ids')
            if matched_s_ids:
                s_ids = [int(x.strip()) for x in str(matched_s_ids).split(',') if x.strip().isdigit()]
                if s_ids:
                    try:
                        from sale.models import Sale
                        invalid_s = Sale.objects.filter(id__in=s_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                        if invalid_s.exists():
                            form.add_error('matched_sale_ids', f"Sale IDs {', '.join(str(s.id) for s in invalid_s)} are already paid or not Open.")
                    except ImportError:
                        pass
                        
            matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
            if matched_jv_ids:
                jv_ids = [int(x.strip()) for x in str(matched_jv_ids).split(',') if x.strip().isdigit()]
                if jv_ids:
                    from tools.models import JournalVoucher
                    invalid_jv = JournalVoucher.objects.filter(id__in=jv_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                    if invalid_jv.exists():
                        form.add_error('matched_jv_ids', f"JV IDs {', '.join(str(jv.id) for jv in invalid_jv)} are already paid or not Open.")

            if form.errors:
                return render(request, 'cash/manual_cash_entry.html', {'form': form})

            with transaction.atomic():
                cash = form.save(commit=False)
                cash.user = request.user
                cash.batch = "MANUAL_ENTRY"
                vc = form.cleaned_data.get('vendor_choice')
                if vc: cash.vendor_id = int(vc)
                cc = form.cleaned_data.get('customer_choice')
                if cc: cash.customer_id = int(cc)
                
                matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
                if matched_p_ids:
                    cash.matched_purchase_ids = matched_p_ids
                    try: cash.matched_purchase_id = int(str(matched_p_ids).split(',')[0].strip())
                    except ValueError: pass
                        
                matched_s_ids = form.cleaned_data.get('matched_sale_ids')
                if matched_s_ids:
                    cash.matched_sale_ids = matched_s_ids
                    try: cash.matched_sale_id = int(str(matched_s_ids).split(',')[0].strip())
                    except ValueError: pass
                
                matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
                if matched_jv_ids:
                    cash.matched_jv_ids = matched_jv_ids
                    try: cash.matched_jv_id = int(str(matched_jv_ids).split(',')[0].strip())
                    except ValueError: pass
                
                cash.save()

                dr_acct_id = str(cash.debit_account_id) if cash.debit_account_id else None
                cr_acct_id = str(cash.credit_account_id) if cash.credit_account_id else None
                
                dr_acct = None
                cr_acct = None
                if dr_acct_id and dr_acct_id.lower() != 'none':
                    dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
                if cr_acct_id and cr_acct_id.lower() != 'none':
                    cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

                amount = cash.debit if cash.debit > 0 else cash.credit
                fee_amt = getattr(cash, 'fee_amount', 0.0) or 0.0
                
                je_desc = f"Manual Cash Txn: {cash.description}"
                if matched_p_ids:
                    je_desc += f", matched with purchase IDs {matched_p_ids}."
                if matched_s_ids:
                    je_desc += f", matched with sale IDs {matched_s_ids}."
                if matched_jv_ids:
                    je_desc += f", matched with JV IDs {matched_jv_ids}."
                je_desc = je_desc[:500]

                je = JournalEntry.objects.create(date=cash.date, description=je_desc, reference_number=cash.voucher_no, cash=cash)
                
                is_money_out = cash.credit > 0
                if is_money_out and fee_amt > 0:
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                    
                    principal_debit = amount - fee_amt
                    if principal_debit > 0:
                        JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=je_desc[:255])
                    
                    fee_acct_id = str(cash.fee_account_id or '725080')
                    fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                    JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
                else:
                    _distribute_settlement_lines(
                        je, amount, dr_acct, cr_acct, je_desc
                    )
                
            messages.success(request, f"Manual Cash transaction posted securely!")
            return redirect('cash:cash_list')
        else:
            print(f"❌ Manual Cash Entry Form Validation Failed: {form.errors}")
            messages.error(request, "Validation failed. Please check the form for errors.")
    else:
        form = ManualCashEntryForm(vendor_choices=vendor_choices, customer_choices=customer_choices, account_choices=account_choices)
    return render(request, 'cash/manual_cash_entry.html', {'form': form})

class CashDetailView(LoginRequiredMixin, DetailView):
    model = Cash
    template_name = 'cash/cash_detail.html'
    context_object_name = 'cash'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_owner'] = True
        return context

class CashUpdateView(LoginRequiredMixin, UpdateView):
    model = Cash
    form_class = ManualCashEntryForm 
    template_name = 'cash/cash_update.html'
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
        kwargs['vendor_choices'] = [('', '--- Select Existing Vendor ---')] + db_vendors
        
        try:
            from sale.models import Customer
        except ImportError:
            Customer = None
        db_customers = [(c.id, f"{c.customer_id} - {c.name}") for c in Customer.objects.all().order_by('customer_id')] if Customer else []
        kwargs['customer_choices'] = [('', '--- Select Existing Customer ---')] + db_customers
        
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.all().order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        return kwargs
        
    def get_initial(self):
        initial = super().get_initial()
        if self.object.vendor:
            initial['vendor_choice'] = self.object.vendor.id
        if self.object.customer:
            initial['customer_choice'] = self.object.customer.id
        return initial

    def form_valid(self, form):
        old_p_ids = []
        if self.object.pk and self.object.matched_purchase_ids:
            old_p_ids = [int(x.strip()) for x in str(self.object.matched_purchase_ids).split(',') if x.strip().isdigit()]
            
        old_s_ids = []
        if self.object.pk and self.object.matched_sale_ids:
            old_s_ids = [int(x.strip()) for x in str(self.object.matched_sale_ids).split(',') if x.strip().isdigit()]
            
        old_jv_ids = []
        if self.object.pk and self.object.matched_jv_ids:
            old_jv_ids = [int(x.strip()) for x in str(self.object.matched_jv_ids).split(',') if x.strip().isdigit()]

        matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
        if matched_p_ids:
            p_ids = [int(x.strip()) for x in str(matched_p_ids).split(',') if x.strip().isdigit()]
            check_p_ids = [pid for pid in p_ids if pid not in old_p_ids]
            if check_p_ids:
                from tools.models import Purchase
                invalid_p = Purchase.objects.filter(id__in=check_p_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                if invalid_p.exists():
                    form.add_error('matched_purchase_ids', f"Purchase IDs {', '.join(str(p.id) for p in invalid_p)} are already paid or not Open.")

        matched_s_ids = form.cleaned_data.get('matched_sale_ids')
        if matched_s_ids:
            s_ids = [int(x.strip()) for x in str(matched_s_ids).split(',') if x.strip().isdigit()]
            check_s_ids = [sid for sid in s_ids if sid not in old_s_ids]
            if check_s_ids:
                try:
                    from sale.models import Sale
                    invalid_s = Sale.objects.filter(id__in=check_s_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                    if invalid_s.exists():
                        form.add_error('matched_sale_ids', f"Sale IDs {', '.join(str(s.id) for s in invalid_s)} are already paid or not Open.")
                except ImportError:
                    pass

        matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
        if matched_jv_ids:
            jv_ids = [int(x.strip()) for x in str(matched_jv_ids).split(',') if x.strip().isdigit()]
            check_jv_ids = [jvid for jvid in jv_ids if jvid not in old_jv_ids]
            if check_jv_ids:
                from tools.models import JournalVoucher
                invalid_jv = JournalVoucher.objects.filter(id__in=check_jv_ids).exclude(payment_status__in=['Open', 'Prepayment'])
                if invalid_jv.exists():
                    form.add_error('matched_jv_ids', f"JV IDs {', '.join(str(jv.id) for jv in invalid_jv)} are already paid or not Open.")

        if form.errors:
            return self.form_invalid(form)

        with transaction.atomic():
            cash = form.save(commit=False)
            vc = form.cleaned_data.get('vendor_choice')
            if vc: cash.vendor_id = int(vc)
            cc = form.cleaned_data.get('customer_choice')
            if cc: cash.customer_id = int(cc)
            
            matched_p_ids = form.cleaned_data.get('matched_purchase_ids')
            if matched_p_ids:
                cash.matched_purchase_ids = matched_p_ids
                try: cash.matched_purchase_id = int(str(matched_p_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                cash.matched_purchase_ids = ""
                cash.matched_purchase = None
                
            matched_s_ids = form.cleaned_data.get('matched_sale_ids')
            if matched_s_ids:
                cash.matched_sale_ids = matched_s_ids
                try: cash.matched_sale_id = int(str(matched_s_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                cash.matched_sale_ids = ""
                cash.matched_sale = None
                
            matched_jv_ids = form.cleaned_data.get('matched_jv_ids')
            if matched_jv_ids:
                cash.matched_jv_ids = matched_jv_ids
                try: cash.matched_jv_id = int(str(matched_jv_ids).split(',')[0].strip())
                except ValueError: pass
            else:
                cash.matched_jv_ids = ""
                cash.matched_jv = None
                
            cash.save()
            
            dr_acct_id = str(cash.debit_account_id) if cash.debit_account_id else None
            cr_acct_id = str(cash.credit_account_id) if cash.credit_account_id else None
            
            dr_acct = None
            cr_acct = None
            if dr_acct_id and dr_acct_id.lower() != 'none':
                dr_acct, _ = Account.objects.get_or_create(account_id=dr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Asset'})
            if cr_acct_id and cr_acct_id.lower() != 'none':
                cr_acct, _ = Account.objects.get_or_create(account_id=cr_acct_id, defaults={'name': 'Uncategorized Account', 'account_type': 'Liability'})

            amount = cash.debit if cash.debit > 0 else cash.credit
            fee_amt = getattr(cash, 'fee_amount', 0.0) or 0.0
            
            je_desc = f"Updated Cash Txn: {cash.description}"
            if matched_p_ids:
                je_desc += f", matched with purchase IDs {matched_p_ids}."
            if matched_s_ids:
                je_desc += f", matched with sale IDs {matched_s_ids}."
            if matched_jv_ids:
                je_desc += f", matched with JV IDs {matched_jv_ids}."
            je_desc = je_desc[:500]

            je, created = JournalEntry.objects.get_or_create(
                cash=cash,
                defaults={
                    'date': cash.date or date.today(),
                    'description': je_desc,
                    'reference_number': cash.voucher_no,
                }
            )
            if not created:
                je.date = cash.date or date.today()
                je.description = je_desc
                je.reference_number = cash.voucher_no
                je.save(update_fields=['date', 'description', 'reference_number'])
                je.lines.all().delete()
            
            is_money_out = cash.credit > 0
            if is_money_out and fee_amt > 0:
                JournalLine.objects.create(journal_entry=je, account=cr_acct, credit=amount, description=je_desc[:255])
                
                principal_debit = amount - fee_amt
                if principal_debit > 0:
                    JournalLine.objects.create(journal_entry=je, account=dr_acct, debit=principal_debit, description=je_desc[:255])
                
                fee_acct_id = str(cash.fee_account_id or '725080')
                fee_acct, _ = Account.objects.get_or_create(account_id=fee_acct_id, defaults={'name': 'Bank Fees', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=fee_acct, debit=fee_amt, description="Bank Charges")
            else:
                _distribute_settlement_lines(
                    je, amount, dr_acct, cr_acct, je_desc
                )
            
        messages.success(self.request, "Cash transaction updated securely!")
        return HttpResponseRedirect(reverse('cash:cash_detail', kwargs={'pk': self.object.pk}))

class CashDeleteView(LoginRequiredMixin, DeleteView):
    model = Cash
    template_name = 'cash/cash_confirm_delete.html'
    success_url = reverse_lazy('cash:cash_list')

    def form_valid(self, form):
        JournalEntry.objects.filter(cash=self.object).delete()
        messages.success(self.request, 'Cash transaction deleted.')
        return super().form_valid(form)

@login_required(login_url="register:login")
def export_cash_csv(request):
    cash_filter = CashFilter(request.GET, queryset=Cash.objects.all().order_by('date'))
    resource = CashResource()
    dataset = resource.export(queryset=cash_filter.qs)
    
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="filtered_cash_transactions.csv"'
    response.write('\ufeff') # Add BOM so Excel reads UTF-8 characters (like Chinese) correctly
    response.write(dataset.csv)
    return response
import os
import tempfile
import pandas as pd
from datetime import date
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum

# Import your forms, processors, and local models
from .forms import BatchUploadForm, PurchaseFormSet, ManualPurchaseEntryForm
from .processors import GeminiInvoiceProcessor
from .models import Purchase, AICostLog, Vendor, Client
from account.models import AccountMappingRule, ClientPromptMemo

# Import the new accounting models
from account.models import Account, JournalEntry, JournalLine


# ====================================================================
# --- 1. AI INVOICE UPLOAD & PROCESSING ---
# ====================================================================

def invoice_ai_upload_view(request):
    """Step 1: Select Client, Upload PDF, Inject Dynamic Rules, Process via AI, and Store."""
    if request.method == 'POST':
        # Clear previous session data safely to prevent data bleed
        request.session.pop('invoice_report_path', None)
        
        form = BatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            uploaded_pdf = form.cleaned_data['invoice_pdf']
            batch_name = form.cleaned_data['batch_name']
            custom_prompt = form.cleaned_data.get('ai_prompt', '')
            
            # ==========================================================
            # --- DYNAMIC MULTI-TENANT RULE INJECTION ---
            # ==========================================================
            rules_context = ""
            memo_context = ""

            # 1. Fetch the Anti-Pattern Memo for this specific client
            client_memo = ClientPromptMemo.objects.filter(client=selected_client).first()
            if client_memo:
                memo_context = client_memo.memo_text

            # 2. Fetch the Mapping Rules and compile them into a CSV format in memory
            rules = AccountMappingRule.objects.filter(client=selected_client).select_related('account')
            if rules.exists():
                rules_data = []
                for rule in rules:
                    rules_data.append({
                        'Account ID': rule.account.account_id,
                        'Account Name': rule.account.name,
                        'Description / Trigger Keywords': rule.trigger_keywords,
                        'Reasoning / AI Guidelines': rule.ai_guideline
                    })
                # Convert the database query directly into a CSV string for the AI prompt
                df_rules = pd.DataFrame(rules_data)
                rules_context = df_rules.to_csv(index=False)
            else:
                print(f"Warning: No Account Mapping Rules found in the database for {selected_client.name}.")

            # ==========================================================
            # --- HANDLE FILE UPLOAD ---
            # ==========================================================
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                # Initialize Processor
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = GeminiInvoiceProcessor(api_key=api_key)
                
                # Execute One-Pass AI Strategy (Extraction + GL Assignment)
                extracted_data, total_pages, costs = processor.process(
                    pdf_path=tmp_pdf_path, 
                    client_id=selected_client.id,
                    custom_prompt=custom_prompt, 
                    batch_name=batch_name,
                    rules_context=rules_context,  # Passing the dynamic DB rules!
                    memo_context=memo_context     # Passing the dynamic DB memo!
                )
                
                # --- LOG COST IMMEDIATELY ---
                # Save the cost now, so it is recorded even if the user abandons the review step.
                AICostLog.objects.create(
                    file_name=uploaded_pdf.name, 
                    total_pages=total_pages, 
                    flash_cost=costs.get('flash_cost', 0), 
                    pro_cost=costs.get('pro_cost', 0), 
                    total_cost=costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                )
                
                # Save results and context to the session for the review screen
                request.session['extracted_invoices'] = extracted_data
                request.session['ai_metadata'] = {
                    'file_name': uploaded_pdf.name,
                    'batch_name': batch_name, 
                    'client_id': selected_client.id,
                    'client_name': selected_client.name,
                    'total_pages': total_pages,
                    'costs': costs
                }
                
                return redirect('tools:review_invoices')
                
            except ValueError as ve:
                messages.error(request, str(ve))
            except Exception as e:
                messages.error(request, f"AI Processing Error: {str(e)}")
            finally:
                # Always clean up the temporary PDF file to prevent storage leaks
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BatchUploadForm()

    return render(request, 'invoice_upload.html', {'form': form})


# ====================================================================
# --- 2. HITL REVIEW & AUTOMATIC GL POSTING ---
# ====================================================================

def review_invoices(request):
    """Step 2: Review AI data, Update Vendors, Save Source Doc, and Post Journal Entry."""
    extracted_data = request.session.get('extracted_invoices', [])
    metadata = request.session.get('ai_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('tools:invoice_upload')
        
    client_id = metadata.get('client_id')
    
    # --- VENDOR CHOICES ---
    # Isolate vendors exclusively to this client
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item.get('company', 'Unknown')} ({item.get('temp_vid', '')})"))
    
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors

    # --- ACCOUNT CHOICES ---
    # Fetch accounts formatted nicely: "100000 - Cash on Hand"
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        # Pass BOTH dynamic vendors and dynamic accounts to the formset
        formset = PurchaseFormSet(
            request.POST, 
            form_kwargs={'dynamic_choices': dynamic_choices, 'account_choices': account_choices}
        )
        
        if formset.is_valid():
            saved_instances = []
            
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    purchase_instance = form.save(commit=False) 
                    purchase_instance.client_id = client_id # Map to client
                    
                    # --- DATA CLEANING ---
                    # Prevent garbage values like "1", "null", or "Unknown" from becoming ="1" in Excel
                    garbage_values = ['null', 'none', 'unknown', 'n/a', '1', 'nan']
                    if str(purchase_instance.invoice_no).lower().strip() in garbage_values:
                        purchase_instance.invoice_no = None
                    if str(purchase_instance.vattin).lower().strip() in garbage_values:
                        purchase_instance.vattin = None
                    
                    # --- FIX: Convert empty strings to None for IntegerFields ---
                    for field in ['account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id']:
                        val = getattr(purchase_instance, field)
                        if val == '' or val == "":
                            setattr(purchase_instance, field, None)

                    # --- VENDOR RESOLUTION ---
                    vc = form.cleaned_data.get('vendor_choice')
                    raw_name = form.cleaned_data.get('company', 'Unknown Vendor')
                    
                    if str(vc).startswith('TEMP_'):
                        new_vid = vc.replace('TEMP_', '')
                        new_vendor, _ = Vendor.objects.get_or_create(
                            client_id=client_id, vendor_id=new_vid, defaults={'name': raw_name}
                        )
                        purchase_instance.vendor = new_vendor
                    elif vc:
                        try:
                            purchase_instance.vendor = Vendor.objects.get(id=int(vc), client_id=client_id)
                        except (ValueError, Vendor.DoesNotExist):
                            pass
                            
                    # 1. Save the Source Document (Purchase Invoice)
                    purchase_instance.save()
                    saved_instances.append(purchase_instance)
                    
                    # ==========================================================
                    # --- 2. AUTOMATIC DOUBLE-ENTRY JOURNAL CREATION ---
                    # ==========================================================
                    
                    # Create Journal Entry Header (Explicit FK back to 'purchase')
                    je = JournalEntry.objects.create(
                        client_id=client_id,
                        date=purchase_instance.date or date.today(),
                        description=f"Purchase from {raw_name}",
                        reference_number=purchase_instance.invoice_no,
                        purchase=purchase_instance
                    )

                    # Financial Calculations
                    total_amount = float(purchase_instance.total_usd or 0.0)
                    vat_amount = float(purchase_instance.vat_usd or 0.0)
                    net_amount = round(total_amount - vat_amount, 2)

                    # --- USER EDITED ACCOUNTS ---
                    # Grab the actual IDs the user settled on in the review screen
                    form_debit_acct = form.cleaned_data.get('account_id')
                    form_credit_acct = form.cleaned_data.get('credit_account_id')

                    # CREDIT: Trade Payable (Total Liability)
                    if total_amount > 0:
                        cr_account_id = str(form_credit_acct) if form_credit_acct else '200000'
                        ap_account, _ = Account.objects.get_or_create(
                            client_id=client_id, account_id=cr_account_id, 
                            defaults={'name': 'Trade Payable - USD', 'account_type': 'Liability'}
                        )
                        JournalLine.objects.create(
                            journal_entry=je, account=ap_account, 
                            description=f"Payable - {raw_name}", credit=total_amount
                        )

                    # DEBIT: VAT Input (Recoverable Tax Asset)
                    if vat_amount > 0:
                        vat_account, _ = Account.objects.get_or_create(
                            client_id=client_id, account_id='115010', 
                            defaults={'name': 'VAT input 进项增值税', 'account_type': 'Asset'}
                        )
                        JournalLine.objects.create(
                            journal_entry=je, account=vat_account, 
                            description="Input VAT", debit=vat_amount
                        )

                    # DEBIT: Expense Account (Net Amount)
                    if net_amount > 0:
                        ai_account_id = str(form_debit_acct) if form_debit_acct else '725080'
                        exp_account, _ = Account.objects.get_or_create(
                            client_id=client_id, account_id=ai_account_id, 
                            defaults={'name': 'Operating Expense', 'account_type': 'Expense'}
                        )
                        JournalLine.objects.create(
                            journal_entry=je, account=exp_account, 
                            description=purchase_instance.description_en or purchase_instance.description or "Expense", 
                            debit=net_amount
                        )
            
            # --- COST LOGGING ---
            costs = metadata.get('costs', {})
            AICostLog.objects.create(
                file_name=metadata.get('file_name', 'Unknown'), 
                total_pages=metadata.get('total_pages', 0), 
                flash_cost=costs.get('flash_cost', 0), 
                pro_cost=costs.get('pro_cost', 0), 
                total_cost=costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
            )

            # --- EXCEL REPORT GENERATION ---
            if saved_instances:
                report_data = list(Purchase.objects.filter(id__in=[p.id for p in saved_instances]).values())
                df_report = pd.DataFrame(report_data)

                # Remove timezone info from datetime columns for Excel compatibility
                for col in df_report.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_report[col]) and df_report[col].dt.tz is not None:
                        df_report[col] = df_report[col].dt.tz_localize(None)

                media_dir = os.path.join(settings.BASE_DIR, 'media')
                os.makedirs(media_dir, exist_ok=True)
                report_path = os.path.join(media_dir, 'invoice_process_report.xlsx')
                df_report.to_excel(report_path, index=False, engine='openpyxl')
                request.session['invoice_report_path'] = report_path
            
            # Clean Session & Redirect
            request.session.pop('extracted_invoices', None)
            request.session.pop('ai_metadata', None)
            
            messages.success(request, f"Successfully saved {len(saved_instances)} invoices and posted Journal Entries for {metadata.get('client_name')}!")
            return redirect('tools:invoice_download') 
        else:
            print("❌ FORMSET VALIDATION FAILED:")
            for i, form in enumerate(formset):
                if form.errors:
                    print(f"Row {i+1} Errors: {form.errors}")
            messages.error(request, "Validation failed. Please check the form for errors.")
            
    else:
        formset = PurchaseFormSet(
            initial=extracted_data, 
            form_kwargs={'dynamic_choices': dynamic_choices, 'account_choices': account_choices}
        )
        
    return render(request, 'invoice_review.html', {'formset': formset, 'metadata': metadata})

# ====================================================================
# --- 3. DOWNLOAD & DASHBOARD VIEWS ---
# ====================================================================

def invoice_download_view(request):
    """Renders the success page with the download link."""
    file_path = request.session.get('invoice_report_path')
    return render(request, 'invoice_download.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_invoice_report(request):
    """Serves the generated Excel invoice report to the user."""
    file_path = request.session.get('invoice_report_path')
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = 'attachment; filename="ai_process_report.xlsx"'
            return response
    
    messages.error(request, "The report file has expired or could not be found.")
    return redirect('tools:invoice_upload')

@staff_member_required
def ai_cost_dashboard(request):
    """Dashboard to review AI processing costs."""
    cost_logs = AICostLog.objects.all().order_by('-date')
    totals = AICostLog.objects.aggregate(
        total_flash=Sum('flash_cost'), 
        total_pro=Sum('pro_cost'), 
        grand_total=Sum('total_cost'), 
        total_pages=Sum('total_pages')
    )
    return render(request, 'cost_dashboard.html', {'cost_logs': cost_logs, 'totals': totals})

def manual_invoice_entry_view(request):
    """View to manually enter a single invoice and post it to the GL."""
    # Ensure a client is active in the session
    client_id = request.session.get('active_client_id')

    # 1. Prefer the client from POST if submitted (allows switching client in form)
    if request.method == 'POST' and request.POST.get('client'):
        client_id = request.POST.get('client')

    if not client_id:
        # Fallback: Default to the first client if session variable is missing
        first_client = Client.objects.first()
        if first_client:
            client_id = first_client.id
        else:
            messages.error(request, "Please create a client first.")
            return redirect('tools:invoice_upload')

    # Fetch dynamic choices
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors

    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = ManualPurchaseEntryForm(request.POST, vendor_choices=vendor_choices, account_choices=account_choices)
        
        if form.is_valid():
            purchase = form.save(commit=False)
            # purchase.client is now set automatically by the form
            purchase.batch = "MANUAL_ENTRY"
            
            # --- FIX: Convert empty strings to None for IntegerFields ---
            for field in ['account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id']:
                val = getattr(purchase, field)
                if val == '' or val == "":
                    setattr(purchase, field, None)
            
            # Resolve Vendor
            vc = form.cleaned_data.get('vendor_choice')
            if vc:
                purchase.vendor_id = int(vc)
            
            purchase.save()

            # ==========================================================
            # --- POST TO GENERAL LEDGER ---
            # ==========================================================
            je = JournalEntry.objects.create(
                client=purchase.client,
                date=purchase.date or date.today(),
                description=f"Manual Purchase: {purchase.company}",
                reference_number=purchase.invoice_no,
                purchase=purchase
            )

            # Get amounts safely
            total_amount = float(purchase.total_usd or 0.0)
            vat_amount = float(purchase.vat_usd or 0.0)
            unreg_amount = float(purchase.unreg_usd or 0.0)
            
            # Calculate WHT mathematically if WHT accounts are selected
            wht_amount = 0.0
            if purchase.wht_account_id and unreg_amount > 0:
                # Assuming 10% or 15% was manually calculated and baked into the total by the user,
                # Or you can calculate it dynamically here based on your rules.
                wht_amount = round(total_amount - unreg_amount, 2) # simplified logic, adjust if needed

            # 1. Main Debit (Expense/Asset)
            if purchase.account_id:
                acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.account_id), defaults={'name': 'Operating Expense', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=acct, description=purchase.description_en or "Expense", debit=(total_amount - vat_amount - wht_amount))

            # 2. VAT Debit
            if vat_amount > 0 and purchase.vat_account_id:
                vat_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.vat_account_id), defaults={'name': 'VAT input', 'account_type': 'Asset'})
                JournalLine.objects.create(journal_entry=je, account=vat_acct, description="Input VAT", debit=vat_amount)

            # 3. WHT Expense Debit (Gross-up scenario)
            if wht_amount > 0 and purchase.wht_debit_account_id:
                wht_exp_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.wht_debit_account_id), defaults={'name': 'WHT Expense', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=wht_exp_acct, description="WHT Expense Absorbed", debit=wht_amount)

            # 4. Main Credit (Payable)
            if total_amount > 0 and purchase.credit_account_id:
                cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.credit_account_id), defaults={'name': 'Trade Payable', 'account_type': 'Liability'})
                JournalLine.objects.create(journal_entry=je, account=cr_acct, description=f"Payable - {purchase.company}", credit=total_amount)

            # 5. WHT Payable Credit
            if wht_amount > 0 and purchase.wht_account_id:
                wht_pay_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.wht_account_id), defaults={'name': 'WHT Payable', 'account_type': 'Liability'})
                JournalLine.objects.create(journal_entry=je, account=wht_pay_acct, description="WHT Payable to GDT", credit=wht_amount)

            messages.success(request, f"Successfully created manual invoice and posted Journal Entry for {purchase.company}.")
            return redirect('tools:manual_invoice_entry') # Reload blank form

    else:
        form = ManualPurchaseEntryForm(initial={'client': client_id}, vendor_choices=vendor_choices, account_choices=account_choices)

    return render(request, 'manual_invoice_entry.html', {'form': form})
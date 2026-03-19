import os
import tempfile
import pandas as pd
from datetime import date
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, UpdateView, DeleteView
from django.db.models import Sum
from django.db import transaction
import datetime
from django.core.paginator import Paginator

# Import your forms, processors, and local models
from .forms import GLMigrationUploadForm
from .forms import BatchUploadForm, PurchaseFormSet, ManualPurchaseEntryForm, GLMigrationUploadForm, GLPurchaseFormSet, GLBankFormSet, GLCashFormSet, ClientSelectionForm
from .processors import GeminiInvoiceProcessor, GLMigrationProcessor
from .models import Purchase, AICostLog, Vendor, Client
from cash.models import Bank, Cash
from account.models import Account, JournalEntry, JournalLine, AccountMappingRule, ClientPromptMemo
from register.models import Profile
from .filters import PurchaseFilter
from .resources import PurchaseResource

# ====================================================================
# --- 1. AI INVOICE UPLOAD & PROCESSING ---
# ====================================================================

@login_required(login_url="register:login")
def invoice_ai_upload_view(request):
    """Step 1: Select Client, Upload PDF, Inject Dynamic Rules, Process via AI, and Store."""
    user = request.user

    if request.method == 'POST':
        # Clear previous session data safely to prevent data bleed
        request.session.pop('invoice_report_path', None)
        
        form = BatchUploadForm(request.POST, request.FILES)
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
                return redirect('tools:invoice_upload')

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
        
        # Dynamically limit the dropdown to ONLY the clients the user manages
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()

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
            
            try:
                with transaction.atomic():
                    for form in formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            purchase_instance = form.save(commit=False) 
                            purchase_instance.client_id = client_id # Map to client
                            purchase_instance.batch = metadata.get('batch_name')
                            
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
            except Exception as e:
                messages.error(request, f"Database transaction failed. Nothing was saved. Error: {str(e)}")
                return render(request, 'invoice_review.html', {'formset': formset, 'metadata': metadata})
            
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

@login_required(login_url="register:login")
def manual_invoice_entry_view(request):
    """View to manually enter a single invoice and post it to the GL."""
    if request.method == 'POST' and 'client' in request.POST and 'vendor_choice' not in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:manual_invoice_entry')

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

    # Fetch dynamic choices
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors

    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = ManualPurchaseEntryForm(request.POST, vendor_choices=vendor_choices, account_choices=account_choices)
        
        if form.is_valid():
            
            # Wrap the entire creation process in an atomic transaction
            with transaction.atomic():
                purchase = form.save(commit=False)
                
                # CRITICAL: Assign the user so Profile permissions work in List/Detail views
                purchase.user = request.user 
                purchase.batch = "MANUAL_ENTRY"
                
                # Convert empty strings to None for IntegerFields
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
                
                # Calculate WHT
                wht_amount = 0.0
                if purchase.wht_account_id and unreg_amount > 0:
                    wht_amount = round(total_amount - unreg_amount, 2) 

                # 1. Main Debit (Expense/Asset)
                if purchase.account_id:
                    acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.account_id), defaults={'name': 'Operating Expense', 'account_type': 'Expense'})
                    JournalLine.objects.create(journal_entry=je, account=acct, description=purchase.description_en or "Expense", debit=(total_amount - vat_amount - wht_amount))

                # 2. VAT Debit
                if vat_amount > 0 and purchase.vat_account_id:
                    vat_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.vat_account_id), defaults={'name': 'VAT input', 'account_type': 'Asset'})
                    JournalLine.objects.create(journal_entry=je, account=vat_acct, description="Input VAT", debit=vat_amount)

                # 3. WHT Expense Debit
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

            # --- End of Atomic Block ---
            
            messages.success(request, f"Successfully created manual invoice and posted Journal Entry for {purchase.company}.")
            return redirect('tools:manual_invoice_entry') 

    else:
        form = ManualPurchaseEntryForm(initial={'client': client_id}, vendor_choices=vendor_choices, account_choices=account_choices)

    return render(request, 'manual_invoice_entry.html', {'form': form})

@login_required
def export_purchase_invoices(request, client_id):
    """Exports Purchase instances to an Excel file using URL parameter for client routing."""
    
    # Optional but recommended: Verify the client exists and user has access
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

    # Base Queryset: Ensure sequential order based on entry processing instead of randomly descending
    queryset = Purchase.objects.filter(client_id=client.id).order_by('id')

    # Pass the client_id directly into the Resource
    resource = PurchaseResource(client_id=client.id)
    dataset = resource.export(queryset=queryset)

    today_str = datetime.date.today().strftime("%Y%m%d")
    # Clean the client name for the filename (removes spaces/special chars)
    safe_client_name = "".join([c for c in client.name if c.isalpha() or c.isdigit()]).rstrip()
    
    filename = f"purchase_invoices_{safe_client_name}_{today_str}.xlsx"
    
    media_dir = os.path.join(settings.BASE_DIR, 'media')
    os.makedirs(media_dir, exist_ok=True)
    report_path = os.path.join(media_dir, filename)
    
    with open(report_path, 'wb') as f:
        f.write(dataset.xlsx)
        
    request.session['export_report_path'] = report_path
    request.session['export_filename'] = filename
    
    messages.success(request, f"Successfully exported purchase invoices for {client.name}!")
    return redirect('tools:purchase_export_success')

def purchase_export_success_view(request):
    """Renders the success page after an export completes."""
    file_path = request.session.get('export_report_path')
    return render(request, 'purchase_export_success.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_exported_purchases(request):
    """Serves the exported Excel file to the user."""
    file_path = request.session.get('export_report_path')
    filename = request.session.get('export_filename', 'exported_purchases.xlsx')
    
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    messages.error(request, "The export file has expired or could not be found.")
    return redirect('tools:invoice_upload')

@login_required
def gl_migration_upload_view(request):
    """Uploads GL data, parses via DB-backed AI, and stores in session queue."""
    if request.method == 'POST':
        request.session.pop('gl_report_path', None)
        request.session.pop('gl_migration_log', None)
        
        form = GLMigrationUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            
            user = request.user
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=selected_client.id).exists():
                        has_access = True
                except Profile.DoesNotExist:
                    pass
            if not has_access:
                messages.error(request, "You do not have permission to migrate data for this client.")
                return redirect('tools:gl_migration_upload')
                
            uploaded_file = form.cleaned_data['gl_file']
            batch_name = form.cleaned_data['batch_name']
            
            _, file_ext = os.path.splitext(uploaded_file.name)
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
                for chunk in uploaded_file.chunks():
                    tmp_file.write(chunk)
                tmp_file_path = tmp_file.name

            try:
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = GLMigrationProcessor(api_key=api_key, client_id=selected_client.id)
                
                print(f"🚀 Parsing Historical Data for: {selected_client.name}...")
                parsed_data, costs = processor.process_migration_file(tmp_file_path)
                
                # Log the AI Cost Immediately
                total_items = len(parsed_data.get('purchases', [])) + len(parsed_data.get('bank_txns', [])) + len(parsed_data.get('cash_txns', []))
                AICostLog.objects.create(
                    file_name=uploaded_file.name, 
                    total_pages=total_items, # Treat the number of grouped clusters as 'pages'
                    flash_cost=costs.get('flash_cost', 0), 
                    pro_cost=costs.get('pro_cost', 0), 
                    total_cost=costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                )
                
                # Save the parsed data arrays to session queue
                request.session['gl_migration_data'] = parsed_data
                request.session['gl_migration_meta'] = {
                    'client_id': selected_client.id,
                    'client_name': selected_client.name,
                    'batch_name': batch_name
                }
                request.session['gl_migration_log'] = [] # To store report data across multiple saves
                
                messages.success(request, "Data parsed successfully. Please review the batches.")
                return redirect('tools:gl_review')
                
            except Exception as e:
                print(f"❌ Migration Error: {str(e)}")
                messages.error(request, f"Migration Error: {str(e)}")
            finally:
                if os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)
        else:
            messages.error(request, "Form validation failed.")
    else:
        form = GLMigrationUploadForm()
        
    return render(request, 'tools/gl_migration_upload.html', {'form': form})


@login_required
def gl_review_view(request):
    """Processes the session queue in chunks via Formsets."""
    parsed_data = request.session.get('gl_migration_data', {})
    meta = request.session.get('gl_migration_meta', {})
    report_log = request.session.get('gl_migration_log', [])

    if not parsed_data or not meta:
        messages.error(request, "No migration queue found. Please upload a file.")
        return redirect('tools:gl_migration_upload')

    client_id = meta.get('client_id')
    
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
        
    batch_name = meta.get('batch_name')
    selected_client = Client.objects.get(id=client_id)
    
    db_accounts = [(str(a.account_id), f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id)]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    # We process a maximum of 15 items per category per page to prevent browser lag
    CHUNK_SIZE = 15
    purchases_queue = parsed_data.get('purchases', [])
    bank_queue = parsed_data.get('bank_txns', [])
    cash_queue = parsed_data.get('cash_txns', [])

    if request.method == 'POST':
        purchase_formset = GLPurchaseFormSet(request.POST, prefix='purchases', form_kwargs={'account_choices': account_choices})
        bank_formset = GLBankFormSet(request.POST, prefix='bank', form_kwargs={'account_choices': account_choices})
        cash_formset = GLCashFormSet(request.POST, prefix='cash', form_kwargs={'account_choices': account_choices})

        if purchase_formset.is_valid() and bank_formset.is_valid() and cash_formset.is_valid():
            new_log_entries = []
            
            try:
                with transaction.atomic():
                    # 1. PROCESS PURCHASES
                    for form in purchase_formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            cd = form.cleaned_data
                            vendor, _ = Vendor.objects.get_or_create(client=selected_client, name=cd['company'], defaults={'vendor_id': 'V-MIGRATE'})
                            
                            p = Purchase.objects.create(
                                client=selected_client, batch=batch_name, date=cd['date'],
                                company=cd['company'], vendor=vendor, account_id=cd['account_id'],
                                invoice_no=cd.get('gl_no'),
                                vat_usd=cd['vat_usd'] or 0.0, total_usd=cd['total_usd'] or 0.0,
                                description=cd['description'], payment_status='Open',
                                instruction=f"SYSTEM: Migrated from GL (ID: {cd.get('gl_no', 'N/A')})"
                            )
                            new_log_entries.append({'Type': 'Purchase (AP)', 'Date': p.date, 'Entity': p.company, 'Amount': p.total_usd, 'Account': p.account_id})

                    # 2. PROCESS BANK
                    for form in bank_formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            cd = form.cleaned_data
                            b = Bank.objects.create(
                                client=selected_client, batch=batch_name, date=cd['date'],
                                counterparty=cd['counterparty'], purpose="GL Migration",
                                debit=cd['debit'] or 0.0, credit=cd['credit'] or 0.0,
                                instruction=f"SYSTEM: Ledger {cd['ledger_account_id']} | ID: {cd.get('gl_no', 'N/A')}"
                            )
                            new_log_entries.append({'Type': 'Bank', 'Date': b.date, 'Entity': b.counterparty, 'Amount': b.debit or b.credit, 'Account': cd['ledger_account_id']})

                    # 3. PROCESS CASH
                    for form in cash_formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                            cd = form.cleaned_data
                            c = Cash.objects.create(
                                client=selected_client, batch=batch_name, date=cd['date'],
                                description=cd['counterparty'], 
                                debit=cd['debit'] or 0.0, credit=cd['credit'] or 0.0,
                                note=f"SYSTEM: Ledger {cd['ledger_account_id']} | ID: {cd.get('gl_no', 'N/A')}"
                            )
                            new_log_entries.append({'Type': 'Cash', 'Date': c.date, 'Entity': c.description, 'Amount': c.debit or c.credit, 'Account': cd['ledger_account_id']})
            except Exception as e:
                messages.error(request, f"Database transaction failed. Nothing was saved. Error: {str(e)}")
                total_remaining = len(purchases_queue) + len(bank_queue) + len(cash_queue)
                return render(request, 'tools/gl_review.html', {
                    'purchase_formset': purchase_formset,
                    'bank_formset': bank_formset,
                    'cash_formset': cash_formset,
                    'meta': meta,
                    'total_remaining': total_remaining
                })
            
            report_log.extend(new_log_entries)

            # Remove the processed chunk from the session queues
            parsed_data['purchases'] = purchases_queue[CHUNK_SIZE:]
            parsed_data['bank_txns'] = bank_queue[CHUNK_SIZE:]
            parsed_data['cash_txns'] = cash_queue[CHUNK_SIZE:]
            
            request.session['gl_migration_data'] = parsed_data
            request.session['gl_migration_log'] = report_log
            request.session.modified = True

            # If all queues are empty, generate the final report and finish
            if not parsed_data['purchases'] and not parsed_data['bank_txns'] and not parsed_data['cash_txns']:
                if report_log:
                    df_report = pd.DataFrame(report_log)
                    media_dir = os.path.join(settings.BASE_DIR, 'media')
                    os.makedirs(media_dir, exist_ok=True)
                    report_path = os.path.join(media_dir, f'gl_migration_report_{datetime.now().strftime("%Y%m%d%H%M")}.xlsx')
                    df_report.to_excel(report_path, index=False, engine='openpyxl')
                    request.session['gl_report_path'] = report_path

                request.session.pop('gl_migration_data', None)
                request.session.pop('gl_migration_meta', None)
                request.session.pop('gl_migration_log', None)
                
                messages.success(request, "🎉 All historical data batches successfully processed!")
                return redirect('tools:gl_download')
            
            messages.success(request, "Batch saved. Loading next items in queue...")
            return redirect('tools:gl_review')
            
        else:
            messages.error(request, "Validation errors found. Please correct them below.")
    else:
        # Load the next chunk into the forms
        purchase_formset = GLPurchaseFormSet(initial=purchases_queue[:CHUNK_SIZE], prefix='purchases', form_kwargs={'account_choices': account_choices})
        bank_formset = GLBankFormSet(initial=bank_queue[:CHUNK_SIZE], prefix='bank', form_kwargs={'account_choices': account_choices})
        cash_formset = GLCashFormSet(initial=cash_queue[:CHUNK_SIZE], prefix='cash', form_kwargs={'account_choices': account_choices})

    total_remaining = len(purchases_queue) + len(bank_queue) + len(cash_queue)

    return render(request, 'tools/gl_review.html', {
        'purchase_formset': purchase_formset,
        'bank_formset': bank_formset,
        'cash_formset': cash_formset,
        'meta': meta,
        'total_remaining': total_remaining
    })


@login_required
def gl_download_view(request):
    """Provides the download link for the completed migration report."""
    file_path = request.session.get('gl_report_path')
    return render(request, 'tools/gl_download.html', {'has_file': bool(file_path and os.path.exists(file_path))})

@login_required(login_url="register:login")
def PurchaseListView(request):
    user = request.user

    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:purchase_list')

    client_id = request.session.get('active_client_id')
    
    if client_id:
        # Base filtering by client
        base_queryset = Purchase.objects.filter(client_id=client_id)

        # Permission Filtering Logic
        if user.is_staff or user.is_superuser:
            purchases = base_queryset
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=client_id).exists():
                    purchases = base_queryset
                else:
                    purchases = Purchase.objects.none()
                    messages.error(request, "You do not have permission to view purchases for this client.")
            except Profile.DoesNotExist:
                purchases = Purchase.objects.none()
        
        client_form = ClientSelectionForm(initial={'client': client_id})
        vendor_queryset = Vendor.objects.filter(client_id=client_id)
    else:
        purchases = Purchase.objects.none()
        client_form = ClientSelectionForm()
        vendor_queryset = Vendor.objects.none()
        messages.info(request, "Please select a client to view purchases.")

    purchases = purchases.order_by('-id')

    # Initialize Filter
    purchase_filter = PurchaseFilter(request.GET, queryset=purchases)
    purchase_filter.form.fields['vendor'].queryset = vendor_queryset

    # Apply Pagination (20 items per page)
    paginator = Paginator(purchase_filter.qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'filter': purchase_filter,
        'purchases': page_obj,  # 'page_obj' is fully iterable, keeping the template loop happy
        'page_obj': page_obj,
        'client_form': client_form,
    }
    return render(request, 'purchase_list.html', context)


class PurchaseDetailView(LoginRequiredMixin, DetailView):
    login_url = "register:login"
    model = Purchase
    template_name = 'purchase_detail.html'
    context_object_name = 'purchase'

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        purchase = self.get_object()
        is_authorized = user.is_staff or user.is_superuser

        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                # Check if user manages the client
                if profile.clients.filter(id=purchase.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        
        if not is_authorized:
            return HttpResponseForbidden(render(request, 'messages/403_forbidden.html', {'message': "You do not have permission to view this purchase."}))

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        purchase = self.get_object()
        
        # Determine ownership for the template (e.g., showing Edit/Delete buttons)
        is_owner = False
        if user.is_staff or user.is_superuser:
            is_owner = True
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=purchase.client_id).exists():
                    is_owner = True
            except Profile.DoesNotExist:
                pass

        context['is_owner'] = is_owner
        return context


class PurchaseUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = Purchase
    form_class = ManualPurchaseEntryForm 
    template_name = 'purchase_update.html'
    
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        purchase = self.get_object()
        is_authorized = user.is_staff or user.is_superuser

        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=purchase.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        
        if not is_authorized:
            return HttpResponseForbidden(render(request, 'messages/403_forbidden.html', {'message': "You do not have permission to update this purchase."}))

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.object.client_id
        
        db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
        kwargs['vendor_choices'] = [('', '--- Select Existing Vendor ---')] + db_vendors
        
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        
        return kwargs

    def form_valid(self, form):
        # Wrap everything in an atomic transaction to prevent partial writes/duplicates
        with transaction.atomic():
            
            purchase = form.save(commit=False)
            
            for field in ['account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id']:
                val = getattr(purchase, field)
                if val == '' or val == "":
                    setattr(purchase, field, None)
            
            vc = form.cleaned_data.get('vendor_choice')
            if vc:
                purchase.vendor_id = int(vc)
                
            purchase.save() # Updates existing, no duplicate created

            # ==========================================================
            # --- ATOMIC RECALCULATION OF GENERAL LEDGER ---
            # ==========================================================
            
            # 1. Safely wipe old entries. (Requires JournalLine to have on_delete=models.CASCADE in models.py)
            JournalEntry.objects.filter(purchase=purchase).delete()
            
            client_id = purchase.client_id
            
            # 2. Rebuild the entries
            je = JournalEntry.objects.create(
                client=purchase.client,
                date=purchase.date or date.today(),
                description=f"Updated Manual Purchase: {purchase.company}",
                reference_number=purchase.invoice_no,
                purchase=purchase
            )

            total_amount = float(purchase.total_usd or 0.0)
            vat_amount = float(purchase.vat_usd or 0.0)
            unreg_amount = float(purchase.unreg_usd or 0.0)
            
            wht_amount = 0.0
            if purchase.wht_account_id and unreg_amount > 0:
                wht_amount = round(total_amount - unreg_amount, 2)

            if purchase.account_id:
                acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.account_id), defaults={'name': 'Operating Expense', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=acct, description=purchase.description_en or "Expense", debit=(total_amount - vat_amount - wht_amount))

            if vat_amount > 0 and purchase.vat_account_id:
                vat_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.vat_account_id), defaults={'name': 'VAT input', 'account_type': 'Asset'})
                JournalLine.objects.create(journal_entry=je, account=vat_acct, description="Input VAT", debit=vat_amount)

            if wht_amount > 0 and purchase.wht_debit_account_id:
                wht_exp_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.wht_debit_account_id), defaults={'name': 'WHT Expense', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=wht_exp_acct, description="WHT Expense Absorbed", debit=wht_amount)

            if total_amount > 0 and purchase.credit_account_id:
                cr_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.credit_account_id), defaults={'name': 'Trade Payable', 'account_type': 'Liability'})
                JournalLine.objects.create(journal_entry=je, account=cr_acct, description=f"Payable - {purchase.company}", credit=total_amount)

            if wht_amount > 0 and purchase.wht_account_id:
                wht_pay_acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.wht_account_id), defaults={'name': 'WHT Payable', 'account_type': 'Liability'})
                JournalLine.objects.create(journal_entry=je, account=wht_pay_acct, description="WHT Payable to GDT", credit=wht_amount)

        # End of atomic block. If successful, proceed to success message and redirect.
        messages.success(self.request, "Purchase invoice and General Ledger entries updated successfully!")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('tools:purchase_detail', kwargs={'pk': self.object.pk})

class PurchaseDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = Purchase
    template_name = 'purchase_confirm_delete.html'
    success_url = reverse_lazy('tools:purchase_list')

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        purchase = self.get_object()
        is_authorized = user.is_staff or user.is_superuser

        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=purchase.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        
        if not is_authorized:
            return HttpResponseForbidden(render(request, 'messages/403_forbidden.html', {'message': "You do not have permission to delete this purchase."}))

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Clean up associated General Ledger Entries before deleting the purchase
        JournalEntry.objects.filter(purchase=self.object).delete()
        messages.success(self.request, 'Purchase and associated Journal Entries deleted successfully!')
        return super().form_valid(form)

@login_required(login_url="register:login")
def export_purchase_csv(request):
    user = request.user
    client_id = request.session.get('active_client_id')

    # Failsafe if accessed without an active client
    if not client_id:
        return HttpResponse("No active client selected.", status=400)

    # Base filtering by client
    base_queryset = Purchase.objects.filter(client_id=client_id)

    # Permission Filtering Logic (Identical to PurchaseListView)
    if user.is_staff or user.is_superuser:
        purchases = base_queryset
    else:
        try:
            profile = Profile.objects.get(user=user)
            if profile.clients.filter(id=client_id).exists():
                purchases = base_queryset
            else:
                purchases = Purchase.objects.none()
        except Profile.DoesNotExist:
            purchases = Purchase.objects.none()

    purchases = purchases.order_by('-date')

    # Apply the same filter parameters passed via the GET request
    purchase_filter = PurchaseFilter(request.GET, queryset=purchases)
    filtered_purchases = purchase_filter.qs

    # Generate the CSV using django-import-export
    resource = PurchaseResource()
    dataset = resource.export(queryset=filtered_purchases)
    
    # Create and return the HTTP response with the CSV payload
    response = HttpResponse(dataset.csv, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="purchase_invoices.csv"'
    
    return response
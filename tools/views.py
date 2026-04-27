import os
import tempfile
import pandas as pd
import calendar
import io
import re
from collections import defaultdict
import time
import openpyxl
from openpyxl.styles import Alignment
import difflib
from datetime import date, datetime
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, UpdateView, DeleteView
from django.db.models import Sum, Q
from django.db import transaction
from django.core.paginator import Paginator
import pdfplumber

# Import your forms, processors, and local models
from .forms import BatchUploadForm, PurchaseFormSet, ManualPurchaseEntryForm, GLMigrationUploadForm,\
GLHistoricalFormSet, ClientSelectionForm, OldEntryForm, JournalVoucherEntryForm, BalancikaExportForm,\
MultiplePDFUploadForm, MonthlyClosingForm, AccrualFormSet, FXFormSet, EngagementLetterUploadForm
from .processors import GeminiInvoiceProcessor, GLMigrationProcessor, ProposalPDFProcessor, TOSPDFProcessor,\
TaxLiabilitiesProcessor, EngagementLetterProcessor, UnifiedTaxProcessor
from .models import Purchase, AICostLog, Vendor, Client, Old, JournalVoucher
from account.models import Account, JournalEntry, JournalLine, AccountMappingRule, ClientPromptMemo
from register.models import Profile
from .filters import PurchaseFilter, JournalVoucherFilter
from .resources import PurchaseResource
from cash.models import Bank

# ====================================================================
# --- 1. AI INVOICE UPLOAD & PROCESSING ---
# ====================================================================

@login_required(login_url="register:login")
def invoice_ai_upload_view(request):
    """Step 1: Select Client, Upload PDF, Inject Dynamic Rules, Process via AI, and Store."""
    user = request.user

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'process_page':
            job = request.session.get('invoice_job')
            if not job:
                return JsonResponse({"status": "error", "message": "Job session not found."})
                
            page = int(request.POST.get('page', 1))
            api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
            processor = GeminiInvoiceProcessor(api_key=api_key)
            
            print(f"\n[PAGE {page}] EXTRACTING KEY INVOICE DATA FROM AI...")
            ledgers, page_cost, next_seq, err = processor.process_page(
                gemini_file_name=job['gemini_file_name'],
                pg=page,
                client_id=job['client_id'],
                custom_prompt=job['custom_prompt'],
                batch_name=job['batch_name'],
                rules_context=job['rules_context'],
                memo_context=job['memo_context'],
                current_invoice_seq=job['current_seq'],
                date_prefix=job['date_prefix']
            )
            
            print(f"✅ [Page {page}] Extraction Complete | AI Cost: ${page_cost:.5f}")
            if ledgers:
                print(f"   🎉 Extracted {len(ledgers)} invoices from Page {page}.")
                for item in ledgers:
                    print(f"      🔹 Inv No: {item.get('invoice_no', 'N/A')} | Vendor: {str(item.get('company', 'N/A'))[:30]} | Total: ${item.get('total_usd', 0.0)} | VAT: ${item.get('vat_usd', 0.0)}")
                job['results'].extend(ledgers)
            else:
                print(f"   ⚠️ No invoices extracted from Page {page}.")
                if err:
                    print(f"      ❌ Error: {err}")

            job['current_seq'] = next_seq
            job['costs']['pro_cost'] += page_cost
            request.session['invoice_job'] = job
            request.session.save()
            
            return JsonResponse({"status": "success", "page": page, "ledgers_count": len(ledgers) if ledgers else 0, "error": err})
            
        if action == 'finalize':
            job = request.session.get('invoice_job')
            if not job:
                return JsonResponse({"status": "error", "message": "Job session not found."})
                
            request.session[f'invoice_seq_{job["client_id"]}'] = job['current_seq']
            
            print("\n[FINALIZING] LOGGING AI COSTS AND SAVING STATE...")
            total_flash = job['costs']['flash_cost']
            total_pro = job['costs']['pro_cost']
            total_cost = total_flash + total_pro
            print(f"💰 Total AI Cost for this batch: ${total_cost:.5f}")
            
            try:
                AICostLog.objects.create(file_name=job['file_name'], total_pages=job['total_pages'], flash_cost=total_flash, pro_cost=total_pro, total_cost=total_cost)
            except NameError:
                pass
                
            request.session['extracted_invoices'] = job['results']
            request.session['ai_metadata'] = {'file_name': job['file_name'], 'batch_name': job['batch_name'], 'client_id': job['client_id'], 'client_name': Client.objects.get(id=job['client_id']).name, 'total_pages': job['total_pages'], 'costs': job['costs']}
            request.session.pop('invoice_job', None)
            
            print("✅ Process complete. Redirecting to review screen.")
            print("="*50 + "\n")
            
            return JsonResponse({"status": "success", "redirect_url": reverse('tools:review_invoices')})

        request.session.pop('invoice_report_path', None)
        
        form = BatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=selected_client.id).exists():
                        has_access = True
                except Exception:
                    pass
            if not has_access:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({"status": "error", "message": "Permission denied."})
                else:
                    messages.error(request, "You do not have permission to upload data for this client.")
                    return redirect('main')

            uploaded_pdf = form.cleaned_data['invoice_pdf']
            batch_name = form.cleaned_data['batch_name']
            custom_prompt = form.cleaned_data.get('ai_prompt', '')
            
            # ==========================================================
            # SEQUENCE & DATE RESOLUTION (Deterministic Processing)
            # ==========================================================
            # 1. Fetch the global sequence tracker from the user's session (Defaults to 1 for new sessions)
            current_seq = request.session.get(f'invoice_seq_{selected_client.id}', 1)
            
            # 2. Extract YYYYMMDD date from user's custom prompt (e.g., looks for 20260226)
            date_match = re.search(r'\b(202\d[0-1]\d[0-3]\d)\b', custom_prompt)
            if date_match:
                date_prefix = date_match.group(1)
            else:
                # Fallback to today's date if the user didn't provide a valid YYYYMMDD string
                date_prefix = datetime.now().strftime("%Y%m%d")

            print(f"🔢 Target Sequence Starting at: {current_seq} | Prefix: INV-{date_prefix}-")

            # ==========================================================
            # --- DYNAMIC MULTI-TENANT RULE INJECTION ---
            # ==========================================================
            rules_context = ""
            memo_context = ""

            client_memo = ClientPromptMemo.objects.filter(client=selected_client).first()
            if client_memo:
                # Make sure the new Invoice Extraction rules are appended here if not physically in the DB yet
                memo_context = client_memo.memo_text

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
                print("\n" + "="*50)
                print(f"🚀 STARTING INVOICE AI PROCESSING for {selected_client.name}")
                print("="*50)
                print("\n[INITIALIZING] UPLOADING PDF TO GEMINI...")
                
                # Strict Environment Variable Validation
                api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
                if not api_key:
                    return JsonResponse({"status": "error", "message": "System Error: GEMINI_API_KEY_2 is missing or not configured."})

                processor = GeminiInvoiceProcessor(api_key=api_key)
                
                with pdfplumber.open(tmp_pdf_path) as pdf:
                    total_pages = len(pdf.pages)
                    if total_pages > 20:
                        return JsonResponse({"status": "error", "message": f"Limit exceeded. PDF has {total_pages} pages, max is 20."})
                
                f = processor.client.files.upload(file=tmp_pdf_path)
                while f.state.name == "PROCESSING": 
                    time.sleep(2)
                    f = processor.client.files.get(name=f.name)
                    
                print(f"✅ Uploaded {total_pages} pages successfully. Ready for extraction.")
                
                request.session['invoice_job'] = {
                    'gemini_file_name': f.name,
                    'file_name': uploaded_pdf.name,
                    'total_pages': total_pages,
                    'client_id': selected_client.id,
                    'batch_name': batch_name,
                    'custom_prompt': custom_prompt,
                    'rules_context': rules_context,
                    'memo_context': memo_context,
                    'current_seq': current_seq,
                    'date_prefix': date_prefix,
                    'results': [],
                    'costs': {'flash_cost': 0.0, 'pro_cost': 0.0}
                }
                request.session.save()
                
                return JsonResponse({"status": "init_success", "total_pages": total_pages})
                
            except ValueError as ve:
                return JsonResponse({"status": "error", "message": str(ve)})
            except Exception as e:
                return JsonResponse({"status": "error", "message": f"AI Initialization Error: {str(e)}"})
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
        else:
            return JsonResponse({"status": "error", "message": "Form validation failed. Check required fields."})
    else:
        form = BatchUploadForm()
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
        messages.error(request, "You do not have permission to review this client's data.")
        return redirect('main')
    
    # --- VENDOR CHOICES ---
    # Isolate vendors exclusively to this client
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    
    # Re-sequence new vendors so they group correctly and have unique temp_ids
    all_vids = Vendor.objects.filter(client_id=client_id).values_list('vendor_id', flat=True)
    max_num = 1
    for vid in all_vids:
        if vid:
            match = re.search(r'V-?(\d+)', str(vid))
            if match:
                max_num = max(max_num, int(match.group(1)))
    next_num = max_num + 1

    new_vendor_map = {}
    for item in extracted_data:
        if item.get('is_new_vendor'):
            raw_name = item.get('company', 'Unknown')
            name_str = str(raw_name).lower().replace('&', ' and ')
            target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()
            
            if target_norm not in new_vendor_map:
                current_seq = next_num + len(new_vendor_map)
                new_vid = f"V-{current_seq:05d}"
                new_temp_id = f"TEMP_{new_vid}"
                new_vendor_map[target_norm] = {
                    'temp_vid': new_vid,
                    'temp_id': new_temp_id,
                    'company': raw_name
                }
            
            mapped = new_vendor_map[target_norm]
            item['temp_vid'] = mapped['temp_vid']
            item['temp_id'] = mapped['temp_id']
            item['vendor_choice'] = mapped['temp_id']

    temp_vendors = []
    for mapped in new_vendor_map.values():
        temp_vendors.append((mapped['temp_id'], f"✨ NEW: {mapped['company']} ({mapped['temp_vid']})"))
        
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors

    # --- ACCOUNT CHOICES ---
    # Fetch all global accounts to allow users to select correct accounts if they aren't explicitly mapped to this client yet
    seen_accounts = set()
    db_accounts = []
    for acc_id, name in Account.objects.values_list('account_id', 'name'):
        if acc_id not in seen_accounts:
            seen_accounts.add(acc_id)
            db_accounts.append((str(acc_id), f"{acc_id} - {name}"))
    db_accounts.sort(key=lambda x: str(x[0]))
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

                            main_net = net_amount

                            if main_net > 0:
                                ai_account_id = str(form_debit_acct) if form_debit_acct else '725080'
                                exp_account, _ = Account.objects.get_or_create(
                                    client_id=client_id, account_id=ai_account_id, 
                                    defaults={'name': 'Operating Expense', 'account_type': 'Expense'}
                                )
                                JournalLine.objects.create(
                                    journal_entry=je, account=exp_account, 
                                    description=purchase_instance.description_en or purchase_instance.description or "Expense", 
                                    debit=main_net
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

@login_required(login_url="register:login")
def ai_cost_dashboard(request):
    """Dashboard to review AI processing costs."""
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "You do not have permission to view the AI cost dashboard.")
        return redirect('main')

    cost_logs_list = AICostLog.objects.all().order_by('-date')

    paginator = Paginator(cost_logs_list, 20)  # 20 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    totals = AICostLog.objects.aggregate(
        total_flash=Sum('flash_cost'), 
        total_pro=Sum('pro_cost'), 
        grand_total=Sum('total_cost'), 
        total_pages=Sum('total_pages')
    )
    return render(request, 'cost_dashboard.html', {'cost_logs': page_obj, 'totals': totals, 'page_obj': page_obj})

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
            return redirect('main')

    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    # Fetch dynamic choices
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    vendor_choices = [('', '--- Select Existing Vendor ---')] + db_vendors

    seen_accounts = set()
    db_accounts = []
    for acc_id, name in Account.objects.values_list('account_id', 'name'):
        if acc_id not in seen_accounts:
            seen_accounts.add(acc_id)
            db_accounts.append((str(acc_id), f"{acc_id} - {name}"))
    db_accounts.sort(key=lambda x: str(x[0]))
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

                main_net = round((total_amount - vat_amount - wht_amount), 2)
                
                if purchase.account_id and main_net > 0:
                    acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.account_id), defaults={'name': 'Operating Expense', 'account_type': 'Expense'})
                    JournalLine.objects.create(journal_entry=je, account=acct, description=purchase.description_en or "Expense", debit=main_net)

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
        messages.error(request, "You do not have permission to export this client's data.")
        return redirect('main')

    # Base Queryset: Ensure sequential order based on entry processing instead of randomly descending
    queryset = Purchase.objects.filter(client_id=client.id).select_related('client', 'vendor').prefetch_related('journal_entries__lines__account').order_by('id')

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
        request.session.pop('gl_migration_completed', None)
        
        form = GLMigrationUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            
            user = request.user
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=selected_client.id).exists():
                        has_access = True
                except Exception:
                    pass
            if not has_access:
                messages.error(request, "You do not have permission to migrate data for this client.")
                return redirect('main')
                
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
                
                # --- NEW: PREVENT GHOST SAVES (Hard Stop if AI returned nothing) ---
                if not parsed_data:
                    messages.error(request, "AI Extraction Failed: No valid transactions were processed. Please check the file formatting or terminal logs for Pydantic schema errors.")
                    return redirect('tools:gl_migration_upload')
                
                # Log the AI Cost Immediately
                total_items = len(parsed_data)
                AICostLog.objects.create(
                    file_name=uploaded_file.name, 
                    total_pages=total_items, # Treat the number of extracted lines as 'pages'
                    flash_cost=costs.get('flash_cost', 0), 
                    pro_cost=costs.get('pro_cost', 0), 
                    total_cost=costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                )
                
                # Save the parsed data arrays to session queue
                request.session['gl_migration_data'] = {'lines': parsed_data}
                request.session['gl_migration_meta'] = {
                    'client_id': selected_client.id,
                    'client_name': selected_client.name,
                    'batch_name': batch_name
                }
                request.session['gl_migration_log'] = [] 
                request.session['gl_migration_completed'] = []
                
                messages.success(request, f"Data parsed successfully. Found {total_items} lines. Please review the batches.")
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
    """Processes the session queue in chunks via Formsets. Saves to DB only when queue is completely empty."""
    parsed_data = request.session.get('gl_migration_data', {})
    meta = request.session.get('gl_migration_meta', {})
    completed_data = request.session.get('gl_migration_completed', [])

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
        except Exception:
            pass
    if not has_access:
        messages.error(request, "You do not have permission to review this client's data.")
        return redirect('main')
        
    selected_client = Client.objects.get(id=client_id)
    
    seen_accounts = set()
    db_accounts = []
    for acc_id, name in Account.objects.filter(client_id=client_id).values_list('account_id', 'name'):
        if acc_id not in seen_accounts:
            seen_accounts.add(acc_id)
            db_accounts.append((str(acc_id), f"{acc_id} - {name}"))
    db_accounts.sort(key=lambda x: str(x[0]))
    account_choices = [('', '--- Select Account ---')] + db_accounts

    # We process a maximum of 30 items per page to prevent browser lag
    CHUNK_SIZE = 30
    lines_queue = parsed_data.get('lines', [])

    if request.method == 'POST':
        formset = GLHistoricalFormSet(request.POST, form_kwargs={'account_choices': account_choices})

        if formset.is_valid():
            # Extract cleaned data from this chunk
            chunk_results = []
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    cd = form.cleaned_data
                    chunk_results.append({
                        'gl_no': cd.get('gl_no') or 'UNGROUPED',
                        'date': str(cd['date']) if cd.get('date') else None,
                        'account_id': cd['account_id'],
                        'description': cd['description'],
                        'instruction': cd.get('instruction', ''),
                        'debit': cd['debit'] or 0.0,
                        'credit': cd['credit'] or 0.0
                    })
            
            # Add to the session's completed list
            completed_data.extend(chunk_results)
            request.session['gl_migration_completed'] = completed_data

            # Remove the processed chunk from the session queues
            parsed_data['lines'] = lines_queue[CHUNK_SIZE:]
            
            request.session['gl_migration_data'] = parsed_data
            request.session.modified = True

            # If queue is empty, we are done reviewing! Now perform atomic DB save.
            if not parsed_data['lines']:
                report_log = []
                
                # --- NEW: PREVENT GHOST SAVES (Hard Stop if completed_data is empty) ---
                if not completed_data:
                    messages.error(request, "CRITICAL ERROR: Attempted to save an empty dataset to the database.")
                    return redirect('tools:gl_migration_upload')
                
                try:
                    with transaction.atomic():
                        for item in completed_data:
                            # 1. PROCESS AND SAVE TO 'Old' MODEL
                            old_record = Old.objects.create(
                                client=selected_client,
                                date=item['date'] or date.today(),
                                account_id=item['account_id'],
                                description=item['description'],
                                instruction=item['instruction'],
                                debit=item['debit'],
                                credit=item['credit']
                            )
                            
                            gl_no = item['gl_no']
                            
                            # 2. CREATE LINKED JOURNAL ENTRY
                            ref = f"HIST-{gl_no}" if gl_no and gl_no != 'UNGROUPED' else f"OLD-{old_record.id}"
                            je = JournalEntry.objects.create(
                                client=selected_client,
                                date=item['date'] or date.today(),
                                reference_number=ref,
                                description=f"Historical GL Migration: {item.get('description', '')}"[:255],
                                old=old_record
                            )
                            
                            # 3. CREATE JOURNAL LINE
                            account, _ = Account.objects.get_or_create(
                                account_id=str(item['account_id']),
                                client=selected_client,
                                defaults={'name': 'System Gen Acct', 'account_type': 'Asset'}
                            )
                            
                            safe_desc = item['description'][:255] if item.get('description') else "Historical Entry"
                            if item['debit'] > 0 and item['credit'] > 0:
                                JournalLine.objects.create(journal_entry=je, account=account, debit=item['debit'], credit=0.0, description=safe_desc)
                                JournalLine.objects.create(journal_entry=je, account=account, debit=0.0, credit=item['credit'], description=safe_desc)
                            else:
                                JournalLine.objects.create(
                                    journal_entry=je,
                                    account=account,
                                    debit=item['debit'],
                                    credit=item['credit'],
                                    description=safe_desc
                                )
                            
                            report_log.append({
                                'GL No': gl_no, 'Date': item['date'], 
                                'Account': item['account_id'], 
                                'Debit': item['debit'], 'Credit': item['credit'], 
                                'Description': item['description']
                            })
                except Exception as e:
                    messages.error(request, f"Database transaction failed during final save. Nothing was saved. Error: {str(e)}")
                    return render(request, 'tools/gl_review.html', {
                        'formset': formset, 'meta': meta, 'total_remaining': 0
                    })

                # Generate final report
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
                request.session.pop('gl_migration_completed', None)
                
                messages.success(request, f"🎉 All {len(completed_data)} historical records successfully staged to Old model and mapped to Journals!")
                return redirect('tools:gl_download')
            
            messages.success(request, "Batch reviewed and queued. Loading next items...")
            return redirect('tools:gl_review')
            
        else:
            messages.error(request, "Validation errors found. Please correct them below.")
    else:
        # Load the next chunk into the forms
        formset = GLHistoricalFormSet(initial=lines_queue[:CHUNK_SIZE], form_kwargs={'account_choices': account_choices})

    total_remaining = len(lines_queue)

    return render(request, 'tools/gl_review.html', {
        'formset': formset,
        'meta': meta,
        'total_remaining': total_remaining
    })

@login_required
def gl_download_view(request):
    """Provides the download link for the completed migration report."""
    file_path = request.session.get('gl_report_path')
    has_file = bool(file_path and os.path.exists(file_path))
    file_url = f"/media/{os.path.basename(file_path)}" if has_file else ""
    return render(request, 'tools/gl_download.html', {
        'has_file': has_file,
        'file_url': file_url
    })

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
                    messages.error(request, "You do not have permission to view purchases for this client.")
                    return redirect('main')
            except Profile.DoesNotExist:
                messages.error(request, "You do not have permission to view purchases for this client.")
                return redirect('main')
        
        client_form = ClientSelectionForm(initial={'client': client_id})
        vendor_queryset = Vendor.objects.filter(client_id=client_id).order_by('vendor_id')
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
            messages.error(request, "You do not have permission to view this purchase.")
            return redirect('main')

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
            messages.error(request, "You do not have permission to update this purchase.")
            return redirect('main')

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        client_id = self.object.client_id
        
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

            main_net = (total_amount - vat_amount - wht_amount)
            
            if purchase.account_id and main_net > 0:
                acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(purchase.account_id), defaults={'name': 'Operating Expense', 'account_type': 'Expense'})
                JournalLine.objects.create(journal_entry=je, account=acct, description=purchase.description_en or "Expense", debit=main_net)

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
            messages.error(request, "You do not have permission to delete this purchase.")
            return redirect('main')

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
        messages.error(request, "No active client selected.")
        return redirect('main')

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
                messages.error(request, "You do not have permission to export data for this client.")
                return redirect('main')
        except Profile.DoesNotExist:
            messages.error(request, "You do not have permission to export data for this client.")
            return redirect('main')

    purchases = purchases.select_related('client', 'vendor').prefetch_related('journal_entries__lines__account').order_by('id')

    # Apply the same filter parameters passed via the GET request
    purchase_filter = PurchaseFilter(request.GET, queryset=purchases)
    filtered_purchases = purchase_filter.qs

    # Generate the CSV using django-import-export
    resource = PurchaseResource(client_id=client_id)
    dataset = resource.export(queryset=filtered_purchases)
    
    # Create and return the HTTP response with a UTF-8 BOM so Excel reads Chinese characters properly
    response = HttpResponse(dataset.csv.encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="purchase_invoices.csv"'
    
    return response

# ====================================================================
# --- 4. OLD MODEL CRUD & JOURNAL POSTING ---
# ====================================================================

@login_required(login_url="register:login")
def OldListView(request):
    user = request.user

    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:old_list')

    client_id = request.session.get('active_client_id')
    
    if client_id:
        base_queryset = Old.objects.filter(client_id=client_id)

        if user.is_staff or user.is_superuser:
            old_records = base_queryset
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=client_id).exists():
                    old_records = base_queryset
                else:
                    messages.error(request, "You do not have permission to view records for this client.")
                    return redirect('main')
            except Profile.DoesNotExist:
                messages.error(request, "You do not have permission to view records for this client.")
                return redirect('main')
        
        client_form = ClientSelectionForm(initial={'client': client_id})
    else:
        old_records = Old.objects.none()
        client_form = ClientSelectionForm()
        messages.info(request, "Please select a client to view historical records.")

    old_records = old_records.order_by('-id')

    paginator = Paginator(old_records, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'old_records': page_obj,
        'page_obj': page_obj,
        'client_form': client_form,
    }
    return render(request, 'tools/old_list.html', context)


@login_required(login_url="register:login")
def manual_old_entry_view(request):
    if request.method == 'POST' and 'client' in request.POST and 'account_id' not in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:manual_old_entry')

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
            return redirect('main')

    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    seen_accounts = set()
    db_accounts = []
    for acc_id, name in Account.objects.values_list('account_id', 'name'):
        if acc_id not in seen_accounts:
            seen_accounts.add(acc_id)
            db_accounts.append((str(acc_id), f"{acc_id} - {name}"))
    db_accounts.sort(key=lambda x: str(x[0]))
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = OldEntryForm(request.POST, account_choices=account_choices)
        if form.is_valid():
            with transaction.atomic():
                old_record = form.save()

                # Post to General Ledger, linking via reference_number
                je = JournalEntry.objects.create(
                    client=old_record.client,
                    date=old_record.date or date.today(),
                    description=f"Historical Entry: {old_record.description}"[:255],
                    reference_number=f"OLD-{old_record.id}",
                    old=old_record
                )
                
                acct, _ = Account.objects.get_or_create(
                    client_id=client_id, 
                    account_id=str(old_record.account_id), 
                    defaults={'name': 'Historical Default', 'account_type': 'Asset'}
                )
                safe_desc = old_record.description[:255] if old_record.description else "Historical Entry"
                debit_val = old_record.debit or 0.0
                credit_val = old_record.credit or 0.0
                
                if debit_val > 0 and credit_val > 0:
                    JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=0.0)
                    JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=0.0, credit=credit_val)
                else:
                    JournalLine.objects.create(
                        journal_entry=je, 
                        account=acct, 
                        description=safe_desc, 
                        debit=debit_val,
                        credit=credit_val
                    )
            
            messages.success(request, f"Successfully created manual historical record and posted to GL.")
            return redirect('tools:old_list') 
    else:
        form = OldEntryForm(initial={'client': client_id}, account_choices=account_choices)

    return render(request, 'tools/old_form.html', {'form': form})


class OldDetailView(LoginRequiredMixin, DetailView):
    login_url = "register:login"
    model = Old
    template_name = 'tools/old_detail.html'
    context_object_name = 'old_record'

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        old_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=old_record.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        if not is_authorized:
            messages.error(request, "Permission denied.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        old_record = self.get_object()
        context['is_owner'] = user.is_staff or user.is_superuser or (hasattr(user, 'profile') and user.profile.clients.filter(id=old_record.client_id).exists())
        return context


class OldUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = Old
    form_class = OldEntryForm 
    template_name = 'tools/old_form.html'
    
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        old_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=old_record.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        if not is_authorized:
            messages.error(request, "You do not have permission to update this record.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=self.object.client_id).order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            old_record = form.save()
            JournalEntry.objects.filter(Q(old=old_record) | Q(reference_number=f"OLD-{old_record.id}")).delete()
            
            je = JournalEntry.objects.create(
                client=old_record.client, date=old_record.date or date.today(),
                description=f"Updated Historical Entry: {old_record.description}"[:255], 
                reference_number=f"OLD-{old_record.id}",
                old=old_record
            )
            acct, _ = Account.objects.get_or_create(client_id=old_record.client_id, account_id=str(old_record.account_id), defaults={'name': 'Historical Default', 'account_type': 'Asset'})
            safe_desc = old_record.description[:255] if old_record.description else "Historical Entry"
            debit_val = old_record.debit or 0.0
            credit_val = old_record.credit or 0.0
            if debit_val > 0 and credit_val > 0:
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=0.0)
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=0.0, credit=credit_val)
            else:
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=credit_val)
        messages.success(self.request, "Historical record and General Ledger entries updated successfully!")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('tools:old_detail', kwargs={'pk': self.object.pk})

class OldDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = Old
    template_name = 'tools/old_confirm_delete.html'
    success_url = reverse_lazy('tools:old_list')

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        old_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=old_record.client_id).exists():
                    is_authorized = True
            except Profile.DoesNotExist:
                pass
        if not is_authorized:
            messages.error(request, "You do not have permission to delete this record.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        JournalEntry.objects.filter(Q(old=self.object) | Q(reference_number=f"OLD-{self.object.id}")).delete()
        messages.success(self.request, 'Record and associated Journal Entries deleted successfully!')
        return super().form_valid(form)

############################

# Export Balancika Sheets

############################

def clean_invoice_number(val):
    s = str(val).strip()
    if s.lower() in ['nan', 'none'] or s == '': 
        return ''
    if re.match(r'^-?\d+(\.\d+)?[eE][+\-]?\d+$', s):
        try:
            return '{:.0f}'.format(float(s))
        except:
            return s
    return s.replace('.0', '')

@login_required
def export_balancika_view(request):
    if request.method == 'POST':
        form = BalancikaExportForm(request.POST)
        if form.is_valid():
            client = form.cleaned_data['client']
            start_date = form.cleaned_data.get('start_date')
            end_date = form.cleaned_data.get('end_date')
            purchase_id = form.cleaned_data.get('purchase_id')
            bank_id = form.cleaned_data.get('bank_id')
            entry_counter = form.cleaned_data['entry_no_start']
            
            user = request.user
            has_access = user.is_staff or user.is_superuser
            if not has_access:
                try:
                    if user.profile.clients.filter(id=client.id).exists():
                        has_access = True
                except Profile.DoesNotExist:
                    pass
            if not has_access:
                messages.error(request, "You do not have permission to export data for this client.")
                return redirect('main')

            purchase_filters = Q(client=client)
            bank_filters = Q(client=client, credit__gt=0)

            if start_date:
                purchase_filters &= Q(date__gte=start_date)
                bank_filters &= Q(date__gte=start_date)
            if end_date:
                purchase_filters &= Q(date__lte=end_date)
                bank_filters &= Q(date__lte=end_date)

            purchases = []
            bank_charges = []

            if purchase_id:
                purchases = list(Purchase.objects.filter(purchase_filters & Q(id=purchase_id)))
            elif bank_id:
                bank_charges = list(Bank.objects.filter(bank_filters & Q(id=bank_id)))
            else:
                purchases = list(Purchase.objects.filter(purchase_filters))
                
                bank_fee_triggers = ['interbank fund', 'checkbook', 'commission']
                q_objects = Q()
                for trigger in bank_fee_triggers:
                    q_objects |= Q(purpose__icontains=trigger) | Q(trans_type__icontains=trigger) | Q(remark__icontains=trigger) | Q(raw_remark__icontains=trigger)
                
                bank_charges = list(Bank.objects.filter(bank_filters & q_objects))

            combined_records = purchases + bank_charges
            combined_records.sort(key=lambda x: (x.date if x.date else date.min, x.id))

            if not combined_records:
                messages.warning(request, f"No purchases or bank charges found for {client.name} with the given criteria.")
                return render(request, 'tools/balancika_export.html', {'form': form})

            # Calculate base month start and end dates
            base_start_str = start_date.strftime('%d-%b-%Y') if start_date else date.today().strftime('%d-%b-%Y')
            base_end_str = end_date.strftime('%d-%b-%Y') if end_date else date.today().strftime('%d-%b-%Y')

            sheet1_data = []
            sheet2_data = []

            for record in combined_records:
                entry_no = f"PIN-{entry_counter:05d}"
                entry_counter += 1

                if isinstance(record, Purchase):
                    p = record
                    # Mappings from Django Model (Adjust field names if your model differs slightly)
                    original_acct_id = str(getattr(p, 'account_id', '')).strip()
                    original_vendor_id = str(p.vendor.vendor_id).strip() if p.vendor else ''
                    original_invoice = clean_invoice_number(getattr(p, 'invoice_no', ''))
                    description = str(getattr(p, 'description', '')).strip()
                    description_en = str(getattr(p, 'description_en', '')).strip()

                    # Date Formatting
                    if p.date:
                        final_date = p.date.strftime('%d-%b-%Y')
                        # Find the last day of the transaction's month
                        _, p_last_day = calendar.monthrange(p.date.year, p.date.month)
                        final_due_date = date(p.date.year, p.date.month, p_last_day).strftime('%d-%b-%Y')
                    else:
                        final_date, final_due_date = base_start_str, base_end_str

                    # --- SHEET 1 POPULATION ---
                    sheet1_data.append({
                        "Entry No": entry_no,
                        "Date (dd-MMM-YYYY)": final_date,
                        "Type": "Apply to GL Account",
                        "Reference": original_invoice,
                        "Remark": description_en if description_en else description,
                        "Vendor ID": original_vendor_id,
                        "Employee ID": "",
                        "Class ID": "",
                        "Due Date (dd-MMM-YYYY)": final_due_date,
                        "Purchase Order": "",
                        "Currency ID": "USD",
                        "Exchange Rate": 1
                    })

                    # --- SHEET 2 SCENARIO LOGIC ---
                    # Safely get numeric amounts from the Django model
                    local_vat_amt = float(getattr(p, 'vat_usd', 0.0) or 0.0)
                    total_amt = float(getattr(p, 'total_usd', 0.0) or 0.0)
                    
                    # If your model has specific non-vat fields, use them. Otherwise, calculate.
                    local_purchase_amt = float(getattr(p, 'local_purchase_usd', total_amt - local_vat_amt) or 0.0)
                    non_vat_amt = float(getattr(p, 'non_vat_usd', 0.0) or 0.0) 

                    desc_lower = description.lower()
                    display_desc = description_en if description_en else description
                    rows_to_add = []

                    # RENTAL SCENARIO
                    if 'rental' in desc_lower:
                        wht_val = total_amt * 0.10
                        rows_to_add.append({"Desc": display_desc, "Cost": total_amt, "Total": total_amt, "Line": 1, "VAT": "None", "AcctID": original_acct_id})
                        rows_to_add.append({"Desc": "10% WHT on Rental", "Cost": -wht_val, "Total": -wht_val, "Line": 2, "VAT": "None", "AcctID": "23500"})
                        rows_to_add.append({"Desc": "10% WHT on Rental Expense", "Cost": wht_val, "Total": wht_val, "Line": 3, "VAT": "None", "AcctID": "65000"})

                    # NSSF SCENARIO
                    elif ("nssf" in desc_lower or "occupational risk" in desc_lower) and "pension" in desc_lower:
                        parts = [pt.strip() for pt in re.split(r'\.\s+', description) if pt.strip()]
                        for idx, pt in enumerate(parts[:3]):
                            m = re.search(r"(?:--|-)\s*([\d,]+\.?\d*)", pt)
                            amt = float(m.group(1).replace(',', '')) if m else 0.0
                            rows_to_add.append({"Desc": pt, "Cost": amt, "Total": amt, "Line": idx+1, "VAT": "None", "AcctID": original_acct_id})

                    # SCENARIOS B/D/A
                    elif non_vat_amt != 0 and local_vat_amt != 0:
                        rows_to_add.append({"Desc": display_desc, "Cost": local_purchase_amt, "Total": local_purchase_amt, "Line": 1, "VAT": "VAT_IN_1", "AcctID": original_acct_id})
                        rows_to_add.append({"Desc": display_desc, "Cost": non_vat_amt, "Total": non_vat_amt, "Line": 2, "VAT": "None", "AcctID": original_acct_id})
                    elif local_vat_amt != 0:
                        rows_to_add.append({"Desc": display_desc, "Cost": local_purchase_amt, "Total": local_purchase_amt, "Line": 1, "VAT": "VAT_IN_1", "AcctID": original_acct_id})
                    else:
                        rows_to_add.append({"Desc": display_desc, "Cost": total_amt, "Total": total_amt, "Line": 1, "VAT": "None", "AcctID": original_acct_id})

                    for r in rows_to_add:
                        f_acct = str(r["AcctID"])
                        sheet2_data.append({
                            "Entry No": entry_no,
                            "Description": r["Desc"],
                            "Account ID": f_acct, 
                            "Item ID": "", 
                            "Location ID": "", 
                            "Lot Number": "",
                            "Manufacture Date (dd-MMM-YYYY)": "", 
                            "Expiry Date (dd-MMM-YYYY)": "",
                            "Class ID": "", 
                            "Uom ID": "",
                            "Quantity": 1, 
                            "FOC": 0,
                            "Unit Cost": r["Cost"], 
                            "Total Cost": r["Cost"],
                            "Disc %": 0, 
                            "Disc": 0,
                            "Grand Total": r["Total"],
                            "Purchase Order No": "", 
                            "Purchase Order Line": r["Line"],
                            "Job No": "", 
                            "Job Task No": 0, 
                            "Job Planning Line": 0,
                            "WHT": "None", 
                            "Tax Group 2": "None", 
                            "Tax Group 3": "None",
                            "VAT Input": r["VAT"]
                        })
                else:
                    b = record
                    original_acct_id = str(getattr(b, 'debit_account_id', ''))
                    original_vendor_id = str(b.vendor.vendor_id).strip() if b.vendor else ''
                    original_invoice = b.bank_ref_id or ''
                    
                    trans_type = str(getattr(b, 'trans_type', '')).strip()
                    purpose = str(getattr(b, 'purpose', '')).strip()
                    remark = str(getattr(b, 'remark', '')).strip()
                    
                    display_desc = remark if remark else purpose
                    if trans_type and trans_type.lower() not in display_desc.lower():
                        display_desc = f"{trans_type} - {display_desc}" if display_desc else trans_type
                    if not display_desc:
                        display_desc = "Bank Charge"
                    
                    if b.date:
                        final_date = b.date.strftime('%d-%b-%Y')
                        _, b_last_day = calendar.monthrange(b.date.year, b.date.month)
                        final_due_date = date(b.date.year, b.date.month, b_last_day).strftime('%d-%b-%Y')
                    else:
                        final_date, final_due_date = base_start_str, base_end_str
                        
                    sheet1_data.append({
                        "Entry No": entry_no,
                        "Date (dd-MMM-YYYY)": final_date,
                        "Type": "Apply to GL Account",
                        "Reference": original_invoice,
                        "Remark": display_desc,
                        "Vendor ID": original_vendor_id,
                        "Employee ID": "",
                        "Class ID": "",
                        "Due Date (dd-MMM-YYYY)": final_due_date,
                        "Purchase Order": "",
                        "Currency ID": "USD",
                        "Exchange Rate": 1
                    })
                    
                    charge_amt = float(b.credit)
                    
                    sheet2_data.append({
                        "Entry No": entry_no,
                        "Description": display_desc,
                        "Account ID": original_acct_id, 
                        "Item ID": "", 
                        "Location ID": "", 
                        "Lot Number": "",
                        "Manufacture Date (dd-MMM-YYYY)": "", 
                        "Expiry Date (dd-MMM-YYYY)": "",
                        "Class ID": "", 
                        "Uom ID": "",
                        "Quantity": 1, 
                        "FOC": 0,
                        "Unit Cost": charge_amt, 
                        "Total Cost": charge_amt,
                        "Disc %": 0, 
                        "Disc": 0,
                        "Grand Total": charge_amt,
                        "Purchase Order No": "", 
                        "Purchase Order Line": 1,
                        "Job No": "", 
                        "Job Task No": 0, 
                        "Job Planning Line": 0,
                        "WHT": "None", 
                        "Tax Group 2": "None", 
                        "Tax Group 3": "None",
                        "VAT Input": "None"
                    })

            # --- GENERATE EXCEL FILE IN MEMORY ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                pd.DataFrame(sheet1_data).to_excel(writer, sheet_name='Sheet1', index=False)
                pd.DataFrame(sheet2_data).to_excel(writer, sheet_name='Sheet2', index=False)
            
            # Rewind the buffer
            output.seek(0)

            # --- RETURN AS DOWNLOADABLE ATTACHMENT ---
            date_range_str = ""
            if start_date and end_date:
                date_range_str = f"_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}"
            elif start_date:
                date_range_str = f"_from_{start_date.strftime('%Y%m%d')}"
            elif end_date:
                date_range_str = f"_to_{end_date.strftime('%Y%m%d')}"
            
            filename = f"Balancika_Export_{client.name.replace(' ', '_')}{date_range_str}.xlsx"
            response = HttpResponse(
                output.read(), 
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    else:
        form = BalancikaExportForm()
        user = request.user
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()

    return render(request, 'tools/balancika_export.html', {'form': form})

# ====================================================================
# UPDATE ENGAGEMENT LETTER
# CONFIGURATION
# ====================================================================


@login_required
def upload_proposals_view(request):

    SHEET_NAME = getattr(settings, 'PROPOSAL_EXCEL_SHEET_NAME', 'Masterlist')

    # Column mapping for improved readability and maintainability
    COLUMN_MAP = {
        'NO': 2,
        'PROPOSAL_DATE': 12,
        'PROPOSAL_NO': 13,
        'COMPANY_NAME': 14,
        'SERVICE': 15,
        'FEE': 16,
    }
    
    if request.method == 'POST':
        form = MultiplePDFUploadForm(request.POST, request.FILES)
        if form.is_valid():
            # Retrieve the list of uploaded PDF files
            files = request.FILES.getlist('pdf_files')
            excel_file = form.cleaned_data.get('excel_file')
            
            # 1. Initialize the AI Processor
            # Make sure GEMINI_API_KEY_2 is set in your environment variables
            api_key = os.getenv("GEMINI_API_KEY_2")
            if not api_key:
                messages.error(request, "System Error: GEMINI_API_KEY_2 is missing from environment variables.")
                return render(request, 'tools/engagement_extract.html', {'form': form})
                
            processor = ProposalPDFProcessor(api_key=api_key)

            # 2. Load the Excel Workbook
            try:
                wb = openpyxl.load_workbook(excel_file)
                # Ensure we are writing to the correct sheet, fallback to active if missing
                ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.active
            except Exception as e:
                messages.error(request, f"❌ Failed to load the uploaded Excel file: {str(e)}")
                return render(request, 'tools/engagement_extract.html', {'form': form})

            # 3. Find the exact starting row based on Column B ("No.")
            last_no = 0
            start_row = ws.max_row + 1
            
            # Scan from the bottom up to find the last valid entry number
            for r in range(ws.max_row, 1, -1):
                val = ws.cell(row=r, column=2).value
                if isinstance(val, (int, float)):
                    last_no = int(val)
                    start_row = r + 1
                    
                    break

            # If the sheet is empty or has no numbers, fallback to the requested starting point
            if last_no == 0:
                last_no = 80  
                start_row = 10 # Assuming headers are at the top, it will write on row 10

            current_row = start_row
            current_no = last_no + 1

            # 4. Loop through uploaded PDFs, extract data, and write to Excel
            success_count = 0
            total_files = len(files)
            print(f"\n{'='*50}\n🚀 STARTING BATCH PROPOSAL EXTRACTION ({total_files} files)\n{'='*50}")
            
            for i, pdf_file in enumerate(files, 1):
                try:
                    print(f"⏳ [{i}/{total_files}] Processing '{pdf_file.name}'...")
                    # Read the file directly from memory as bytes (No saving to hard drive)
                    pdf_bytes = pdf_file.read()
                    
                    # Pass bytes to the processor (returns our guaranteed dictionary)
                    data = processor.extract_proposal_data(pdf_bytes)

                    # Write data using the readable column map
                    ws.cell(row=current_row, column=COLUMN_MAP['NO']).value = current_no
                    ws.cell(row=current_row, column=COLUMN_MAP['PROPOSAL_DATE']).value = data.get('proposal_date', '')
                    extracted_proposal_no = data.get('proposal_number', '')
                    ws.cell(row=current_row, column=COLUMN_MAP['PROPOSAL_NO']).value = extracted_proposal_no
                    ws.cell(row=current_row, column=COLUMN_MAP['COMPANY_NAME']).value = data.get('company_name', '')
                    ws.cell(row=current_row, column=COLUMN_MAP['SERVICE']).value = data.get('service_proposed', '')
                    ws.cell(row=current_row, column=COLUMN_MAP['FEE']).value = data.get('fee_detail', '')

                    print(f"   ✅ Extracted: {data.get('company_name')} | No: {extracted_proposal_no}")
                    current_cost = processor.cost_stats.get('flash_cost', 0) + processor.cost_stats.get('pro_cost', 0)
                    print(f"   💲 Acc. Cost: ${current_cost:.5f}")

                    current_row += 1
                    current_no += 1
                    success_count += 1
                    
                except Exception as e:
                    # If one file fails, log it but continue processing the rest
                    print(f"   ❌ Failed to process {pdf_file.name}: {str(e)}")
                    messages.warning(request, f"⚠️ Failed to process {pdf_file.name}. Moving to next file.")

            # 5. Return the Workbook as an HTTP response
            try:
                # 6. Log AI Cost for auditing and consistency
                costs = processor.cost_stats
                total_cost = costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                
                print(f"\n🎉 BATCH COMPLETE! Successfully processed {success_count}/{total_files} files.")
                print(f"💰 Total AI Cost for this batch: ${total_cost:.5f}\n{'='*50}\n")

                if total_cost > 0:
                    AICostLog.objects.create(
                        file_name=f"{total_files} proposals batch",
                        total_pages=total_files, # Treat each PDF as a "page" for logging
                        flash_cost=costs.get('flash_cost', 0),
                        pro_cost=costs.get('pro_cost', 0),
                        total_cost=total_cost
                    )
                
                output = io.BytesIO()
                wb.save(output)
                output.seek(0)
                
                filename = f"Updated_Masterlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                response = HttpResponse(
                    output.read(), 
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                
                # Set a cookie to signal the frontend JavaScript that the file is ready
                response.set_cookie('download_complete', 'true', path='/', max_age=60)
                return response

            except Exception as e:
                messages.error(request, f"❌ Failed to generate the Excel file. Error: {str(e)}")
            
    else:
        # GET request - just render the empty form
        form = MultiplePDFUploadForm()

    return render(request, 'tools/engagement_extract.html', {'form': form})

# --------------------------------------------------------------------
# FUZZY MATCHING LOGIC
# --------------------------------------------------------------------
def normalize_company_name(name):
    if not name: return ""
    n = str(name).lower()
    n = re.sub(r'\b(co\.,?\s*ltd\.?|ltd\.?|pte\.?|inc\.?|company|limited|plc\.?|corp\.?)\b', '', n)
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def calculate_similarity(ai_name, excel_name):
    if not ai_name or not excel_name: return 0.0
    
    ai_norm = normalize_company_name(ai_name)
    ex_norm = normalize_company_name(excel_name)
    
    if not ai_norm or not ex_norm: return 0.0
    if ai_norm == ex_norm: return 1.0 
    
    if len(ai_norm) > 5 and len(ex_norm) > 5:
        if ai_norm in ex_norm or ex_norm in ai_norm:
            return 0.95 
            
    return difflib.SequenceMatcher(None, ai_norm, ex_norm).ratio()

# --------------------------------------------------------------------
# MAIN VIEW
# --------------------------------------------------------------------
@login_required
def upload_engagement_letters_view(request):
    SHEET_NAME = getattr(settings, 'PROPOSAL_EXCEL_SHEET_NAME', 'Masterlist')

    COLUMN_MAP = {
        'COMPANY_NAME_1': 6,  # Col F
        'PROPOSAL_DATE': 12,  # Col L
        'PROPOSAL_NO': 13,    # Col M 
        'COMPANY_NAME_2': 14, # Col N
        'EL_DATE': 31,        # Col AE
        'EL_NUMBER': 32,      # Col AF
        'SERVICES': 33,       # Col AG
        'FEE_INCLUSIVE': 34,  # Col AH
        'FEE_EXCLUSIVE': 35,  # Col AI
    }
    
    if request.method == 'POST':
        form = EngagementLetterUploadForm(request.POST, request.FILES)
        if form.is_valid():
            files = request.FILES.getlist('pdf_files')
            excel_file = form.cleaned_data.get('excel_file')
            
            api_key = os.getenv("GEMINI_API_KEY_2")
            if not api_key:
                messages.error(request, "System Error: GEMINI_API_KEY_2 is missing.")
                return render(request, 'tools/engagement_letter_extract.html', {'form': form})
                
            processor = EngagementLetterProcessor(api_key=api_key)

            try:
                wb = openpyxl.load_workbook(excel_file)
                ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.active
            except Exception as e:
                messages.error(request, f"❌ Failed to load Excel file: {str(e)}")
                return render(request, 'tools/engagement_letter_extract.html', {'form': form})

            success_count = 0
            total_files = len(files)
            print(f"\n{'='*60}\n🚀 STARTING BATCH EL EXTRACTION ({total_files} files)\n{'='*60}", flush=True)
            
            for i, pdf_file in enumerate(files, 1):
                try:
                    print(f"\n⏳ [{i}/{total_files}] Processing '{pdf_file.name}'...", flush=True)
                    pdf_bytes = pdf_file.read()
                    
                    data = processor.extract_el_data(pdf_bytes)
                    ai_company_name = data.get('company_name', '').strip()
                    
                    print(f"   🤖 AI Extracted Company: '{ai_company_name}'", flush=True)

                    best_match_row = None
                    best_score = 0.0
                    matched_excel_name = ""

                    if ai_company_name:
                        for r in range(5, ws.max_row + 1):
                            prop_date = str(ws.cell(row=r, column=COLUMN_MAP['PROPOSAL_DATE']).value or '').strip()
                            prop_no = str(ws.cell(row=r, column=COLUMN_MAP['PROPOSAL_NO']).value or '').strip()
                            
                            # Year Gate Filter (Only match 2026 documents)
                            if '2026' not in prop_date and '-26' not in prop_date and '2026' not in prop_no:
                                continue 

                            name_f = str(ws.cell(row=r, column=COLUMN_MAP['COMPANY_NAME_1']).value or '').strip()
                            name_n = str(ws.cell(row=r, column=COLUMN_MAP['COMPANY_NAME_2']).value or '').strip()
                            
                            score_f = calculate_similarity(ai_company_name, name_f)
                            score_n = calculate_similarity(ai_company_name, name_n)
                            
                            max_score = max(score_f, score_n)
                            
                            if max_score > best_score:
                                best_score = max_score
                                best_match_row = r
                                matched_excel_name = name_f if score_f >= score_n else name_n
                                
                            if best_score == 1.0:
                                break

                    if best_match_row and best_score >= 0.80:
                        # Write the data
                        ws.cell(row=best_match_row, column=COLUMN_MAP['EL_DATE']).value = data.get('el_date', '')
                        ws.cell(row=best_match_row, column=COLUMN_MAP['EL_NUMBER']).value = data.get('el_number', '')
                        
                        # Services and Fees (Apply wrap_text=True for perfect multiline rendering in Excel)
                        cell_services = ws.cell(row=best_match_row, column=COLUMN_MAP['SERVICES'])
                        cell_services.value = data.get('type_of_services', '')
                        cell_services.alignment = Alignment(wrap_text=True)

                        cell_fee_inc = ws.cell(row=best_match_row, column=COLUMN_MAP['FEE_INCLUSIVE'])
                        cell_fee_inc.value = data.get('total_fee_inclusive', '')
                        cell_fee_inc.alignment = Alignment(wrap_text=True)

                        cell_fee_exc = ws.cell(row=best_match_row, column=COLUMN_MAP['FEE_EXCLUSIVE'])
                        cell_fee_exc.value = data.get('total_fee_exclusive', '')
                        cell_fee_exc.alignment = Alignment(wrap_text=True)
                        
                        print(f"   ✅ MATCHED (Score: {best_score:.2f}) -> Excel Row {best_match_row}: '{matched_excel_name}'", flush=True)
                        success_count += 1
                    else:
                        print(f"   ⚠️ NO MATCH (Highest Score: {best_score:.2f} for '{matched_excel_name}')", flush=True)
                        messages.warning(request, f"⚠️ '{pdf_file.name}' extracted '{ai_company_name}' but could not find a 2026 match in the Masterlist.")

                except Exception as e:
                    print(f"   ❌ Failed to process {pdf_file.name}: {str(e)}", flush=True)
                    messages.warning(request, f"⚠️ Failed to process {pdf_file.name}.")

            try:
                costs = processor.cost_stats
                total_cost = costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                print(f"\n🎉 BATCH COMPLETE! Processed {success_count}/{total_files} files. Total Cost: ${total_cost:.5f}\n{'='*60}\n", flush=True)

                if total_cost > 0:
                    AICostLog.objects.create(
                        file_name=f"{total_files} ELs batch",
                        total_pages=total_files,
                        flash_cost=costs.get('flash_cost', 0),
                        pro_cost=costs.get('pro_cost', 0),
                        total_cost=total_cost
                    )
                
                output = io.BytesIO()
                wb.save(output)
                output.seek(0)
                
                filename = f"Masterlist_Updated_EL_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                response = HttpResponse(
                    output.read(), 
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                response.set_cookie('download_complete', 'true', path='/', max_age=60)
                return response

            except Exception as e:
                messages.error(request, f"❌ Failed to generate the Excel file: {str(e)}")
            
    else:
        form = EngagementLetterUploadForm()

    return render(request, 'tools/engagement_letter_extract.html', {'form': form})

@login_required
def monthly_closing_view(request):
    # ---------------------------------------------------------
    # 1. CLIENT SELECTION ROUTE 
    # ---------------------------------------------------------
    if request.method == 'POST' and 'client' in request.POST and 'date' not in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:monthly_closing')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    # ---------------------------------------------------------
    # 2. STATE SYNC FIX
    # ---------------------------------------------------------
    if request.method == 'POST' and 'client' in request.POST:
        submitted_client_id = request.POST.get('client')
        if submitted_client_id:
            client_id = submitted_client_id
            request.session['active_client_id'] = client_id

    # ---------------------------------------------------------
    # 3. SECURITY & PERMISSIONS
    # ---------------------------------------------------------
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Exception:
                pass
        if not has_access:
            messages.error(request, "Permission denied to manage this client.")
            request.session.pop('active_client_id', None)
            return redirect('main')

    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client to perform monthly closing.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    # ---------------------------------------------------------
    # 4. STATIC TUPLE GENERATION 
    # ---------------------------------------------------------
    db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=client_id).order_by('account_id')]
    account_choices = [('', '--- Select Account ---')] + db_accounts

    db_vendors = [(str(v.id), f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    vendor_choices = [('', '--- No Vendor ---')] + db_vendors

    form_kwargs_dict = {
        'client_id': client_id, 
        'account_choices': account_choices, 
        'vendor_choices': vendor_choices
    }

    # ---------------------------------------------------------
    # 5. MAIN PROCESSING ROUTE
    # ---------------------------------------------------------
    if request.method == 'POST':
        form = MonthlyClosingForm(request.POST, request.FILES)
        
        accrual_formset = AccrualFormSet(request.POST, prefix='accrual', form_kwargs=form_kwargs_dict)
        fx_formset = FXFormSet(request.POST, prefix='fx', form_kwargs=form_kwargs_dict)

        if form.is_valid() and accrual_formset.is_valid() and fx_formset.is_valid():
            client = form.cleaned_data['client']
            date = form.cleaned_data['date']
            
            api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
            if not api_key:
                messages.error(request, "System Error: GEMINI_API_KEY_2 is missing.")
                return render(request, 'tools/monthly_closing.html', {
                    'form': form, 'accrual_formset': accrual_formset, 'fx_formset': fx_formset
                })
            
            vendor_tax, _ = Vendor.objects.get_or_create(client=client, name='General Department of Taxation', defaults={'vendor_id': 'V-TAX'})
            vendor_staff, _ = Vendor.objects.get_or_create(client=client, name='Staff', defaults={'vendor_id': 'V-STAFF'})

            transaction_lines = [] 
            period_label = date.strftime("%b'%y")
            total_ai_cost = 0.0

            print(f"\n{'='*60}\n🚀 STARTING UNIFIED MONTHLY CLOSING PROCESS\n{'='*60}")
            
            # =========================================================
            # SCENARIO A: Unified Tax & Salary Liabilities
            # =========================================================
            if form.cleaned_data.get('tax_declaration_pdf'):
                pdf_bytes = form.cleaned_data['tax_declaration_pdf'].read()
                salary_payable_usd = form.cleaned_data.get('salary_payable') or 0.0
                staff_meals_usd = form.cleaned_data.get('staff_meals') or 0.0
                
                processor = UnifiedTaxProcessor(api_key=api_key)
                data = processor.extract_tax_data(pdf_bytes)

                if not data.get('error'):
                    total_ai_cost += processor.cost_stats['flash_cost']
                    
                    exchange_rate = data.get('exchange_rate', 0.0)
                    tos_instr = data.get('tos_instruction') or f"TOS Extracted (Rate: {exchange_rate})"
                    wht_instr = data.get('wht_instruction') or f"WHT Extracted (Rate: {exchange_rate})"
                    fbt_instr = data.get('fbt_instruction') or f"FBT Extracted (Rate: {exchange_rate})"
                    general_instr = data.get('general_instruction') or f"Salary/Meals Extracted (Rate: {exchange_rate})"

                    if staff_meals_usd == 0.0:
                        staff_meals_usd = data.get('staff_meals_usd', 0.0)

                    # 1. Process Salary & TOS (If Salary Payable was provided)
                    if salary_payable_usd > 0:
                        tax_res_usd = data.get('tos_resident_usd', 0.0)
                        tax_non_res_usd = data.get('tos_non_resident_usd', 0.0)
                        total_tos = round(tax_res_usd + tax_non_res_usd, 2)

                        transaction_lines.extend([
                            {"vendor": vendor_staff, "account_id": "705000", "instruction": general_instr, "desc": f"Salary expense {period_label}", "debit": salary_payable_usd, "credit": 0.0},
                            {"vendor": vendor_tax, "account_id": "725410", "instruction": tos_instr, "desc": f"Salary tax expense {period_label}", "debit": total_tos, "credit": 0.0},
                            {"vendor": vendor_tax, "account_id": "210030", "instruction": tos_instr, "desc": f"Salary tax expense {period_label}", "debit": 0.0, "credit": tax_res_usd},
                            {"vendor": vendor_tax, "account_id": "210030", "instruction": tos_instr, "desc": f"NR Salary tax expense {period_label}", "debit": 0.0, "credit": tax_non_res_usd},
                            {"vendor": vendor_staff, "account_id": "225070", "instruction": general_instr, "desc": f"Being accrued for salary expense {period_label}", "debit": 0.0, "credit": salary_payable_usd}
                        ])

                    if staff_meals_usd > 0:
                        transaction_lines.extend([
                            {"vendor": vendor_staff, "account_id": "705070", "instruction": general_instr, "desc": f"Staff meals {period_label}", "debit": staff_meals_usd, "credit": 0.0},
                            {"vendor": vendor_staff, "account_id": "225070", "instruction": general_instr, "desc": f"Being accrued for staff meals {period_label}", "debit": 0.0, "credit": staff_meals_usd}
                        ])

                    # 2. Process Withholding Taxes
                    total_wht = round(data.get('wht_10_usd', 0.0) + data.get('wht_15_usd', 0.0), 2)
                    if total_wht > 0:
                        desc = f"Being accrued for Withholding tax expenses in {period_label}"
                        transaction_lines.extend([
                            {"vendor": vendor_tax, "account_id": "725420", "instruction": wht_instr, "desc": desc, "debit": total_wht, "credit": 0.0},
                            {"vendor": vendor_tax, "account_id": "210040", "instruction": wht_instr, "desc": desc, "debit": 0.0, "credit": total_wht}
                        ])

                    # 3. Process Fringe Benefit Tax
                    fbt = round(data.get('fbt_usd', 0.0), 2)
                    if fbt > 0:
                        desc = f"Being accrued for Fringe Benefit tax expenses in {period_label}"
                        transaction_lines.extend([
                            {"vendor": vendor_tax, "account_id": "705010", "instruction": fbt_instr, "desc": desc, "debit": fbt, "credit": 0.0},
                            {"vendor": vendor_tax, "account_id": "210031", "instruction": fbt_instr, "desc": desc, "debit": 0.0, "credit": fbt}
                        ])

            # =========================================================
            # SCENARIO B: Accrued Expenses
            # =========================================================
            print("\n[ MODULE 3 ] Processing Manual Accruals...")
            accrual_count = 0
            for a_form in accrual_formset:
                if a_form.cleaned_data and not a_form.cleaned_data.get('DELETE', False) and a_form.cleaned_data.get('debit', 0) > 0:
                    debit_amt = round(a_form.cleaned_data['debit'], 2)
                    desc = a_form.cleaned_data['description']
                    p_status = a_form.cleaned_data.get('payment_status', 'Open')
                    
                    vendor_id_str = a_form.cleaned_data.get('vendor')
                    vendor_instance = None
                    if vendor_id_str:
                        try:
                            vendor_instance = Vendor.objects.get(id=int(vendor_id_str), client=client)
                        except (ValueError, Vendor.DoesNotExist):
                            pass
                    
                    transaction_lines.extend([
                        {"vendor": vendor_instance, "account_id": a_form.cleaned_data['account_id'], "instruction": "Manual Accrual", "desc": desc, "debit": debit_amt, "credit": 0.0, "payment_status": p_status},
                        {"vendor": vendor_instance, "account_id": "215090", "instruction": "Manual Accrual", "desc": desc, "debit": 0.0, "credit": debit_amt, "payment_status": p_status}
                    ])
                    accrual_count += 1

            # =========================================================
            # SCENARIO C: FX Gain/Loss
            # =========================================================
            print("\n[ MODULE 4 ] Processing FX Gain/Loss...")
            fx_count = 0
            for f_form in fx_formset:
                if f_form.cleaned_data and not f_form.cleaned_data.get('DELETE', False) and f_form.cleaned_data.get('exchange_rate', 0) > 0:
                    open_bal_usd = f_form.cleaned_data['openning_balance']
                    end_bal_khr = f_form.cleaned_data['ending_balance']
                    fx_rate = f_form.cleaned_data['exchange_rate']
                    desc = f_form.cleaned_data['description']
                    p_status = f_form.cleaned_data.get('payment_status', 'Paid')
                    
                    fx_account_id = f_form.cleaned_data['account_id'] 
                    bank_account_id = f_form.cleaned_data['bank_account_id']

                    month_end_usd = round(end_bal_khr / fx_rate, 2)
                    fx_diff = round(month_end_usd - open_bal_usd, 2)
                    instruction_txt = f"FX Calculation: (End Bal KHR {end_bal_khr} / Rate {fx_rate}) - Open Bal USD {open_bal_usd} = {fx_diff}"
                    
                    # Note: We pass None to "vendor" because FX adjustments are internal bank revaluations.
                    if fx_diff < 0:
                        loss_amt = abs(fx_diff)
                        transaction_lines.extend([
                            {"vendor": None, "account_id": fx_account_id, "instruction": instruction_txt, "desc": f"{desc} (FX Loss)", "debit": loss_amt, "credit": 0.0, "payment_status": p_status},
                            {"vendor": None, "account_id": bank_account_id, "instruction": instruction_txt, "desc": f"{desc} (FX Loss)", "debit": 0.0, "credit": loss_amt, "payment_status": p_status}
                        ])
                        fx_count += 1
                    elif fx_diff > 0:
                        gain_amt = fx_diff
                        transaction_lines.extend([
                            {"vendor": None, "account_id": bank_account_id, "instruction": instruction_txt, "desc": f"{desc} (FX Gain)", "debit": gain_amt, "credit": 0.0, "payment_status": p_status},
                            {"vendor": None, "account_id": fx_account_id, "instruction": instruction_txt, "desc": f"{desc} (FX Gain)", "debit": 0.0, "credit": gain_amt, "payment_status": p_status}
                        ])
                        fx_count += 1

            # =========================================================
            # ATOMIC DATABASE SAVE 
            # =========================================================
            transaction_lines = [line for line in transaction_lines if line['debit'] > 0 or line['credit'] > 0]

            if transaction_lines:
                try:
                    from django.db import transaction
                    with transaction.atomic():
                        for line in transaction_lines:
                            jv = JournalVoucher.objects.create(
                                client=client, date=date, vendor=line['vendor'], account_id=line['account_id'], instruction=line['instruction'], 
                                description=line['desc'], debit=line['debit'], credit=line['credit'], 
                                payment_status=line.get('payment_status', 'Open')
                            )
                            je = JournalEntry.objects.create(
                                client=client, date=date, description=f"Monthly Closing Automation - {period_label}"[:255], 
                                reference_number=f"JV-{jv.id}", journal_voucher=jv
                            )
                            account, _ = Account.objects.get_or_create(
                                client=client, account_id=str(line['account_id']), defaults={'name': 'System Gen Acct', 'account_type': 'Expense'}
                            )
                            JournalLine.objects.create(
                                journal_entry=je, account=account, debit=line['debit'], credit=line['credit'], description=line['desc'][:255]
                            )

                        if total_ai_cost > 0:
                            try:
                                AICostLog.objects.create(
                                    file_name=f"Monthly Closing Batch - {period_label}",
                                    total_pages=1, flash_cost=total_ai_cost, total_cost=total_ai_cost
                                )
                            except NameError:
                                pass

                    messages.success(request, f"Successfully processed {period_label}. Created {len(transaction_lines)} Journal Voucher records.")
                    return redirect('tools:monthly_closing')
                except Exception as db_error:
                    messages.error(request, f"Database Error: {str(db_error)}")
            else:
                messages.warning(request, "No valid transactions were extracted or entered.")

    else:
        # ---------------------------------------------------------
        # 6. GET REQUEST RENDERING
        # ---------------------------------------------------------
        form = MonthlyClosingForm(initial={'client': client_id})
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Exception:
                form.fields['client'].queryset = Client.objects.none()
                
        accrual_formset = AccrualFormSet(prefix='accrual', form_kwargs=form_kwargs_dict)
        fx_formset = FXFormSet(prefix='fx', form_kwargs=form_kwargs_dict)

    return render(request, 'tools/monthly_closing.html', {
        'form': form, 'accrual_formset': accrual_formset, 'fx_formset': fx_formset
    })

def load_client_vendors(request):
    """HTMX endpoint to return vendor <option> tags based on selected client."""
    client_id = request.GET.get('client')
    if client_id:
        vendors = Vendor.objects.filter(client_id=client_id).order_by('vendor_id')
    else:
        vendors = Vendor.objects.none()
        
    return render(request, 'tools/partials/vendor_options.html', {'vendors': vendors})

# ====================================================================
# --- 5. JOURNAL VOUCHER CRUD & POSTING ---
# ====================================================================

@login_required(login_url="register:login")
def JournalVoucherListView(request):
    user = request.user

    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('tools:journal_voucher_list')

    client_id = request.session.get('active_client_id')
    
    if client_id:
        base_queryset = JournalVoucher.objects.filter(client_id=client_id)
        if user.is_staff or user.is_superuser:
            jv_records = base_queryset
        else:
            try:
                profile = Profile.objects.get(user=user)
                if profile.clients.filter(id=client_id).exists():
                    jv_records = base_queryset
                else:
                    messages.error(request, "You do not have permission to view records for this client.")
                    return redirect('main')
            except Profile.DoesNotExist:
                messages.error(request, "You do not have permission to view records for this client.")
                return redirect('main')
        
        client_form = ClientSelectionForm(initial={'client': client_id})
        vendor_queryset = Vendor.objects.filter(client_id=client_id).order_by('vendor_id')
    else:
        jv_records = JournalVoucher.objects.none()
        client_form = ClientSelectionForm()
        vendor_queryset = Vendor.objects.none()
        messages.info(request, "Please select a client to view journal vouchers.")

    jv_records = jv_records.order_by('-id')
    jv_filter = JournalVoucherFilter(request.GET, queryset=jv_records)
    jv_filter.form.fields['vendor'].queryset = vendor_queryset

    paginator = Paginator(jv_filter.qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {'filter': jv_filter, 'jv_records': page_obj, 'page_obj': page_obj, 'client_form': client_form}
    return render(request, 'tools/journal_voucher_list.html', context)

@login_required(login_url="register:login")
def manual_journal_voucher_entry_view(request):
    if request.method == 'POST' and 'client' in request.POST and 'account_id' not in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client: request.session['active_client_id'] = selected_client.id
            else: request.session.pop('active_client_id', None)
            return redirect('tools:manual_journal_voucher_entry')

    client_id = request.session.get('active_client_id')
    
    if client_id:
        user = request.user
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists(): has_access = True
            except Profile.DoesNotExist: pass
        if not has_access:
            messages.error(request, "Permission denied to manage this client.")
            request.session.pop('active_client_id', None)
            return redirect('main')

    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    seen_accounts = set()
    db_accounts = []
    for acc_id, name in Account.objects.values_list('account_id', 'name'):
        if acc_id not in seen_accounts:
            seen_accounts.add(acc_id)
            db_accounts.append((str(acc_id), f"{acc_id} - {name}"))
    db_accounts.sort(key=lambda x: str(x[0]))
    account_choices = [('', '--- Select Account ---')] + db_accounts

    if request.method == 'POST':
        form = JournalVoucherEntryForm(request.POST, account_choices=account_choices)
        if form.is_valid():
            with transaction.atomic():
                jv_record = form.save()
                je = JournalEntry.objects.create(
                    client=jv_record.client, date=jv_record.date or date.today(),
                    description=f"Journal Voucher: {jv_record.description}"[:255], reference_number=f"JV-{jv_record.id}",
                    journal_voucher=jv_record
                )
                acct, _ = Account.objects.get_or_create(client_id=client_id, account_id=str(jv_record.account_id), defaults={'name': 'JV Default', 'account_type': 'Asset'})
                safe_desc = jv_record.description[:255] if jv_record.description else "Journal Voucher"
                debit_val = jv_record.debit or 0.0
                credit_val = jv_record.credit or 0.0
                if debit_val > 0 and credit_val > 0:
                    JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=0.0)
                    JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=0.0, credit=credit_val)
                else:
                    JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=credit_val)
            
            messages.success(request, f"Successfully created journal voucher and posted to GL.")
            return redirect('tools:journal_voucher_list') 
    else:
        form = JournalVoucherEntryForm(initial={'client': client_id}, account_choices=account_choices)
        form.fields['vendor'].queryset = Vendor.objects.filter(client_id=client_id)

    return render(request, 'tools/journal_voucher_form.html', {'form': form})

class JournalVoucherDetailView(LoginRequiredMixin, DetailView):
    login_url = "register:login"
    model = JournalVoucher
    template_name = 'tools/journal_voucher_detail.html'
    context_object_name = 'jv_record'

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        jv_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=jv_record.client_id).exists(): is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized:
            messages.error(request, "Permission denied.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        jv_record = self.get_object()
        context['is_owner'] = user.is_staff or user.is_superuser or (hasattr(user, 'profile') and user.profile.clients.filter(id=jv_record.client_id).exists())
        return context

class JournalVoucherUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = JournalVoucher
    form_class = JournalVoucherEntryForm 
    template_name = 'tools/journal_voucher_form.html'
    
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        jv_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=jv_record.client_id).exists(): is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized:
            messages.error(request, "Permission denied.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        db_accounts = [(a.account_id, f"{a.account_id} - {a.name}") for a in Account.objects.filter(client_id=self.object.client_id).order_by('account_id')]
        kwargs['account_choices'] = [('', '--- Select Account ---')] + db_accounts
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'form' in context:
            context['form'].fields['vendor'].queryset = Vendor.objects.filter(client_id=self.object.client_id)
        return context

    def form_valid(self, form):
        with transaction.atomic():
            jv_record = form.save()
            JournalEntry.objects.filter(Q(journal_voucher=jv_record) | Q(reference_number=f"JV-{jv_record.id}")).delete()
            
            je = JournalEntry.objects.create(
                client=jv_record.client, date=jv_record.date or date.today(),
                description=f"Updated Journal Voucher: {jv_record.description}"[:255], reference_number=f"JV-{jv_record.id}",
                journal_voucher=jv_record
            )
            acct, _ = Account.objects.get_or_create(client_id=jv_record.client_id, account_id=str(jv_record.account_id), defaults={'name': 'JV Default', 'account_type': 'Asset'})
            safe_desc = jv_record.description[:255] if jv_record.description else "Journal Voucher"
            debit_val = jv_record.debit or 0.0
            credit_val = jv_record.credit or 0.0
            if debit_val > 0 and credit_val > 0:
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=0.0)
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=0.0, credit=credit_val)
            else:
                JournalLine.objects.create(journal_entry=je, account=acct, description=safe_desc, debit=debit_val, credit=credit_val)
        messages.success(self.request, "Journal voucher and General Ledger entries updated successfully!")
        return HttpResponseRedirect(reverse('tools:journal_voucher_detail', kwargs={'pk': self.object.pk}))

class JournalVoucherDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = JournalVoucher
    template_name = 'tools/journal_voucher_confirm_delete.html'
    success_url = reverse_lazy('tools:journal_voucher_list')

    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        jv_record = self.get_object()
        is_authorized = user.is_staff or user.is_superuser
        if not is_authorized:
            try:
                if Profile.objects.get(user=user).clients.filter(id=jv_record.client_id).exists(): is_authorized = True
            except Profile.DoesNotExist: pass
        if not is_authorized:
            messages.error(request, "Permission denied.")
            return redirect('main')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        JournalEntry.objects.filter(Q(journal_voucher=self.object) | Q(reference_number=f"JV-{self.object.id}")).delete()
        messages.success(self.request, 'Journal voucher and associated Journal Entries deleted successfully!')
        return super().form_valid(form)
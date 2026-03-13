import os
import tempfile
import pandas as pd
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.contrib import messages
from django.http import HttpResponse

from .forms import BankBatchUploadForm, BankFormSet, CashBatchUploadForm, CashReviewForm, CashFormSet
from .processors import GeminiABABankProcessor, GeminiCanadiaBankProcessor, ClientBCustomBankProcessor,\
    CashStandardExcelProcessor
from .models import Bank, Cash
from tools.models import AICostLog, Client, Vendor

BANK_PROCESSOR_MAP = {
    'aba_standard': GeminiABABankProcessor,
    'canadia_standard': GeminiCanadiaBankProcessor,
    'client_b_custom': ClientBCustomBankProcessor,
}

def bank_ai_upload_view(request):
    """Upload Statement, Route via Strategy Map, Process, and Store."""
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
                messages.error(request, f"Invalid processor configuration.")
                return redirect('cash:bank_upload')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = ProcessorStrategyClass(api_key=api_key)
                extracted_data, total_pages, costs = processor.process(
                    pdf_path=tmp_pdf_path, 
                    batch_name=batch_name,
                    custom_prompt=custom_prompt
                )
                
                # --- LOG COST IMMEDIATELY ---
                AICostLog.objects.create(
                    file_name=uploaded_pdf.name, 
                    total_pages=total_pages, 
                    flash_cost=costs.get('flash_cost', 0), 
                    pro_cost=costs.get('pro_cost', 0), 
                    total_cost=costs.get('flash_cost', 0) + costs.get('pro_cost', 0)
                )
                
                request.session['extracted_bank'] = extracted_data
                request.session['bank_metadata'] = {
                    'file_name': uploaded_pdf.name,
                    'batch_name': batch_name, 
                    'client_id': selected_client.id,     # Tie processing to Client
                    'client_name': selected_client.name,
                    'config_used': dict(form.fields['processor_config'].choices).get(selected_config),
                    'total_pages': total_pages,
                    'costs': costs
                }
                return redirect('cash:bank_review')
                
            except Exception as e:
                messages.error(request, f"Bank AI Error: {str(e)}")
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BankBatchUploadForm()
    return render(request, 'bank_upload.html', {'form': form})

def bank_review_view(request):
    extracted_data = request.session.get('extracted_bank', [])
    metadata = request.session.get('bank_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('cash:bank_upload')

    if request.method == 'POST':
        formset = BankFormSet(request.POST)
        if formset.is_valid():
            saved_instances = []
            client_id = metadata.get('client_id')
            
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    instance = form.save(commit=False)
                    instance.client_id = client_id # Force attach Client ID to Bank record
                    instance.batch = metadata.get('batch_name')
                    instance.save()
                    saved_instances.append(instance)

            if saved_instances:
                report_data = list(Bank.objects.filter(id__in=[p.id for p in saved_instances]).values())
                df_report = pd.DataFrame(report_data)
                
                # Remove timezone info from datetime columns for Excel compatibility
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
            messages.success(request, f"Successfully saved {len(saved_instances)} bank transactions for {metadata.get('client_name')}!")
            return redirect('cash:bank_download') 
    else:
        formset = BankFormSet(initial=extracted_data)

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

def cash_upload_view(request):
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
            
            # Handle extensions correctly for Pandas
            _, file_ext = os.path.splitext(uploaded_file.name)
            # Respect .xls for legacy support, .csv for text, default everything else (e.g. .xlse) to .xlsx
            if file_ext.lower() == '.xls':
                ext = '.xls'
            elif file_ext.lower() == '.csv':
                ext = '.csv'
            else:
                ext = '.xlsx'

            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                for chunk in uploaded_file.chunks():
                    tmp_file.write(chunk)
                tmp_file_path = tmp_file.name

            try:
                # API Key is optional here since we are using Pandas natively
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = ProcessorStrategyClass(api_key=api_key)
                extracted_data, total_pages, costs = processor.process(
                    file_path=tmp_file_path, 
                    client_id=selected_client.id,
                    batch_name=batch_name
                )
                
                request.session['extracted_cash'] = extracted_data
                request.session['cash_metadata'] = {
                    'file_name': uploaded_file.name,
                    'batch_name': batch_name, 
                    'client_id': selected_client.id,
                    'client_name': selected_client.name,
                    'total_pages': total_pages,
                }
                return redirect('cash:cash_review')
                
            except Exception as e:
                print(f"❌ CASH PROCESSING ERROR: {str(e)}")
                messages.error(request, f"Processing Error: {str(e)}")
            finally:
                if os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)
        else:
            print(f"❌ CASH UPLOAD FORM ERRORS: {form.errors}")
    else:
        form = CashBatchUploadForm()
    return render(request, 'cash_upload.html', {'form': form})

def cash_review_view(request):
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
    
    # Pagination: Limit to 20 rows per render
    page_number = request.GET.get('page', 1)
    items_per_page = 20
    paginator = Paginator(extracted_data, items_per_page)
    page_obj = paginator.get_page(page_number)
    current_slice = page_obj.object_list
    
    start_sequence = (page_obj.number - 1) * items_per_page

    if request.method == 'POST':
        formset = CashFormSet(request.POST, form_kwargs={'dynamic_choices': dynamic_choices, 'start_sequence': start_sequence})
        
        if formset.is_valid():
            saved_instances = []
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    instance = form.save(commit=False)
                    instance.client_id = client_id
                    
                    vc = form.cleaned_data.get('vendor_choice')
                    raw_name = form.cleaned_data.get('company', 'Unknown Vendor')
                    
                    if str(vc).startswith('TEMP_'):
                        new_vid = vc.replace('TEMP_', '')
                        new_vendor, _ = Vendor.objects.get_or_create(
                            client_id=client_id, vendor_id=new_vid, defaults={'name': raw_name}
                        )
                        instance.vendor = new_vendor
                    elif vc:
                        try:
                            instance.vendor = Vendor.objects.get(id=int(vc), client_id=client_id)
                        except (ValueError, Vendor.DoesNotExist):
                            pass
                            
                    instance.save()
                    saved_instances.append(instance)

            if saved_instances:
                report_data = list(Cash.objects.filter(id__in=[p.id for p in saved_instances]).values())
                df_report = pd.DataFrame(report_data)
                media_dir = os.path.join(settings.BASE_DIR, 'media')
                os.makedirs(media_dir, exist_ok=True)
                report_path = os.path.join(media_dir, 'cash_process_report.xlsx')
                df_report.to_excel(report_path, index=False, engine='openpyxl')
                request.session['cash_report_path'] = report_path 

            # Remove the processed items from the session list
            try:
                # Use the page number from the URL to identify which slice was processed
                current_page_num = int(request.GET.get('page', 1))
            except ValueError:
                current_page_num = 1

            start_index = (current_page_num - 1) * items_per_page
            end_index = start_index + items_per_page
            
            # Delete the processed slice from the master list
            del extracted_data[start_index:end_index]
            request.session['extracted_cash'] = extracted_data
            request.session.modified = True

            if not extracted_data:
                request.session.pop('extracted_cash', None)
                request.session.pop('cash_metadata', None)
                messages.success(request, f"Success! All {len(saved_instances)} items saved. Process Complete.")
                return redirect('cash:cash_download') 
            else:
                messages.success(request, f"Saved {len(saved_instances)} items. {len(extracted_data)} remaining.")
                return redirect('cash:cash_review')
        else:
            # FIX 3: Print errors to the terminal so you know exactly why it failed!
            print("❌ FORMSET VALIDATION FAILED:")
            for i, form in enumerate(formset):
                if form.errors:
                    print(f"Row {i+1} Errors: {form.errors}")
            messages.error(request, "Validation failed. Please check the form for errors.")
            
    else:
        formset = CashFormSet(initial=current_slice, form_kwargs={'dynamic_choices': dynamic_choices, 'start_sequence': start_sequence})

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
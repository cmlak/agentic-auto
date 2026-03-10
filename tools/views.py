import os
import tempfile
import pandas as pd
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum

from .forms import BatchUploadForm, PurchaseFormSet
from .processors import GeminiInvoiceProcessor
from .models import Purchase, AICostLog, Vendor, Client

def invoice_ai_upload_view(request):
    if request.method == 'POST':
        request.session.pop('invoice_report_path', None)
        form = BatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            selected_client = form.cleaned_data['client']
            uploaded_pdf = form.cleaned_data['invoice_pdf']
            batch_name = form.cleaned_data['batch_name']
            custom_prompt = form.cleaned_data.get('ai_prompt', '')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = GeminiInvoiceProcessor(api_key=api_key)
                extracted_data, total_pages, costs = processor.process(
                    pdf_path=tmp_pdf_path, 
                    client_id=selected_client.id,
                    custom_prompt=custom_prompt, 
                    batch_name=batch_name
                )
                
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
                messages.error(request, f"AI Error: {str(e)}")
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BatchUploadForm()
    return render(request, 'invoice_upload.html', {'form': form})

def review_invoices(request):
    extracted_data = request.session.get('extracted_invoices', [])
    metadata = request.session.get('ai_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('tools:invoice_upload')
        
    client_id = metadata.get('client_id')
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.filter(client_id=client_id).order_by('vendor_id')]
    
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item.get('company', 'Unknown')} ({item['temp_vid']})"))
    
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors

    if request.method == 'POST':
        formset = PurchaseFormSet(request.POST, form_kwargs={'dynamic_choices': dynamic_choices})
        if formset.is_valid():
            saved_instances = []
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    purchase_instance = form.save(commit=False) 
                    purchase_instance.client_id = client_id # Map to client
                    
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
                            
                    purchase_instance.save()
                    saved_instances.append(purchase_instance)
            
            AICostLog.objects.create(file_name=metadata.get('file_name', 'Unknown'), total_pages=metadata.get('total_pages', 0), flash_cost=metadata.get('costs', {}).get('flash_cost', 0), pro_cost=metadata.get('costs', {}).get('pro_cost', 0), total_cost=metadata.get('costs', {}).get('flash_cost', 0) + metadata.get('costs', {}).get('pro_cost', 0))

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
            
            request.session.pop('extracted_invoices', None)
            request.session.pop('ai_metadata', None)
            messages.success(request, f"Successfully saved {len(saved_instances)} invoices for {metadata.get('client_name')}!")
            return redirect('tools:invoice_download') 
    else:
        formset = PurchaseFormSet(initial=extracted_data, form_kwargs={'dynamic_choices': dynamic_choices})
    return render(request, 'invoice_review.html', {'formset': formset, 'metadata': metadata})

def invoice_download_view(request):
    file_path = request.session.get('invoice_report_path')
    return render(request, 'invoice_download.html', {'has_file': bool(file_path and os.path.exists(file_path))})

def download_invoice_report(request):
    file_path = request.session.get('invoice_report_path')
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = 'attachment; filename="invoice_process_report.xlsx"'
            return response
    return redirect('tools:invoice_upload')

@staff_member_required
def ai_cost_dashboard(request):
    cost_logs = AICostLog.objects.all().order_by('-date')
    totals = AICostLog.objects.aggregate(total_flash=Sum('flash_cost'), total_pro=Sum('pro_cost'), grand_total=Sum('total_cost'), total_pages=Sum('total_pages'))
    return render(request, 'cost_dashboard.html', {'cost_logs': cost_logs, 'totals': totals})
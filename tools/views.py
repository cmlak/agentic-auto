import os
import tempfile
import pandas as pd
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum

# Import only the necessary forms, processors, and models
from .forms import BatchUploadForm, PurchaseFormSet
from .processors import GeminiInvoiceProcessor
from .models import Purchase, AICostLog, Vendor

# ====================================================================
# --- 1. AI INVOICE PROCESSING (UPLOAD -> REVIEW -> SAVE) ---
# ====================================================================

def invoice_ai_upload_view(request):
    """Step 1: Upload PDF, process via AI (Vendor matching done in processor), and store results."""
    formset = PurchaseFormSet()

    if request.method == 'POST':
        form = BatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_pdf = form.cleaned_data['invoice_pdf']
            file_name = uploaded_pdf.name
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                for chunk in uploaded_pdf.chunks():
                    tmp_pdf.write(chunk)
                tmp_pdf_path = tmp_pdf.name

            try:
                api_key = os.getenv("GEMINI_API_KEY_2") 
                processor = GeminiInvoiceProcessor(api_key=api_key)
                
                # The processor handles extraction AND vendor matching internally
                extracted_data, total_pages, costs = processor.process(tmp_pdf_path)
                
                # Store fully enriched data in session
                request.session['extracted_invoices'] = extracted_data
                request.session['ai_metadata'] = {
                    'file_name': file_name,
                    'total_pages': total_pages,
                    'costs': costs
                }
                
                return redirect('tools:review_invoices')
                
            except Exception as e:
                messages.error(request, f"AI Error: {str(e)}")
            finally:
                if os.path.exists(tmp_pdf_path):
                    os.remove(tmp_pdf_path)
    else:
        form = BatchUploadForm()

    report_ready = 'invoice_report_path' in request.session

    return render(request, 'invoice_upload.html', {'form': form, 'report_ready': report_ready, 'formset': formset})


def review_invoices(request):
    extracted_data = request.session.get('extracted_invoices', [])
    metadata = request.session.get('ai_metadata', {})

    if not extracted_data and request.method == 'GET':
        return redirect('tools:invoice_upload')

    # Build Dynamic Choices for the Dropdown (DB Vendors + New Candidates)
    db_vendors = [(v.id, f"{v.vendor_id} - {v.name}") for v in Vendor.objects.all().order_by('vendor_id')]
    temp_vendors = []
    for item in extracted_data:
        if item.get('is_new_vendor'):
            temp_vendors.append((item['temp_id'], f"✨ NEW: {item['company']} ({item['temp_vid']})"))
    
    # Remove duplicates from temp list
    temp_vendors = list(dict.fromkeys(temp_vendors))
    dynamic_choices = [('', '--- Select Vendor ---')] + db_vendors + temp_vendors

    if request.method == 'POST':
        # Pass the choices to the POST formset too
        formset = PurchaseFormSet(request.POST, form_kwargs={'dynamic_choices': dynamic_choices})
        
        if formset.is_valid():
            saved_instances = []
            
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get('DELETE'):
                    purchase_instance = form.save(commit=False) 
                    
                    vc = form.cleaned_data.get('vendor_choice')
                    raw_name = form.cleaned_data.get('company', 'Unknown Vendor')
                    
                    if str(vc).startswith('TEMP_'):
                        # User confirmed a temporary vendor! Create it in the DB.
                        new_vid = vc.replace('TEMP_', '')
                        # Check if we already created it in a previous row to avoid duplicates
                        new_vendor, created = Vendor.objects.get_or_create(
                            vendor_id=new_vid,
                            defaults={'name': raw_name}
                        )
                        purchase_instance.vendor = new_vendor
                    elif vc:
                        # Existing DB Vendor
                        try:
                            # Assign the full vendor object, not just the ID
                            purchase_instance.vendor = Vendor.objects.get(id=int(vc))
                        except (ValueError, Vendor.DoesNotExist):
                            # Handle cases where vc is empty or not a valid ID
                            pass
                            
                    purchase_instance.save()
                    saved_instances.append(purchase_instance)
            
            # ... (Keep your Cost Logging and Excel Generation here) ...
            
            request.session.pop('extracted_invoices', None)
            request.session.pop('ai_metadata', None)
            messages.success(request, f"Successfully saved {len(saved_instances)} invoices to the database!")
            return redirect('tools:invoice_upload') 

    else:
        formset = PurchaseFormSet(initial=extracted_data, form_kwargs={'dynamic_choices': dynamic_choices})

    return render(request, 'invoice_review.html', {
        'formset': formset,
        'metadata': metadata
    })
    
# ====================================================================
# --- 2. REPORT DOWNLOAD ---
# ====================================================================

def download_invoice_report(request):
    """Serves the generated Excel report to the user."""
    file_path = request.session.get('invoice_report_path')
    
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response['Content-Disposition'] = 'attachment; filename="invoice_process_report.xlsx"'
            return response
    else:
        messages.error(request, "The report file has expired or could not be found.")
        return redirect('tools:invoice_upload')


# ====================================================================
# --- 3. MANAGEMENT DASHBOARD ---
# ====================================================================

@staff_member_required
def ai_cost_dashboard(request):
    """
    Restricted view for authorized personnel to track Gemini API expenses.
    Requires the user to have 'is_staff = True' in the database.
    """
    cost_logs = AICostLog.objects.all().order_by('-date')
    
    totals = AICostLog.objects.aggregate(
        total_flash=Sum('flash_cost'),
        total_pro=Sum('pro_cost'),
        grand_total=Sum('total_cost'),
        total_pages=Sum('total_pages')
    )
    
    return render(request, 'cost_dashboard.html', {
        'cost_logs': cost_logs,
        'totals': totals
    })
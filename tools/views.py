import os
import re
import difflib
import pandas as pd
from datetime import datetime

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse

from .forms import VendorTaxUploadForm
from .models import Purchase

# ====================================================================
# --- HELPER FUNCTIONS ---
# ====================================================================

def normalize_name(name):
    """Normalizes vendor names for accurate matching."""
    if pd.isna(name):
        return ""
    name_str = str(name).lower().replace('&', ' and ')
    return re.sub(r'[\W_]+', ' ', name_str).strip()

# ====================================================================
# --- MAIN UPLOAD & PROCESS VIEW ---
# ====================================================================

def process_vendor_tax_upload(request):
    if request.method == 'POST':
        form = VendorTaxUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # 1. Read uploaded files directly into pandas dataframes
                vendor_file = request.FILES['vendor_file']
                tax_file = request.FILES['tax_file']
                
                df_vendor = pd.read_csv(vendor_file)
                df_tax = pd.read_csv(tax_file)

                # 2. Determine Next Vendor ID Sequence
                max_id = 0
                for vid in df_vendor['id'].dropna():
                    match = re.match(r'V(\d+)', str(vid))
                    if match:
                        max_id = max(max_id, int(match.group(1)))
                
                current_id_seq = max_id + 1
                current_no_seq = 1
                if 'no' in df_vendor.columns:
                    max_no = pd.to_numeric(df_vendor['no'], errors='coerce').max()
                    if pd.notna(max_no):
                        current_no_seq = int(max_no) + 1

                # 3. Create Lookup for Existing Vendors
                existing_vendors_normalized = set(df_vendor['name'].apply(normalize_name))
                new_vendors = []

                # 4. Iterate Tax Data to Find New Vendors
                if 'name' in df_tax.columns and 'local_purchase_vat_usd' in df_tax.columns:
                    for index, row in df_tax.iterrows():
                        vat_val = pd.to_numeric(row.get('local_purchase_vat_usd', 0), errors='coerce')
                        vat_val = 0.0 if pd.isna(vat_val) else float(vat_val)
                        
                        raw_name = str(row['name']).strip()
                        name_normalized = normalize_name(raw_name)

                        # If VAT is not 0 AND Name is valid AND Name not in existing list
                        if vat_val != 0 and raw_name.lower() != 'nan' and name_normalized and name_normalized not in existing_vendors_normalized:
                            new_id = f"V{current_id_seq:03d}"
                            new_vendors.append({'no': current_no_seq, 'id': new_id, 'name': raw_name})
                            existing_vendors_normalized.add(name_normalized)
                            current_id_seq += 1
                            current_no_seq += 1

                # 5. Combine Existing and New Vendors
                if new_vendors:
                    df_new = pd.DataFrame(new_vendors)
                    df_final = pd.concat([df_vendor, df_new], ignore_index=True)
                    messages.success(request, f"Added {len(new_vendors)} new vendors to the system.")
                else:
                    df_final = df_vendor
                    messages.info(request, "No new vendors met the criteria.")

                # 6. Map IDs to Tax Data
                vendor_lookup = {normalize_name(row['name']): str(row['id']) for _, row in df_final.iterrows() if pd.notna(row['name'])}
                
                def get_matched_vendor_id(raw_name):
                    target = normalize_name(raw_name)
                    if not target: return 'V005'
                    
                    # Exact Match
                    if target in vendor_lookup: return vendor_lookup[target]
                    
                    # Fuzzy Match
                    best_id, best_coverage = 'V005', 0.0
                    for v_name, v_id in vendor_lookup.items():
                        if not v_name or v_name[0] != target[0]: continue
                        matcher = difflib.SequenceMatcher(None, target, v_name)
                        match = matcher.find_longest_match(0, len(target), 0, len(v_name))
                        if match.a == 0 and match.b == 0:
                            coverage = match.size / len(target)
                            if coverage >= 0.6 and coverage > best_coverage:
                                best_coverage = coverage
                                best_id = v_id
                    return best_id

                df_tax['vendor_id'] = df_tax['name'].apply(get_matched_vendor_id)

                # 7. Save mapped tax records to Django Database
                purchase_objects = []
                for _, row in df_tax.iterrows():
                    # Handle date safely with a fallback
                    try:
                        parsed_date = pd.to_datetime(row.get('date')).date()
                    except:
                        parsed_date = datetime.now().date() 

                    purchase = Purchase(
                        date=parsed_date,
                        invoice_no=str(row.get('invoice_no', '')),
                        vattin=str(row.get('vattin', '')),
                        vendor_id=row.get('vendor_id', ''),
                        account_id=row.get('account_id') if pd.notna(row.get('account_id')) else None,
                        description=str(row.get('description', '')),
                        non_vat_non_tax_payer_usd=row.get('non_vat_non_tax_payer_usd') if pd.notna(row.get('non_vat_non_tax_payer_usd')) else None,
                        non_vat_tax_payer_usd=row.get('non_vat_tax_payer_usd') if pd.notna(row.get('non_vat_tax_payer_usd')) else None,
                        local_purchase_usd=row.get('local_purchase_usd') if pd.notna(row.get('local_purchase_usd')) else None,
                        local_purchase_vat_usd=row.get('local_purchase_vat_usd') if pd.notna(row.get('local_purchase_vat_usd')) else None,
                        total_usd=row.get('total_usd') if pd.notna(row.get('total_usd')) else None,
                        page=row.get('page') if pd.notna(row.get('page')) else None,
                    )
                    purchase_objects.append(purchase)
                
                # Save individually to trigger the custom save() method (Excel formatting protection)
                for p in purchase_objects:
                    p.save()

                # 8. Save updated vendor file temporarily for user download
                media_dir = os.path.join(settings.BASE_DIR, 'media')
                os.makedirs(media_dir, exist_ok=True)
                output_file_path = os.path.join(media_dir, 'vendor_updated.csv')
                
                # Save to CSV with BOM for Excel compatibility
                df_final.to_csv(output_file_path, index=False, encoding='utf-8-sig')
                
                # Store the file path in the user's session
                request.session['vendor_file_path'] = output_file_path

                messages.success(request, f"Successfully processed and saved {len(purchase_objects)} purchase records to the database.")
                return redirect('tools:upload_success') 

            except Exception as e:
                messages.error(request, f"An error occurred while processing the files: {str(e)}")
    else:
        form = VendorTaxUploadForm()

    return render(request, 'upload.html', {'form': form})

# ====================================================================
# --- SUCCESS & DOWNLOAD VIEWS ---
# ====================================================================

def upload_success(request):
    """Renders the success page and checks if the download file is available."""
    has_file = 'vendor_file_path' in request.session
    return render(request, 'success.html', {'has_file': has_file})

def download_vendor_csv(request):
    """Serves the updated vendor CSV file to the user."""
    file_path = request.session.get('vendor_file_path')
    
    if file_path and os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="text/csv")
            response['Content-Disposition'] = 'attachment; filename="vendor_updated.csv"'
            return response
    else:
        messages.error(request, "The download file has expired or could not be found.")
        return redirect('tools:upload_tax_vendor')
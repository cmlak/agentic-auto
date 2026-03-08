import pandas as pd
import os
import re
import difflib

# ====================================================================
# --- CONFIGURATION ---
# ====================================================================
# Update these paths to match your local environment
VENDOR_BASE_DIR = r'C:\Users\cmlak\OneDrive - Baker Tilly (Cambodia) Co., Ltd\07. Project Development\bakertilly\Allfine\Data\2025\December 2025' 
TAX_BASE_DIR = r'C:\Users\cmlak\OneDrive - Baker Tilly (Cambodia) Co., Ltd\07. Project Development\bakertilly\Allfine\Data\2025\December 2025' 

RAW_TAX_FILE = os.path.join(TAX_BASE_DIR, 'allfine_tax_dec_clean_v2.csv')
VENDOR_FILE = os.path.join(VENDOR_BASE_DIR, 'allfine_vendor_dec_v1.csv')
OUTPUT_VENDOR_UPDATE = os.path.join(VENDOR_BASE_DIR, 'allfine_vendor_dec_v1_updated.csv')
OUTPUT_TAX_VENDOR = os.path.join(TAX_BASE_DIR, 'allfine_tax_vendor_step_1_dec.csv')
# ====================================================================

def update_vendor_list():
    print("--- STARTING VENDOR UPDATE ---")
    
    # 1. Load Files
    try:
        print(f"Reading Vendor File: {VENDOR_FILE}")
        if not os.path.exists(VENDOR_FILE):
            print(f"❌ ERROR: Vendor file not found.")
            print(f"   Looking in: {VENDOR_BASE_DIR}")
            print(f"   👉 ACTION: Copy 'allfine_vendor_nov_v1_updated.csv' from the November folder, paste it here, and rename it to 'allfine_vendor_dec_v1.csv'.")
            return

        df_vendor = pd.read_csv(VENDOR_FILE)
        print(f"Reading Tax File: {RAW_TAX_FILE}")
        if not os.path.exists(RAW_TAX_FILE):
            print(f"❌ ERROR: Tax file not found: {RAW_TAX_FILE}")
            return
        df_tax = pd.read_csv(RAW_TAX_FILE)
    except Exception as e:
        print(f"CRITICAL ERROR: Could not read input files. {e}")
        return

    # 2. Determine Next Vendor ID Sequence
    max_id = 0
    # Loop through existing IDs (e.g., 'V001', 'V049') to find the highest number
    for vid in df_vendor['id']:
        # Extract number using regex (looks for 'V' followed by digits)
        match = re.match(r'V(\d+)', str(vid))
        if match:
            num = int(match.group(1))
            if num > max_id:
                max_id = num
    
    current_id_seq = max_id + 1
    print(f"Max existing ID found: V{max_id:03d}. Next ID will start at: V{current_id_seq:03d}")

    # Determine Next 'no' Sequence
    current_no_seq = 1
    if 'no' in df_vendor.columns:
        # Convert to numeric, coerce errors to NaN, find max
        max_no = pd.to_numeric(df_vendor['no'], errors='coerce').max()
        if pd.notna(max_no):
            current_no_seq = int(max_no) + 1

    # Helper function to normalize names (ignore punctuation like '.', ',')
    def normalize_name(name):
        if pd.isna(name):
            return ""
        # Normalize '&' to 'and' to handle variations like "Company & Co" vs "Company and Co"
        name_str = str(name).lower().replace('&', ' and ')
        # Lowercase, replace non-alphanumeric (including underscore) with space, strip
        return re.sub(r'[\W_]+', ' ', name_str).strip()

    # 3. Create Lookup for Existing Vendors
    # We use a set for faster lookup, using normalized names
    existing_vendors_normalized = set(df_vendor['name'].apply(normalize_name))
    
    new_vendors = []
    
    # 4. Iterate Tax Data to Find New Vendors
    if 'name' in df_tax.columns and 'local_purchase_vat_usd' in df_tax.columns:
        for index, row in df_tax.iterrows():
            # Extract VAT Amount safely
            try:
                vat_val = float(row['local_purchase_vat_usd'])
                if pd.isna(vat_val):
                    vat_val = 0.0
            except:
                vat_val = 0.0
            
            raw_name = str(row['name']).strip()
            name_normalized = normalize_name(raw_name)
            
            # Logic: If VAT is not 0 AND Name is valid AND Name not in existing list
            if vat_val != 0:
                if raw_name and raw_name.lower() != 'nan' and name_normalized and name_normalized not in existing_vendors_normalized:
                    # Create New Vendor ID
                    new_id = f"V{current_id_seq:03d}"
                    
                    # Add to new list
                    new_vendors.append({'no': current_no_seq, 'id': new_id, 'name': raw_name})
                    
                    # Add to lookup set so we don't add duplicates within this same run
                    existing_vendors_normalized.add(name_normalized)
                    
                    # Increment Sequence
                    current_id_seq += 1
                    current_no_seq += 1
                    print(f"New Vendor Found: {new_id} - {raw_name}")

    # 5. Save Updated File
    if new_vendors:
        df_new = pd.DataFrame(new_vendors)
        df_final = pd.concat([df_vendor, df_new], ignore_index=True)
        print(f"SUCCESS: Added {len(new_vendors)} new vendors.")
    else:
        df_final = df_vendor
        print("No new vendors met the criteria.")

    try:
        df_final.to_csv(OUTPUT_VENDOR_UPDATE, index=False, encoding='utf-8-sig')
        print(f"File saved to: {OUTPUT_VENDOR_UPDATE}")
    except Exception as e:
        print(f"ERROR: Could not save output file. {e}")

    # 6. Create Tax File with Vendor IDs
    print("--- CREATING TAX FILE WITH VENDOR IDs ---")
    
    # Build lookup dictionary from the updated vendor list
    vendor_lookup = {normalize_name(row['name']): str(row['id']) for _, row in df_final.iterrows() if pd.notna(row['name'])}
            
    def get_matched_vendor_id(raw_name):
        target = normalize_name(raw_name)
        if not target:
            return 'V005'
            
        # 1. Exact Match
        if target in vendor_lookup:
            return vendor_lookup[target]
            
        # 2. Fuzzy Match (Start index 0, >= 60% coverage of Tax Name)
        best_id = 'V005'
        best_coverage = 0.0
        
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

    # Map IDs to Tax Data
    df_tax['vendor_id'] = df_tax['name'].apply(get_matched_vendor_id)
    
    # Reorder columns to place 'vendor_id' after 'name' for better visibility
    if 'name' in df_tax.columns:
        cols = list(df_tax.columns)
        cols.remove('vendor_id')
        name_idx = cols.index('name')
        cols.insert(name_idx + 1, 'vendor_id')
        df_tax = df_tax[cols]

    try:
        df_tax.to_csv(OUTPUT_TAX_VENDOR, index=False, encoding='utf-8-sig')
        print(f"SUCCESS: Tax file with Vendor IDs saved to: {OUTPUT_TAX_VENDOR}")
    except Exception as e:
        print(f"ERROR: Could not save tax vendor file. {e}")

if __name__ == "__main__":
    update_vendor_list()
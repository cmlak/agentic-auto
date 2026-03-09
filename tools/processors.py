import os
import time
import threading
import pdfplumber
import re
import difflib
from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Vendor 

# ====================================================================
# --- 1. PYDANTIC DATA MODELS (STRICT COMPLIANCE SCHEMA) ---
# ====================================================================

class RoutingDecision(BaseModel):
    page_type: Literal["invoice", "bank_slip", "empty"]
    complexity: Literal["low", "medium", "high"]

class BankEntry(BaseModel):
    date: str = Field("Unknown")
    bank: str = Field(..., description="Issuing Bank name (e.g., ABA).")
    ref_id: str = Field(..., description="Unique Bank Reference ID.")
    description: str
    money_in: float = Field(0.0)
    money_out: float = Field(0.0)
    reported_balance: float = Field(..., description="Balance shown on the bank document.")
    matched_invoice_no: str = Field("N/A", description="Linked invoice ID if payment found.")
    page: int

    @model_validator(mode='after')
    def fix_ids(self):
        if 'E+' in str(self.ref_id).upper():
            try: self.ref_id = str(int(float(self.ref_id)))
            except: pass
        return self

class PurchaseEntry(BaseModel):
    date: Optional[str] = Field(None, description="Date of the invoice (YYYY-MM-DD).")
    invoice_no: str = Field("Unknown")
    vattin: str = Field("N/A", description="VAT Registration Number (TIN).")
    vendor_name: str = Field(..., description="Vendor name. If in Khmer or Chinese, translate to English.")
    description: str = Field(..., description="Summary description. If in Khmer or Chinese, translate to English. Use English only. Max 20 words.")
    # Compliance Tiering
    non_vat_non_tax_payer_usd: float = 0.0  
    non_vat_tax_payer_usd: float = 0.0      
    local_purchase_usd: float = 0.0         
    local_purchase_vat_usd: float = 0.0     
    total_usd: float
    page: int

    @model_validator(mode='after')
    def validate_tax_integrity(self):
        if 'E+' in str(self.invoice_no).upper():
            try: self.invoice_no = str(int(float(self.invoice_no)))
            except: pass
        
        # Ensure date is None if Unknown to prevent rendering issues
        if self.date and str(self.date).lower() in ["unknown", "n/a", "none"]:
            self.date = None
        return self

class AccountingBatch(BaseModel):
    # This forces the AI to think before outputting JSON arrays, preventing hallucinations.
    self_verification_step: str = Field(
        ..., 
        description="Write a short summary verifying: 1. Aggregation logic. 2. Vendor Name location. 3. Date verification. 4. Description length. 5. VENDOR REASONING: Explicitly state the VATTIN found and VAT amount to justify if this is a valid tax vendor."
    )
    bank_entries: List[BankEntry] = []
    purchase_entries: List[PurchaseEntry] = []

# ====================================================================
# --- 2. MAIN PROCESSOR AGENT ---
# ====================================================================

class GeminiInvoiceProcessor:
    def __init__(self, api_key):
        print("\n" + "="*50)
        print("🚀 INITIALIZING GEMINI AI PROCESSOR")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.TRIAGE_MODEL = "gemini-3-flash-preview"
        self.AUDIT_MODEL = "gemini-3.1-pro-preview"
        self.cost_lock = threading.Lock()
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}
        
        # NEW: Batch-level vendor tracking to prevent duplicates and ensure sequential IDs
        self.vendor_lock = threading.Lock()
        self.batch_new_vendors = {} 
        
        # Ensure V001 (General Vendor) always exists in DB
        Vendor.objects.get_or_create(
            vendor_id='V001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )

    def calculate_cost(self, usage, model_id):
        rates = {"gemini-3.1-pro-preview": {"in": 1.25, "out": 10.00}, "gemini-3-flash-preview": {"in": 0.10, "out": 0.40}}
        r = rates.get(model_id, {"in": 0.10, "out": 0.40})
        if usage: return ((usage.prompt_token_count / 1e6) * r["in"]) + ((usage.candidates_token_count / 1e6) * r["out"])
        return 0.0

    def resolve_and_assign_vendor(self, raw_name, vattin, vat_amount):
        """Maps vendor. Returns a dict with DB ID or Temporary ID logic."""
        general_vendor = Vendor.objects.get(vendor_id='V001')
        
        # RULE: Strict Tax Payer Check
        # Must have VATTIN AND VAT Amount > 0 to be a "New Vendor Candidate"
        # Otherwise, it falls back to General Vendor (V001)
        has_tax_info = (vattin and vattin != 'N/A' and str(vattin).strip() != '')
        has_vat_value = (vat_amount is not None and float(vat_amount) > 0)
        
        if not (has_tax_info and has_vat_value):
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        if not raw_name or str(raw_name).lower() == 'nan' or raw_name == 'Unknown':
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        # 1. Exact Match
        exact_match = Vendor.objects.filter(normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        # 2. Fuzzy Match
        best_vendor, best_coverage = None, 0.0
        for v in Vendor.objects.all():
            if not v.normalized_name or v.normalized_name[0] != target_norm[0]: continue
            matcher = difflib.SequenceMatcher(None, target_norm, v.normalized_name)
            match = matcher.find_longest_match(0, len(target_norm), 0, len(v.normalized_name))
            if match.a == 0 and match.b == 0:
                coverage = match.size / len(target_norm)
                if coverage >= 0.6 and coverage > best_coverage:
                    best_coverage = coverage
                    best_vendor = v

        if best_vendor:
            return {'db_id': best_vendor.id, 'is_new': False, 'temp_vid': None}

        # 3. Batch Cache & New ID Generation (Thread-safe)
        with self.vendor_lock: 
            # Check if we already assigned a temp ID to this vendor in this batch
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

            last_vendor = Vendor.objects.order_by('-id').first()
            next_num = 2 # Start at 2 since V001 is General
            if last_vendor and re.search(r'V(\d+)', last_vendor.vendor_id):
                next_num = int(re.search(r'V(\d+)', last_vendor.vendor_id).group(1)) + 1
            
            # Add offset based on how many new vendors we've found in this batch so far
            current_seq = next_num + len(self.batch_new_vendors)
            
            new_vid = f"V{current_seq:03d}" # Fixed format to 3 digits (V002)
            
            vendor_data = {'db_id': None, 'is_new': True, 'temp_vid': new_vid, 'temp_id': f"TEMP_{new_vid}"}
            self.batch_new_vendors[target_norm] = vendor_data
            
            return vendor_data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def process_page(self, file_obj, pg_num):
        try:
            print(f"📄 [Page {pg_num}] Starting Triage...")
            t_resp = self.client.models.generate_content(
                model=self.TRIAGE_MODEL,
                contents=[file_obj, f"Analyze Page {pg_num}. Is it an invoice or bank slip?"],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=RoutingDecision
                )
            )
            with self.cost_lock: self.cost_stats["flash_cost"] += self.calculate_cost(t_resp.usage_metadata, self.TRIAGE_MODEL)
            if t_resp.parsed.page_type == "empty": return None

            print(f"🧠 [Page {pg_num}] Audit starting...")
            
            prompt = f"""
            TASK: Extract accounting data strictly from Page {pg_num}.
            
            CRITICAL INSTRUCTIONS:
            1. AGGREGATION (CRUCIAL): Output exactly ONE PurchaseEntry for this entire page. If a receipt has 10 line items, COMBINE them into a single summary description and SUM the total amount. Do NOT split a single receipt into multiple rows.
            2. DATE: Look very closely for handwritten dates, stamps, or Khmer dates (e.g., ថ្ងៃទី... ខែ... ឆ្នាំ...). Format strictly as YYYY-MM-DD. If completely missing, return null.
            3. VENDOR NAME: Extract the company/shop name. If it is in Khmer or Chinese, TRANSLATE it to English.
            4. DESCRIPTION: Provide a summary description of the items. If in Khmer or Chinese, TRANSLATE it to English. Use English only. Keep it short: Maximum 15 words total!
            5. TAX RULES: If there is no VAT TIN on the invoice, put the total amount in 'non_vat_non_tax_payer_usd'.
            6. VENDOR REASONING: In 'self_verification_step', explicitly explain your reasoning for the Vendor. Did you find a VATTIN? Is there a 10% VAT amount? If yes, this is a genuine new vendor. If no VATTIN, explain that it is a general vendor.
            
            DOUBLE-CHECK PROTOCOL:
            You must fill out the 'self_verification_step' string FIRST. State how you aggregated the items, found the vendor name, verified the date, and EXPLAIN YOUR REASONING for the vendor's tax status (VATTIN/VAT present?) before outputting the purchase_entries list.
            """
            
            a_resp = self.client.models.generate_content(
                model=self.AUDIT_MODEL,
                contents=[file_obj, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AccountingBatch,
                    thinking_config=types.ThinkingConfig(thinking_level=t_resp.parsed.complexity.upper())
                )
            )
            with self.cost_lock: self.cost_stats["pro_cost"] += self.calculate_cost(a_resp.usage_metadata, self.AUDIT_MODEL)
            
            print(f"🕵️ [Page {pg_num}] AI Check: {a_resp.parsed.self_verification_step}")
            return a_resp.parsed, pg_num
            
        except Exception as e:
            print(f"❌ [Page {pg_num}] ERROR: {str(e)}")
            raise e 

    def process(self, pdf_path):
        f = self.client.files.upload(file=pdf_path)
        while f.state.name == "PROCESSING": 
            time.sleep(2)
            f = self.client.files.get(name=f.name)

        ledgers = []
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
            # SEQUENTIAL PROCESSING (Page 1 -> Page N)
            for i in range(total_pages):
                pg_num = i + 1
                try:
                    res = self.process_page(f, pg_num)
                    if res:
                        audit, pg = res
                        for entry in audit.purchase_entries:
                            entry_dict = entry.model_dump()

                            # RENAME 'vendor_name' to 'company' to match the Django Model field
                            if 'vendor_name' in entry_dict:
                                entry_dict['company'] = entry_dict.pop('vendor_name')
                            
                            # Vendor Assignment Logic
                            raw_name = entry_dict.get('company', '')
                            vattin = entry_dict.get('vattin', '')
                            vat_amount = entry_dict.get('local_purchase_vat_usd', 0.0)
                            vendor_data = self.resolve_and_assign_vendor(raw_name, vattin, vat_amount)
                            
                            entry_dict['vendor_db_id'] = vendor_data['db_id']
                            entry_dict['is_new_vendor'] = vendor_data['is_new']
                            entry_dict['temp_vid'] = vendor_data['temp_vid']
                            entry_dict['temp_id'] = vendor_data.get('temp_id')
                            
                            # Set the UI choice field value
                            entry_dict['vendor_choice'] = vendor_data['temp_id'] if vendor_data['is_new'] else vendor_data['db_id']
                            
                            ledgers.append(entry_dict)
                except Exception as e:
                    print(f"❌ Skipping Page {pg_num} due to error: {e}")

        return ledgers, total_pages, self.cost_stats
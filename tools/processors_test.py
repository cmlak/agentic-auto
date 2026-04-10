import os
import time
import threading
import re
import difflib
import pdfplumber
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Vendor, Client 

# ====================================================================
# --- PYDANTIC MODELS: INVOICES ---
# ====================================================================

class RoutingDecision(BaseModel):
    page_type: Literal["invoice", "bank_slip", "empty"]
    complexity: Literal["low", "medium", "high"]

class PurchaseEntry(BaseModel):
    date: Optional[str] = Field(None, description="Date of the invoice (YYYY-MM-DD).")
    invoice_no: str = Field("Unknown")
    vattin: str = Field("N/A", description="VAT Registration Number (TIN).")
    vendor_name: str = Field(..., description="Vendor name. If in Khmer or Chinese, translate to English.")
    description: str = Field(..., description="Detailed description of the items in the original language.")
    description_en: str = Field(..., description="Summarize the detailed description in English ONLY. Maximum 25 words.")
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
        if self.date and str(self.date).lower() in ["unknown", "n/a", "none"]:
            self.date = None
        return self

class AccountingBatch(BaseModel):
    self_verification_step: str = Field(
        ..., 
        description="Write a short summary verifying: 1. Aggregation logic. 2. Vendor Name location. 3. Date verification. 4. Description length. 5. Any User Custom Instructions applied. 6. VENDOR REASONING: Explicitly state the VATTIN found and VAT amount to justify tax status."
    )
    purchase_entries: List[PurchaseEntry] = []

# ====================================================================
# --- MAIN INVOICE PROCESSOR ---
# ====================================================================

class GeminiInvoiceProcessor:
    def __init__(self, api_key):
        print("\n" + "="*50)
        print("🚀 INITIALIZING GEMINI INVOICE PROCESSOR")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.TRIAGE_MODEL = "gemini-3-flash-preview"
        self.AUDIT_MODEL = "gemini-3.1-pro-preview"
        self.cost_lock = threading.Lock()
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}
        
        self.vendor_lock = threading.Lock()
        self.batch_new_vendors = {} 

    def calculate_cost(self, usage, model_id):
        rates = {"gemini-3.1-pro-preview": {"in": 1.25, "out": 10.00}, "gemini-3-flash-preview": {"in": 0.10, "out": 0.40}}
        r = rates.get(model_id, {"in": 0.10, "out": 0.40})
        if usage: return ((usage.prompt_token_count / 1e6) * r["in"]) + ((usage.candidates_token_count / 1e6) * r["out"])
        return 0.0

    def resolve_and_assign_vendor(self, raw_name, vattin, vat_amount, client_id):
        """Maps vendor strictly within the selected client's isolated database."""
        # Ensure V001 exists for THIS specific client
        general_vendor, _ = Vendor.objects.get_or_create(
            client_id=client_id,
            vendor_id='V001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )
        
        if not raw_name or str(raw_name).strip().lower() in ['unknown', 'n/a', 'none', '']:
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        # 1. Exact Match (Scoped to Client)
        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        # 2. Fuzzy Match (Scoped to Client)
        best_vendor, best_coverage = None, 0.0
        for v in Vendor.objects.filter(client_id=client_id):
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

        # 3. Restrict creating new vendors: only if they are a registered tax payer
        has_tax_info = (vattin and vattin != 'N/A' and str(vattin).strip() != '')
        has_vat_value = (vat_amount is not None and float(vat_amount) > 0)
        if not (has_tax_info and has_vat_value):
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        # 3. New Vendor Cache (Scoped to Client)
        with self.vendor_lock: 
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

            last_vendor = Vendor.objects.filter(client_id=client_id).order_by('-id').first()
            next_num = 2
            if last_vendor and re.search(r'V(\d+)', last_vendor.vendor_id):
                next_num = int(re.search(r'V(\d+)', last_vendor.vendor_id).group(1)) + 1
            
            current_seq = next_num + len(self.batch_new_vendors)
            new_vid = f"V{current_seq:03d}"
            
            vendor_data = {'db_id': None, 'is_new': True, 'temp_vid': new_vid, 'temp_id': f"TEMP_{new_vid}"}
            self.batch_new_vendors[target_norm] = vendor_data
            return vendor_data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def process_page(self, file_obj, pg_num, custom_prompt=""):
        try:
            print(f"📄 [Page {pg_num}] Starting Triage...")
            t_resp = self.client.models.generate_content(
                model=self.TRIAGE_MODEL,
                contents=[file_obj, f"Analyze Page {pg_num}. Is it an invoice or bank slip?"],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=RoutingDecision)
            )
            with self.cost_lock: self.cost_stats["flash_cost"] += self.calculate_cost(t_resp.usage_metadata, self.TRIAGE_MODEL)
            if t_resp.parsed.page_type == "empty": return None

            print(f"🧠 [Page {pg_num}] Audit starting...")
            prompt = f"""
            TASK: Extract accounting data strictly from Page {pg_num}.
            CRITICAL INSTRUCTIONS:
            1. AGGREGATION: Output exactly ONE PurchaseEntry per page. COMBINE items.
            2. DATE: Format strictly as YYYY-MM-DD. Return null if missing.
            3. VENDOR NAME: Extract the company/shop name.
            4. DESCRIPTION: Original language detail.
            5. DESCRIPTION_EN: Summarize in English ONLY. Max 25 words!
            6. TAX RULES: If no VAT TIN, put amount in 'non_vat_non_tax_payer_usd'.
            """
            if custom_prompt:
                prompt += f"\n7. USER CUSTOM INSTRUCTION:\n{custom_prompt}\n"
            prompt += "\nDOUBLE-CHECK PROTOCOL: Fill out 'self_verification_step' first."
            
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

    def process(self, pdf_path, client_id, custom_prompt="", batch_name=""):
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            if total_pages > 20:
                raise ValueError(f"Limit exceeded. PDF has {total_pages} pages, max is 20.")

        f = self.client.files.upload(file=pdf_path)
        while f.state.name == "PROCESSING": 
            time.sleep(2)
            f = self.client.files.get(name=f.name)

        ledgers = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.process_page, f, i+1, custom_prompt): i+1 for i in range(total_pages)}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    audit, pg = res
                    for entry in audit.purchase_entries:
                        entry_dict = entry.model_dump()
                        if 'vendor_name' in entry_dict:
                            entry_dict['company'] = entry_dict.pop('vendor_name')
                        
                        raw_name = entry_dict.get('company', '')
                        vattin = entry_dict.get('vattin', '')
                        vat_amount = entry_dict.get('local_purchase_vat_usd', 0.0)
                        
                        vendor_data = self.resolve_and_assign_vendor(raw_name, vattin, vat_amount, client_id)
                        
                        entry_dict['vendor_db_id'] = vendor_data['db_id']
                        entry_dict['is_new_vendor'] = vendor_data['is_new']
                        entry_dict['temp_vid'] = vendor_data['temp_vid']
                        entry_dict['temp_id'] = vendor_data.get('temp_id')
                        entry_dict['vendor_choice'] = vendor_data['temp_id'] if vendor_data['is_new'] else vendor_data['db_id']
                        entry_dict['batch'] = batch_name
                        entry_dict['instruction'] = ""
                        
                        ledgers.append(entry_dict)

        return ledgers, total_pages, self.cost_stats
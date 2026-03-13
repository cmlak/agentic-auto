import os
import time
import threading
import re
import difflib
import pdfplumber
from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Vendor, Client 

class RoutingDecision(BaseModel):
    page_type: Literal["invoice", "bank_slip", "empty"]
    complexity: Literal["low", "medium", "high"]

class PurchaseEntry(BaseModel):
    date: Optional[str] = Field(None, description="Date of the invoice (YYYY-MM-DD).")
    invoice_no: str = Field("Unknown")
    
    # ENHANCED: Strict instruction against amending VATTIN
    vattin: str = Field("N/A", description="VAT Registration Number. CRITICAL: Extract EXACTLY as printed. Do NOT standardize, autocorrect, or apply patterns (e.g., keep 'B107' as is, do not change to 'L001').")
    
    vendor_name: str = Field(..., description="Vendor name. If in Khmer or Chinese, translate to English.")
    description: str = Field(..., description="Detailed description of the items in the original language.")
    description_en: str = Field(..., description="Summarize the detailed description in English ONLY. Maximum 25 words.")
    
    # ENHANCED: Full 5-Leg Account Assignment Fields
    account_id: Optional[int] = Field(None, description="Main Debit Account ID (e.g., Expense or Asset).")
    vat_account_id: Optional[int] = Field(None, description="Debit Account ID for VAT Input (e.g., 115010). Leave null if no VAT.")
    wht_debit_account_id: Optional[int] = Field(None, description="Debit Account ID for WHT Expense (e.g., 725420) if company bears the tax. Leave null if no WHT.")
    credit_account_id: int = Field(200000, description="Main Credit Account ID (Default: 200000 for Trade Payable).")
    wht_account_id: Optional[int] = Field(None, description="Credit Account ID for WHT Payable (e.g., 210040). Leave null if no WHT.")
    
    account_reasoning: str = Field("", description="Brief reason for assigning these accounts and ensuring they balance based on the rules.")
    
    # ENHANCED: Renamed for clean database architecture
    unreg_usd: float = Field(0.0, description="Amount from unregistered vendors without a VAT TIN.")
    exempt_usd: float = Field(0.0, description="Amount from registered vendors (has TIN) but no VAT is charged.")
    vat_base_usd: float = Field(0.0, description="The net base amount subject to 10% VAT.")
    vat_usd: float = Field(0.0, description="The 10% VAT amount.")
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
        description="Write a short summary verifying: 1. Aggregation logic. 2. Vendor Name location. 3. Account ID reasoning. 4. VATTIN tax status."
    )
    purchase_entries: List[PurchaseEntry] = []

# ====================================================================
# --- MAIN INVOICE PROCESSOR ---
# ====================================================================

class GeminiInvoiceProcessor:
    def __init__(self, api_key):
        print("\n" + "="*50)
        print("🚀 INITIALIZING GEMINI INVOICE PROCESSOR (WITH GL ASSIGNMENT)")
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
        general_vendor, _ = Vendor.objects.get_or_create(
            client_id=client_id,
            vendor_id='V-00001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )
        
        has_tax_info = (vattin and vattin != 'N/A' and str(vattin).strip() != '')
        has_vat_value = (vat_amount is not None and float(vat_amount) > 0)
        
        if not (has_tax_info and has_vat_value) or not raw_name or raw_name == 'Unknown':
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        best_vendor, best_ratio = None, 0.85 
        vendors_to_check = Vendor.objects.filter(client_id=client_id)
        if target_norm:
             vendors_to_check = vendors_to_check.filter(normalized_name__startswith=target_norm[0])

        for v in vendors_to_check:
            if not v.normalized_name: continue
            ratio = difflib.SequenceMatcher(None, target_norm, v.normalized_name).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_vendor = v
        
        if best_vendor:
            return {'db_id': best_vendor.id, 'is_new': False, 'temp_vid': None}

        with self.vendor_lock: 
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

            vendor_ids = Vendor.objects.filter(client_id=client_id, vendor_id__regex=r'^V-\d+').values_list('vendor_id', flat=True)
            max_num = 0
            for vid in vendor_ids:
                match = re.search(r'V-(\d+)', vid)
                if match:
                    max_num = max(max_num, int(match.group(1)))
            
            next_num = max(2, max_num + 1)
            
            current_seq = next_num + len(self.batch_new_vendors)
            new_vid = f"V-{current_seq:05d}"
            
            vendor_data = {'db_id': None, 'is_new': True, 'temp_vid': new_vid, 'temp_id': f"TEMP_{new_vid}"}
            self.batch_new_vendors[target_norm] = vendor_data
            return vendor_data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def process_page(self, file_obj, pg_num, custom_prompt="", rules_context="", memo_context=""):
        try:
            print(f"📄 [Page {pg_num}] Starting Triage...")
            t_resp = self.client.models.generate_content(
                model=self.TRIAGE_MODEL,
                contents=[file_obj, f"Analyze Page {pg_num}. Is it an invoice or bank slip?"],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=RoutingDecision)
            )
            with self.cost_lock: self.cost_stats["flash_cost"] += self.calculate_cost(t_resp.usage_metadata, self.TRIAGE_MODEL)
            if not t_resp.parsed or t_resp.parsed.page_type == "empty": return None

            print(f"🧠 [Page {pg_num}] Audit & GL Assignment starting...")
            
            # ENHANCED PROMPT: 3-Tier XML Structure & VATTIN Isolation
            prompt = f"""
            TASK: Extract accounting data strictly from Page {pg_num}.
            
            <CRITICAL_VATTIN_INSTRUCTION>
            Extract VATTIN EXACTLY as visually printed. Do NOT autocorrect, standardize, or apply regex patterns. 
            If it prints 'B107', extraction MUST be 'B107', NEVER change it to 'L001'.
            </CRITICAL_VATTIN_INSTRUCTION>
            
            <ACCOUNTING_HIERARCHY_RULES>
            Follow these instructions in order of strict priority (1 is highest):
            
            1. [BATCH LEVEL] USER CUSTOM INSTRUCTION:
            {custom_prompt if custom_prompt else "No custom instruction provided."}
            
            2. [COMPANY LEVEL] CLIENT ANTI-PATTERNS & MEMOS:
            {memo_context if memo_context else "No client memos."}
            
            3. [INDUSTRY LEVEL] ACCOUNT ID ASSIGNMENT MANUAL:
            {rules_context}
            </ACCOUNTING_HIERARCHY_RULES>

            <OUTPUT_INSTRUCTIONS>
            1. AGGREGATION: Output exactly ONE PurchaseEntry per page. COMBINE items.
            2. DATE: Format strictly as YYYY-MM-DD. Return null if missing.
            3. VENDOR NAME: Extract the company/shop name.
            4. DESCRIPTION: Original language detail.
            5. DESCRIPTION_EN: Summarize in English ONLY. Max 25 words!
            6. TAX AMOUNTS: 
               - If no VAT TIN, put amount in 'unreg_usd'.
               - If VAT TIN exists but no VAT charged, put amount in 'exempt_usd'.
               - If VAT is charged, put base in 'vat_base_usd' and tax in 'vat_usd'.
            7. BALANCED ASSIGNMENT: Assign IDs so the transaction mathematically balances.
               - account_id: Main Debit (Expense/Asset).
               - vat_account_id: VAT Input Debit (e.g., 115010) if VAT exists.
               - wht_debit_account_id: WHT Expense Debit (e.g., 725420) if company bears the WHT.
               - credit_account_id: Main Credit (Payable/Cash).
               - wht_account_id: WHT Payable Credit (e.g., 210040) if WHT is triggered.
            </OUTPUT_INSTRUCTIONS>
            
            DOUBLE-CHECK PROTOCOL: Fill out 'self_verification_step' first.
            """
            
            a_resp = self.client.models.generate_content(
                model=self.AUDIT_MODEL,
                contents=[file_obj, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AccountingBatch,
                    temperature=0.0, # CRITICAL: Forces strict obedience to VATTIN rules
                    thinking_config=types.ThinkingConfig(thinking_level=t_resp.parsed.complexity.upper())
                )
            )
            
            page_cost = self.calculate_cost(a_resp.usage_metadata, self.AUDIT_MODEL)
            with self.cost_lock: self.cost_stats["pro_cost"] += page_cost
            print(f"🕵️ [Page {pg_num}] AI Check: {a_resp.parsed.self_verification_step}")
            print(f"💲 [Page {pg_num}] AI Cost Log: ${page_cost:.5f}")
            return a_resp.parsed, pg_num
        except Exception as e:
            print(f"❌ [Page {pg_num}] ERROR: {str(e)}")
            raise e 

    def process(self, pdf_path, client_id, custom_prompt="", batch_name="", rules_context="", memo_context=""):
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            if total_pages > 20:
                raise ValueError(f"Limit exceeded. PDF has {total_pages} pages, max is 20.")

        f = self.client.files.upload(file=pdf_path)
        while f.state.name == "PROCESSING": 
            time.sleep(2)
            f = self.client.files.get(name=f.name)

        ledgers = []
        
        # Sequential processing to guarantee order
        for pg in range(1, total_pages + 1):
            try:
                res = self.process_page(f, pg, custom_prompt, rules_context, memo_context)
                if res:
                    audit, _ = res
                    for entry in audit.purchase_entries:
                        entry_dict = entry.model_dump()
                        
                        if 'vendor_name' in entry_dict:
                            entry_dict['company'] = entry_dict.pop('vendor_name')
                        
                        reasoning = entry_dict.pop('account_reasoning', '')
                        entry_dict['instruction'] = f"AI Reason: {reasoning}" if reasoning else ""
                        
                        raw_name = entry_dict.get('company', '')
                        vattin = entry_dict.get('vattin', '')
                        # Uses new vat_usd field
                        vat_amount = entry_dict.get('vat_usd', 0.0) 
                        
                        vendor_data = self.resolve_and_assign_vendor(raw_name, vattin, vat_amount, client_id)
                        if not vendor_data:
                            vendor_data = {'db_id': None, 'is_new': False, 'temp_vid': None, 'temp_id': None}
                        
                        entry_dict['vendor_db_id'] = vendor_data.get('db_id')
                        entry_dict['is_new_vendor'] = vendor_data.get('is_new', False)
                        entry_dict['temp_vid'] = vendor_data.get('temp_vid')
                        entry_dict['temp_id'] = vendor_data.get('temp_id')
                        entry_dict['vendor_choice'] = vendor_data.get('temp_id') if vendor_data.get('is_new') else vendor_data.get('db_id')
                        entry_dict['batch'] = batch_name
                        entry_dict['page'] = pg
                        
                        ledgers.append(entry_dict)
            except Exception as e:
                print(f"❌ [Page {pg}] processing failed: {e}")

        ledgers.sort(key=lambda x: x.get('page', 0))

        return ledgers, total_pages, self.cost_stats
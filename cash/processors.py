import os
import re
import difflib
import threading
import pandas as pd
import numpy as np
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional
from pypdf import PdfReader
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from tools.models import Vendor

# ====================================================================
# --- 1. BANK STATEMENT EXTRACTION SCHEMAS & PROCESSORS ---
# ====================================================================

class BankTransaction(BaseModel):
    sys_id: str = Field(..., description="Sequential ID (e.g., 2025-01-001).")
    bank_ref_id: str = Field(..., description="The unique Bank Reference Number.")
    tr_date: str = Field(..., description="Transaction date in YYYY-MM-DD format.")
    trans_type: str = Field(..., description="The main transaction type.")
    counterparty: str = Field(..., description="The counterparty name, if available.")
    purpose: str = Field(..., description="The text of the transaction details.")
    remark: str = Field(..., description="Matched Vendor and Invoice No (e.g., 'Vendor: X, Inv: Y'). Maximum 250 characters.")
    raw_remark: str = Field(..., description="The full original bank text plus any matched supplementary data including Invoice No.") 
    debit: float = Field(..., description="Money In (0.0 if empty).")
    credit: float = Field(..., description="Money Out (0.0 if empty).")
    balance: float = Field(..., description="Balance column.")

class BankInfo(BaseModel):
    transactions: List[BankTransaction]
    
    @field_validator('transactions', mode='before')
    @classmethod
    def set_sys_id(cls, v):
        if v is not None:
            for i, transaction in enumerate(v):
                year, month = "2025", "01"
                date_source = None
                
                if isinstance(transaction, dict):
                    date_source = transaction.get('tr_date')
                elif hasattr(transaction, 'tr_date'):
                    date_source = getattr(transaction, 'tr_date', None)

                if date_source and isinstance(date_source, str) and date_source.count('-') == 2:
                    try:
                        parts = date_source.split('-')
                        year = parts[0]
                        month = parts[1]
                    except (IndexError, ValueError):
                        pass

                sys_id_val = f"{year}-{month}-{i+1:03d}"

                if isinstance(transaction, dict):
                    transaction['sys_id'] = sys_id_val
                elif hasattr(transaction, 'sys_id'):
                    transaction.sys_id = sys_id_val
        return v


class GeminiABABankProcessor:
    def __init__(self, api_key):
        print("\n" + "="*50)
        print("🏦 INITIALIZING: ABA BANK PROCESSOR")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.MODEL_NAME = "gemini-2.5-flash" 
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def calculate_cost(self, usage):
        if usage: return ((usage.prompt_token_count / 1e6) * 0.075) + ((usage.candidates_token_count / 1e6) * 0.30)
        return 0.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def process(self, pdf_path, batch_name="", custom_prompt=""):
        print(f"\n📄 Reading PDF natively: {os.path.basename(pdf_path)}...")
        try:
            reader = PdfReader(pdf_path)
            raw_text = ""
            for i, page in enumerate(reader.pages):
                raw_text += page.extract_text(extraction_strategy="layout") + "\n"
            if len(raw_text) < 100: raise ValueError("Not enough content. Scanned image?")
        except Exception as e:
            raise e

        print(f"🧠 Sending structured text to Gemini ({self.MODEL_NAME})...")
        print(f"   🔹 PDF Text Length: {len(raw_text)} characters")
        
        # --- ENHANCEMENT: Instruct AI to merge Spreadsheet data into remarks ---
        extraction_prompt = """Extract all bank transactions from the text below. Strictly follow the JSON schema.
        
        CRITICAL EXTRACTION RULES:
        1. Read the SUPPLEMENTARY ROUTING DATA (if provided). Cross-reference it with the bank transactions using dates and amounts.
        2. If matched, extract ONLY the 'Vendor' and 'Invoice No' from the supplementary data and format it into the 'remark' field (e.g., 'Vendor: Cambodia Concrete, Inv: 2026-00012'). Ensure the remark field does not exceed 250 characters.
        3. Include BOTH the original bank PDF text AND the supplementary spreadsheet information (including the Invoice No) in the 'raw_remark' field so no context is lost.
        """
        
        if custom_prompt: 
            print(f"   🔹 Injecting supplementary data and instructions...")
            extraction_prompt += f"\nADDITIONAL INSTRUCTIONS & SUPPLEMENTARY DATA:\n{custom_prompt}\n"
        extraction_prompt += f"\nDATA:\n---\n{raw_text}\n---\n"

        try:
            print("   ⏳ Waiting for Gemini API response...")
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=extraction_prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=BankInfo, temperature=0.0)
            )
            print("   ✅ Received structured data from Gemini API.")
            
            cost = self.calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost

            structured_data = response.parsed
            if not structured_data: raise ValueError("Model returned unparseable schema.")
                
            transactions = [t.model_dump() for t in structured_data.transactions]
            for t in transactions:
                t['batch'] = batch_name
                t['date'] = t.pop('tr_date', None) 
                
                # --- DOUBLE ENTRY BALANCING FOR UI ---
                amt = max(float(t.get('debit') or 0.0), float(t.get('credit') or 0.0))
                t['debit_amount'] = amt
                t['credit_amount'] = amt
            return transactions, len(reader.pages), self.cost_stats
        except Exception as e:
            print(f"❌ AI Extraction Error: {str(e)}")
            raise e

class GeminiCanadiaBankProcessor:
    def __init__(self, api_key): pass
    def process(self, pdf_path, batch_name="", custom_prompt=""): return [], 0, {"flash_cost": 0.0, "pro_cost": 0.0}

class ClientBCustomBankProcessor:
    def __init__(self, api_key): pass
    def process(self, pdf_path, batch_name="", custom_prompt=""): return [], 0, {"flash_cost": 0.0, "pro_cost": 0.0}

# ====================================================================
# --- 2. CASH BOOK EXTRACTION PROCESSOR ---
# ====================================================================

class CashStandardExcelProcessor:
    """Strategy Processor for parsing structured Cash Book Excel/CSV files."""
    
    def __init__(self, api_key=None):
        print("\n" + "="*50)
        print("💵 INITIALIZING: CASH BOOK EXCEL PROCESSOR")
        print("="*50)
        self.vendor_lock = threading.Lock()
        self.batch_new_vendors = {}
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def resolve_and_assign_vendor(self, raw_name, client_id):
        """Matches vendor strictly within the selected client's isolated database."""
        general_vendor, _ = Vendor.objects.get_or_create(
            client_id=client_id,
            vendor_id='V-00001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )
        
        if not raw_name or str(raw_name).lower() in ['nan', 'none', 'unknown', '']:
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        # Exact Match
        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        # Fuzzy Match
        best_vendor, best_coverage = None, 0.0
        for v in Vendor.objects.filter(client_id=client_id):
            if not v.normalized_name or v.normalized_name[0] != target_norm[0]: 
                continue
            matcher = difflib.SequenceMatcher(None, target_norm, v.normalized_name)
            match = matcher.find_longest_match(0, len(target_norm), 0, len(v.normalized_name))
            if match.a == 0 and match.b == 0:
                coverage = match.size / len(target_norm)
                if coverage >= 0.6 and coverage > best_coverage:
                    best_coverage = coverage
                    best_vendor = v

        if best_vendor:
            return {'db_id': best_vendor.id, 'is_new': False, 'temp_vid': None}

        # Cache as New Vendor Candidate
        with self.vendor_lock: 
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

            # Safely find the absolute highest numeric vendor_id in the database
            all_vids = Vendor.objects.filter(client_id=client_id).values_list('vendor_id', flat=True)
            max_num = 1
            for vid in all_vids:
                if vid:
                    match = re.search(r'V-?(\d+)', str(vid))
                    if match:
                        max_num = max(max_num, int(match.group(1)))
            next_num = max_num + 1
            
            current_seq = next_num + len(self.batch_new_vendors)
            new_vid = f"V-{current_seq:05d}"
            
            vendor_data = {'db_id': None, 'is_new': True, 'temp_vid': new_vid, 'temp_id': f"TEMP_{new_vid}"}
            self.batch_new_vendors[target_norm] = vendor_data
            return vendor_data

    def process(self, file_path, client_id, batch_name="", custom_prompt=""):
        print(f"📄 Reading Tabular File natively: {os.path.basename(file_path)}...")
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
                
            # --- FIX: Standardize column names to lowercase to prevent missing data ---
            df.columns = [str(c).strip().lower() for c in df.columns]
            
            df = df.replace({np.nan: None})
        except Exception as e:
            print(f"❌ File Reading Error: {str(e)}")
            raise ValueError(f"Could not read the file. Please ensure it is a valid CSV or Excel file. Details: {str(e)}")

        ledgers = []
        
        def safe_float(val):
            if pd.isna(val) or val is None or val == '': return 0.0
            try:
                clean_str = str(val).replace(',', '').replace('$', '').replace(' ', '').strip()
                if clean_str == '': return 0.0
                return float(clean_str)
            except (ValueError, TypeError):
                return 0.0
                
        # Define common column aliases to make extraction resilient
        debit_aliases = ['debit', 'dr', 'in', 'deposit', 'receipt', 'cash in', 'paid in']
        credit_aliases = ['credit', 'cr', 'out', 'withdrawal', 'payment', 'cash out', 'paid out']

        for index, row in df.iterrows():
            # Attempt to extract debit/credit values using aliases
            d_val, c_val = 0.0, 0.0
            for col in debit_aliases:
                if col in row and safe_float(row.get(col)) != 0.0:
                    d_val = safe_float(row.get(col))
                    break
                    
            for col in credit_aliases:
                if col in row and safe_float(row.get(col)) != 0.0:
                    c_val = safe_float(row.get(col))
                    break
                    
            amt = max(d_val, c_val)
            
            # --- FIX: Skip empty rows or summary rows with no monetary value ---
            if amt == 0.0:
                continue
                
            # Vendor alias fallback
            vendor_col = next((col for col in ['vendor', 'payee', 'customer'] if col in row), 'vendor')
            raw_vendor = str(row.get(vendor_col, '')).strip() if pd.notna(row.get(vendor_col)) else ''
            vendor_data = self.resolve_and_assign_vendor(raw_vendor, client_id)
            
            date_col = next((col for col in ['date', 'txn date', 'transaction date'] if col in row), 'date')
            raw_date = row.get(date_col)
            clean_date = str(raw_date)[:10] if pd.notna(raw_date) and str(raw_date).strip() != '' else None
            
            desc_col = next((col for col in ['description', 'particulars', 'memo', 'details'] if col in row), 'description')
            
            entry_dict = {
                'batch': batch_name,
                'date': clean_date,
                'voucher_no': str(row.get('voucher_no', '')) if pd.notna(row.get('voucher_no')) else '',
                'description': str(row.get(desc_col, '')) if pd.notna(row.get(desc_col)) else '',
                
                'company': raw_vendor,
                'vendor_db_id': vendor_data['db_id'],
                'is_new_vendor': vendor_data['is_new'],
                'temp_vid': vendor_data['temp_vid'],
                'temp_id': vendor_data.get('temp_id'),
                'vendor_choice': vendor_data['temp_id'] if vendor_data['is_new'] else vendor_data['db_id'],
                'invoice_no': str(row.get('invoice_no', '')) if pd.notna(row.get('invoice_no')) else '',
                
                'debit': d_val,
                'credit': c_val,
                'debit_amount': amt,   # Balanced Double Entry Leg
                'credit_amount': amt,  # Balanced Double Entry Leg
                'balance': safe_float(row.get('balance')),
                'note': str(row.get('note', '')) if pd.notna(row.get('note')) else '',
            }
            ledgers.append(entry_dict)

        print(f"🎉 SUCCESS: Parsed {len(ledgers)} cash rows.")
        return ledgers, 1, self.cost_stats


# ====================================================================
# --- 3. AI RECONCILIATION ENGINE (3-TIER ARCHITECTURE) ---
# ====================================================================

class ReconciliationMapping(BaseModel):
    transaction_id: str = Field(..., description="The sys_id or row index of the Bank/Cash transaction.")
    matched_purchase_ids: Optional[List[int]] = Field(None, description="A list of IDs of the matched Purchases. Null if no match.")
    debit_account_id: str = Field(..., description="The 6-digit GL Account code to be Debited.")
    debit_amount: float = Field(..., description="The balancing transaction amount for the Debit leg.")
    credit_account_id: str = Field(..., description="The 6-digit GL Account code to be Credited.")
    credit_amount: float = Field(..., description="The balancing transaction amount for the Credit leg.")
    reasoning: str = Field(..., description="Brief explanation of why these accounts were selected.")

class ReconciliationResult(BaseModel):
    mappings: List[ReconciliationMapping]

class GeminiReconciliationEngine:
    """Dedicated engine to cross-check Bank flows against Open Purchases AND Historical GL."""
    
    def __init__(self, api_key, context_account='100010'):
        print("\n" + "="*50)
        print(f"⚖️ INITIALIZING: GEMINI RECONCILIATION ENGINE (Base Acct: {context_account})")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.MODEL_NAME = "gemini-3.1-pro-preview" 
        self.context_account = context_account 
        
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reconcile(self, transactions_data: str, open_purchases_data: str, prompt_memo: str = "", historical_gl_data: str = ""):
        
        prompt = f"""
        <TIER_1_INDUSTRY_RULES>
        You are an expert strict corporate accountant. Your task is to assign perfect Double-Entry (Debit and Credit) 6-digit GL accounts to bank/cash transactions.
        The primary account being reconciled is: {self.context_account}
        
        Rule 1: MONEY OUT (Payments/Credits to Base Account)
        - CREDIT: Always {self.context_account}.
        - DEBIT: 
            - A Bank or Cash account (e.g., 100000 Cash on Hand) if the remark indicates an internal transfer, ATM withdrawal, cash replenishment, or reimbursement to cash on hand. CRITICAL: For internal transfers, leave 'matched_purchase_ids' empty.
            - 200000 (Trade Payable) IF AND ONLY IF you find an exact match in <OPEN_PURCHASES>, OR if you find that a Payable was already established in the <HISTORICAL_LEDGER> (indicated by a credit to a Payable account for this vendor/invoice). 
              *COMBINATION MATCHING*: Be mathematically creative. A single payment might cover multiple open purchases. If multiple purchases are paid by one transaction, include ALL matched purchase IDs in the 'matched_purchase_ids' list. Use the extracted 'remark'/'raw_remark'/'description'/'note' fields to search these databases.
            - 120000 (Prepayment) if it is a vendor payment but NO matching invoice or prior payable is found in either <OPEN_PURCHASES> or <HISTORICAL_LEDGER>.

        Rule 2: MONEY IN (Receipts/Debits to Base Account)
        - DEBIT: Always {self.context_account}.
        - CREDIT:
            - A Bank or Cash account (e.g., 100010 Cash in Bank) if it is an internal transfer, cash deposit, or replenishment from the bank to cash on hand. CRITICAL: For internal transfers, leave 'matched_purchase_ids' empty.
            - 300000 (Share Capital) if the remark says "Capital", "Shareholders", or "Funds Received" from owners.
            - 400000 (Accounts Receivable) if it is a payment from a customer.
        </TIER_1_INDUSTRY_RULES>

        <TIER_2_COMPANY_MEMO>
        {prompt_memo}
        </TIER_2_COMPANY_MEMO>
        
        <TIER_3_BATCH_DATA>
        Compare the <TRANSACTIONS> against BOTH the <OPEN_PURCHASES> and the <HISTORICAL_LEDGER>. 
        Use the extracted 'remark', 'raw_remark', 'description', and 'note' fields to identify Vendor Name and Invoice Number. 
        Analyze the historical ledger to determine if a transaction is paying off a prior period payable or if it is a new advance payment (prepayment).
        
        <OPEN_PURCHASES>
        {open_purchases_data}
        </OPEN_PURCHASES>
        
        <HISTORICAL_LEDGER>
        {historical_gl_data if historical_gl_data else "No historical ledger provided."}
        </HISTORICAL_LEDGER>
        
        <TRANSACTIONS>
        {transactions_data}
        </TRANSACTIONS>
        </TIER_3_BATCH_DATA>
        """

        try:
            print(f"   ⚖️  Sending Reconciliation Prompt to Gemini ({self.MODEL_NAME})...")
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=ReconciliationResult, 
                    temperature=0.0
                )
            )
            print("   ✅ Received reconciliation mappings from Gemini API.")
            
            flash_cost = 0.0
            pro_cost = 0.0
            if response.usage_metadata:
                pro_cost = ((response.usage_metadata.prompt_token_count / 1e6) * 1.25) + ((response.usage_metadata.candidates_token_count / 1e6) * 5.00)
                print(f"💲 Reconciliation AI Cost Log: ${pro_cost:.5f}")

            return response.parsed.mappings, {"flash_cost": flash_cost, "pro_cost": pro_cost}
        except Exception as e:
            print(f"❌ Reconciliation Error: {str(e)}")
            return [], {"flash_cost": 0.0, "pro_cost": 0.0}

# ====================================================================
# --- STRATEGY MAPS ---
# ====================================================================

BANK_PROCESSOR_MAP = {
    'aba_standard': GeminiABABankProcessor,
    'canadia_standard': GeminiCanadiaBankProcessor,
    'client_b_custom': ClientBCustomBankProcessor,
}

CASH_PROCESSOR_MAP = {
    'standard_excel': CashStandardExcelProcessor,
}
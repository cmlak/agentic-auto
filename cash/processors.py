import os
import re
import json
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
from sale.models import Sale

# ====================================================================
# --- 1. SCHEMAS ---
# ====================================================================

class BankTransaction(BaseModel):
    sys_id: str = Field(..., description="Sequential ID (e.g., 2025-01-001).")
    bank_ref_id: str = Field(..., description="The unique Bank Reference Number.")
    tr_date: str = Field(..., description="Transaction date in YYYY-MM-DD format.")
    trans_type: str = Field(..., description="The main transaction type.")
    counterparty: str = Field(..., description="The counterparty name, if available.")
    vendor_name: str = Field("", description="Cleaned B2B supplier or payee name. Empty if internal transfer.")
    customer_name: str = Field("", description="Cleaned B2B customer or payer name. Empty if internal transfer or money out.")
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
# ====================================================================
# --- 2. THE PROCESSOR ---
# ====================================================================

class GeminiABABankProcessor:
    def __init__(self, api_key):
        print("\n" + "="*50)
        print("🏦 INITIALIZING: ABA BANK PROCESSOR (3-TIER AGENT)")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.MODEL_NAME = "gemini-3.1-pro-preview" 
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}
        
        # --- EXPLICIT CONTEXT ANCHOR ---
        self.bank_name = "ABA Bank" 

    def calculate_cost(self, usage):
        if usage: return ((usage.prompt_token_count / 1e6) * 1.25) + ((usage.candidates_token_count / 1e6) * 10.00)
        return 0.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def process(self, pdf_path, client_id, batch_name="", custom_prompt=""):
        print(f"\n📄 Reading PDF natively: {os.path.basename(pdf_path)}...")
        try:
            reader = PdfReader(pdf_path)
            raw_text = ""
            for i, page in enumerate(reader.pages):
                raw_text += page.extract_text(extraction_strategy="layout") + "\n"
            if len(raw_text) < 100: raise ValueError("Not enough content. Scanned image?")
        except Exception as e:
            raise e

        print(f"🧠 Sending structured text to Gemini ({self.MODEL_NAME})...", flush=True)
        
        # --- THE THREE-TIER HIERARCHY PROMPT ---
        extraction_prompt = f"""
        <TIER_1_CORE_EXTRACTION_RULES>
        DOCUMENT CONTEXT: This is an official bank statement issued by {self.bank_name}.
        
        Extract ALL bank transactions from the text below. Strictly follow the JSON schema.
        1. 100% COMPLETENESS: You MUST extract EVERY SINGLE transaction. Do NOT skip rows, alternate rows, or leave fields blank to save output length.
        2. MULTI-LINE MERGING: Bank statements often split a single transaction across 2 or 3 lines. You MUST merge these multi-line descriptions into ONE single JSON object per transaction.
        3. COUNTERPARTY EXTRACTION (UNIVERSAL):
           - VENDOR (Money Out): Analyze the description to extract the true B2B Vendor or Payee Name into 'vendor_name'. 
           - CUSTOMER (Money In): Analyze the description to extract the true B2B Customer or Payer Name into 'customer_name'.
           - SYSTEM OVERRIDES: You MUST read <TIER_2_CLIENT_ACCOUNTING_MEMO> for strict rules on what exact strings to output for edge cases.
        4. SUPPLEMENTARY ROUTING DATA: Cross-reference any provided supplementary data. If matched, extract the 'Vendor'/'Customer' and 'Invoice No' into the 'remark' field.
        5. CONTEXT PRESERVATION: Put BOTH the original merged bank text AND any matched supplementary information into the 'raw_remark' field.
        </TIER_1_CORE_EXTRACTION_RULES>

        <TIER_2_CLIENT_ACCOUNTING_MEMO>
        The following instructions take absolute precedence over any default rules above:
        {custom_prompt if custom_prompt else "No custom instructions provided."}
        </TIER_2_CLIENT_ACCOUNTING_MEMO>

        <TIER_3_RAW_STATEMENT_DATA>
        {raw_text}
        </TIER_3_RAW_STATEMENT_DATA>
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=extraction_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=BankInfo, 
                    temperature=0.0
                )
            )

            # ---------------------------------------------------------
            # 🐛 DEBUGGING INTERCEPTOR 
            # ---------------------------------------------------------
            print("\n" + "-"*30 + " RAW AI OUTPUT " + "-"*30, flush=True)
            print(response.text, flush=True)
            print("-"*75 + "\n", flush=True)

            structured_data = response.parsed
            if not structured_data: raise ValueError("Model returned unparseable schema.")
            print("   ✅ Successfully parsed structured data from Gemini API.", flush=True)
            
            cost = self.calculate_cost(response.usage_metadata)
            self.cost_stats["pro_cost"] += cost
                
            # --- VENDOR & CUSTOMER RESOLUTION INITIALIZATION ---
            all_vids = Vendor.objects.filter(client_id=client_id).values_list('vendor_id', flat=True)
            max_num = 1
            for vid in all_vids:
                if vid:
                    match = re.search(r'V-?(\d+)', str(vid))
                    if match: max_num = max(max_num, int(match.group(1)))
            next_num = max_num + 1
            batch_new_vendors = {}
            
            from django.apps import apps
            try: Customer = apps.get_model('sale', 'Customer')
            except LookupError: 
                try: Customer = apps.get_model('sales', 'Customer')
                except LookupError: Customer = None
                
            all_cids = Customer.objects.filter(client_id=client_id).values_list('customer_id', flat=True) if Customer else []
            max_cnum = 1
            for cid in all_cids:
                if cid:
                    match = re.search(r'C-?(\d+)', str(cid))
                    if match: max_cnum = max(max_cnum, int(match.group(1)))
            next_cnum = max_cnum + 1
            batch_new_customers = {}
                
            transactions = [t.model_dump() for t in structured_data.transactions]
            
            for t in transactions:
                t['batch'] = batch_name
                t['date'] = t.pop('tr_date', None) 
                
                amt = max(float(t.get('debit') or 0.0), float(t.get('credit') or 0.0))
                t['debit_amount'] = amt
                t['credit_amount'] = amt
                
                raw_vendor = t.pop('vendor_name', '') or ''
                raw_customer = t.pop('customer_name', '') or ''
                
                trans_type = str(t.get('trans_type', '')).lower()
                purpose = str(t.get('purpose', '')).lower()
                
                is_money_out = float(t.get('credit') or 0.0) > 0
                is_money_in = float(t.get('debit') or 0.0) > 0
                
                # =========================================================
                # 3. NEW: THE PYTHON FAILSAFE (DETERMINISTIC OVERRIDE)
                # =========================================================
                # Use highly specific banking terms to prevent false positives like "professional fee"
                bank_fee_triggers = [
                    'interbank fund transfer fee', 
                    'bank charge', 
                    'bank fee', 
                    'maintenance fee', 
                    'checkbook', 
                    'bank commission'
                ]
                
                if is_money_out and any(keyword in trans_type or keyword in purpose for keyword in bank_fee_triggers):
                    raw_vendor = self.bank_name
                # =========================================================

                # =========================================================
                # VENDOR RESOLUTION (MONEY OUT)
                # =========================================================
                if amt > 0 and is_money_out and raw_vendor and str(raw_vendor).strip() != "" and str(raw_vendor).lower() != "none":
                    
                    name_str = str(raw_vendor).lower().replace('&', ' and ')
                    target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()
                    
                    if len(target_norm) >= 3:
                        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
                        best_vendor = None
                        if not exact_match:
                            best_coverage = 0.0
                            for v in Vendor.objects.filter(client_id=client_id):
                                if not v.normalized_name or not target_norm or v.normalized_name[0] != target_norm[0]: 
                                    continue
                                matcher = difflib.SequenceMatcher(None, target_norm, v.normalized_name)
                                match = matcher.find_longest_match(0, len(target_norm), 0, len(v.normalized_name))
                                if match.a == 0 and match.b == 0:
                                    coverage = match.size / len(target_norm)
                                    if coverage >= 0.8 and coverage > best_coverage:
                                        best_coverage = coverage
                                        best_vendor = v
                        
                        is_new = False
                        temp_vid, temp_id = None, None

                        if not exact_match and not best_vendor:
                            if target_norm not in batch_new_vendors:
                                new_vid = f"V-{next_num:05d}"
                                batch_new_vendors[target_norm] = {
                                    'temp_vid': new_vid,
                                    'temp_id': f"TEMP_{new_vid}",
                                    'company': raw_vendor.title()
                                }
                                next_num += 1
                                print(f"      ✨ Identified New Vendor: {raw_vendor.title()} ({new_vid})", flush=True)
                            
                            mapped = batch_new_vendors[target_norm]
                            is_new = True
                            temp_vid = mapped['temp_vid']
                            temp_id = mapped['temp_id']
                            vendor_id_to_assign = temp_id
                            company_to_assign = mapped['company']
                        else:
                            vendor_id_to_assign = exact_match.id if exact_match else best_vendor.id

                        t['vendor_choice'] = vendor_id_to_assign
                        
                        if is_new:
                            t['is_new_vendor'] = True
                            t['company'] = company_to_assign
                            t['temp_id'] = temp_id
                            t['temp_vid'] = temp_vid
                            
                    if not t.get('remark'):
                        t['remark'] = f"Vendor: {raw_vendor.title()}"
                    elif "Vendor:" not in t['remark']:
                        t['remark'] += f" | Vendor: {raw_vendor.title()}"

                # =========================================================
                # CUSTOMER RESOLUTION (MONEY IN)
                # =========================================================
                if Customer and amt > 0 and is_money_in and raw_customer and str(raw_customer).strip() != "" and str(raw_customer).lower() != "none":
                    
                    if str(raw_customer).lower() == 'capital injection':
                        c_exact = Customer.objects.filter(client_id=client_id, name__iexact='Capital Injection').first()
                        if c_exact:
                            t['customer_choice'] = c_exact.id
                            if not t.get('remark'): t['remark'] = f"Customer: Capital Injection"
                            elif "Customer:" not in t['remark']: t['remark'] += f" | Customer: Capital Injection"
                            continue
                        else:
                            raw_customer = 'Capital Injection'

                    name_str = str(raw_customer).lower().replace('&', ' and ')
                    target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()
                    
                    if len(target_norm) >= 3:
                        exact_match = Customer.objects.filter(client_id=client_id, normalized_name=target_norm).first()
                        best_customer = None
                        if not exact_match:
                            best_coverage = 0.0
                            for c in Customer.objects.filter(client_id=client_id):
                                if not c.normalized_name or not target_norm or c.normalized_name[0] != target_norm[0]: 
                                    continue
                                matcher = difflib.SequenceMatcher(None, target_norm, c.normalized_name)
                                match = matcher.find_longest_match(0, len(target_norm), 0, len(c.normalized_name))
                                if match.a == 0 and match.b == 0:
                                    coverage = match.size / len(target_norm)
                                    if coverage >= 0.8 and coverage > best_coverage:
                                        best_coverage = coverage
                                        best_customer = c
                        
                        is_new = False
                        temp_cid, temp_id = None, None

                        if not exact_match and not best_customer:
                            if target_norm not in batch_new_customers:
                                new_cid = f"C-{next_cnum:05d}"
                                batch_new_customers[target_norm] = {
                                    'temp_cid': new_cid,
                                    'temp_id': f"TEMP_{new_cid}",
                                    'company': raw_customer.title() if raw_customer.lower() != 'capital injection' else 'Capital Injection'
                                }
                                next_cnum += 1
                                print(f"      ✨ Identified New Customer: {batch_new_customers[target_norm]['company']} ({new_cid})", flush=True)
                            
                            mapped = batch_new_customers[target_norm]
                            is_new = True
                            temp_cid = mapped['temp_cid']
                            temp_id = mapped['temp_id']
                            customer_id_to_assign = temp_id
                            company_to_assign = mapped['company']
                        else:
                            customer_id_to_assign = exact_match.id if exact_match else best_customer.id

                        t['customer_choice'] = customer_id_to_assign
                        
                        if is_new:
                            t['is_new_customer'] = True
                            t['customer_company'] = company_to_assign
                            t['customer_temp_id'] = temp_id
                            t['customer_temp_cid'] = temp_cid
                            
                    if not t.get('remark'):
                        t['remark'] = f"Customer: {raw_customer.title()}"
                    elif "Customer:" not in t['remark']:
                        t['remark'] += f" | Customer: {raw_customer.title()}"

            return transactions, len(reader.pages), self.cost_stats
        except Exception as e:
            print(f"❌ AI Extraction Error: {str(e)}", flush=True)
            raise e

class GeminiCanadiaBankProcessor:
    def __init__(self, api_key): pass
    def process(self, pdf_path, client_id, batch_name="", custom_prompt=""): return [], 0, {"flash_cost": 0.0, "pro_cost": 0.0}

class ClientBCustomBankProcessor:
    def __init__(self, api_key): pass
    def process(self, pdf_path, client_id, batch_name="", custom_prompt=""): return [], 0, {"flash_cost": 0.0, "pro_cost": 0.0}

# ====================================================================
# --- 2. CASH BOOK EXTRACTION PROCESSOR ---
# ====================================================================

class CashStandardExcelProcessor:
    """Strategy Processor for parsing structured Cash Book Excel/CSV files."""
    
    def __init__(self, api_key=None):
        print("\n" + "="*50)
        print("💵 INITIALIZING: CASH BOOK EXCEL PROCESSOR")
        print("="*50)
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

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

        # Dynamically identify columns outside the loop to handle variations like "Invoice No." or "Page No"
        cols = list(df.columns)
        vendor_col = next((col for col in cols if any(x in col for x in ['vendor', 'payee', 'customer'])), 'vendor')
        date_col = next((col for col in cols if any(x in col for x in ['date', 'txn date', 'transaction date'])), 'date')
        desc_col = next((col for col in cols if any(x in col for x in ['description', 'particular', 'memo', 'detail'])), 'description')
        inv_col = next((col for col in cols if any(x in col for x in ['invoice', 'inv'])), 'invoice_no')
        vch_col = next((col for col in cols if any(x in col for x in ['voucher', 'vch'])), 'voucher_no')
        page_col = next((col for col in cols if any(x in col for x in ['page', 'pg'])), 'page')

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
                
            raw_vendor = str(row.get(vendor_col, '')).strip() if pd.notna(row.get(vendor_col)) else ''
            raw_date = row.get(date_col)
            clean_date = str(raw_date)[:10] if pd.notna(raw_date) and str(raw_date).strip() != '' else None
            
            entry_dict = {
                'batch': batch_name,
                'date': clean_date,
                'voucher_no': str(row.get(vch_col, '')) if pd.notna(row.get(vch_col)) else '',
                'description': str(row.get(desc_col, '')) if pd.notna(row.get(desc_col)) else '',
                
                'company': raw_vendor,
                'vendor_choice': '',
                'invoice_no': str(row.get(inv_col, '')) if pd.notna(row.get(inv_col)) else '',
                'page': str(row.get(page_col, '')) if pd.notna(row.get(page_col)) else '',
                
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
    matched_sale_ids: Optional[List[int]] = Field(None, description="A list of IDs of the matched Sales. Null if no match.")
    debit_account_id: str = Field(..., description="The GL Account code to be Debited. If unknown or unprovided, output 'UNKNOWN'.")
    debit_amount: float = Field(..., description="The balancing transaction amount for the Debit leg.")
    credit_account_id: str = Field(..., description="The GL Account code to be Credited. If unknown or unprovided, output 'UNKNOWN'.")
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
    def reconcile(self, transactions_data: str, open_purchases_data: str, open_sales_data: str = "[]", prompt_memo: str = "", historical_gl_data: str = "", chart_of_accounts_data: str = ""):
        
        prompt = f"""
        <TIER_1_CORE_RECONCILIATION_RULES>
        You are an expert strict corporate accountant. Your task is to assign perfect Double-Entry (Debit and Credit) GL accounts to bank/cash transactions.
        The primary account being reconciled is: {self.context_account}

        MECHANICAL RULE 1: MONEY OUT (Payment from Base Account)
        - The CREDIT side is ALWAYS the base account: {self.context_account}.
        - For the DEBIT side, you MUST defer to the logic in <TIER_2_CLIENT_ACCOUNTING_MEMO> and find the matching 6-digit code in the <CHART_OF_ACCOUNTS>.
            a) An opposing Bank/Cash account for internal transfers.
            b) A Trade Payable account if a match is found in <OPEN_PURCHASES> or <HISTORICAL_LEDGER>.
            c) A Prepayment account if no match is found.
            d) Any other expense or asset account based on keyword mappings.

        MECHANICAL RULE 2: MONEY IN (Receipt to Base Account)
        - The DEBIT side is ALWAYS the base account: {self.context_account}.
        - For the CREDIT side, you MUST defer to the logic in <TIER_2_CLIENT_ACCOUNTING_MEMO> and find the matching 6-digit code in the <CHART_OF_ACCOUNTS>.
            a) An opposing Bank/Cash account for internal transfers.
            b) An Accounts Receivable account if a match is found in <OPEN_SALES> or <HISTORICAL_LEDGER>.
            c) A Share Capital account for capital injections.
            d) Any other revenue or liability account based on keyword mappings.
        
        MECHANICAL RULE 3: DATA CROSS-REFERENCING
        - You MUST use the 'description', 'remark', 'raw_remark', 'note', 'page', and 'invoice_no' fields from the <TRANSACTIONS> data to search for matches within <OPEN_PURCHASES>, <OPEN_SALES>, and <HISTORICAL_LEDGER>.
        - CRITICAL: If 'page' or 'invoice_no' is present in the transaction, prioritize matching it against the corresponding fields in <OPEN_PURCHASES> or <OPEN_SALES>. If they are missing, you MUST still attempt to match based on amount, date, and vendor name.
        - If a payment covers multiple invoices, you MUST include all matched IDs in the 'matched_purchase_ids' or 'matched_sale_ids' list.
        - If you absolutely cannot find a relevant 6-digit code in the <CHART_OF_ACCOUNTS>, output 'UNKNOWN' for the account ID.
        </TIER_1_CORE_RECONCILIATION_RULES>

        <TIER_2_CLIENT_ACCOUNTING_MEMO>
        The following client-specific accounting rules, GL account mappings, and keyword triggers take absolute precedence and provide the specific logic needed to execute the mechanical rules in Tier 1.
        {prompt_memo if prompt_memo else "No specific client memo provided."}
        </TIER_2_CLIENT_ACCOUNTING_MEMO>
        
        <TIER_3_BATCH_DATA>
        <CHART_OF_ACCOUNTS>
        You MUST ONLY use the 6-digit codes provided in this list:
        {chart_of_accounts_data}
        </CHART_OF_ACCOUNTS>

        <OPEN_PURCHASES>
        {open_purchases_data}
        </OPEN_PURCHASES>
        
        <OPEN_SALES>
        {open_sales_data}
        </OPEN_SALES>
        
        <HISTORICAL_LEDGER>
        {historical_gl_data if historical_gl_data else "No historical ledger provided."}
        </HISTORICAL_LEDGER>
        
        <TRANSACTIONS>
        {transactions_data}
        </TRANSACTIONS>
        </TIER_3_BATCH_DATA>
        """

        try:
            print(f"   ⚖️  Sending Reconciliation Prompt to Gemini ({self.MODEL_NAME})...", end=" ", flush=True)
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=ReconciliationResult, 
                    temperature=0.0
                )
            )
            
            # ---------------------------------------------------------
            # 🐛 RECONCILIATION DEBUGGING INTERCEPTOR 
            # ---------------------------------------------------------
            print("\n" + "-"*30 + " RAW AI RECONCILIATION OUTPUT " + "-"*30, flush=True)
            print(response.text, flush=True)
            print("-" * 90 + "\n", flush=True)
            
            print("✅ Received.", flush=True)
            
            flash_cost = 0.0
            pro_cost = 0.0
            if response.usage_metadata:
                pro_cost = ((response.usage_metadata.prompt_token_count / 1e6) * 1.25) + ((response.usage_metadata.candidates_token_count / 1e6) * 5.00)
                print(f"💲 Reconciliation AI Cost Log: ${pro_cost:.5f}")

            return response.parsed.mappings, {"flash_cost": flash_cost, "pro_cost": pro_cost}
        except Exception as e:
            print(f"\n❌ Reconciliation Error: {str(e)}", flush=True)
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
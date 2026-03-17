import os
import time
import threading
import re
import difflib
import pandas as pd
import numpy as np
import pdfplumber
from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Vendor, Client 
from account.models import Account, AccountMappingRule

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

# ====================================================================
# --- PYDANTIC SCHEMAS FOR REVERSE-ENGINEERING ---
# ====================================================================
class MigratedPurchase(BaseModel):
    gl_no: Optional[str] = Field(None, description="The extracted GL ID/No.")
    date: str = Field(..., description="Transaction Date (YYYY-MM-DD)")
    company: str = Field(..., description="Vendor Name")
    description: str = Field(..., description="Nature of the expense")
    account_id: int = Field(..., description="The main Expense/Asset GL account code (e.g., 181000). DO NOT use 200000 or 115010 here.")
    vat_usd: float = Field(default=0.0, description="Amount debited to VAT Input (115010), if any.")
    total_usd: float = Field(..., description="Total amount credited to Trade Payable (200000).")

class MigratedBankCash(BaseModel):
    gl_no: Optional[str] = Field(None, description="The extracted GL ID/No.")
    date: str = Field(..., description="Transaction Date (YYYY-MM-DD)")
    counterparty: str = Field(..., description="Vendor or Customer Name")
    purpose: str = Field(..., description="Transaction details/description")
    ledger_account_id: int = Field(..., description="The specific Bank or Cash Account ID (e.g., 100200, 100310, 100000) that this transaction belongs to.")
    debit: float = Field(default=0.0, description="Money IN to the Bank/Cash account")
    credit: float = Field(default=0.0, description="Money OUT of the Bank/Cash account")

# ====================================================================
# --- THE MIGRATION ENGINE (DB-BACKED 3-TIER ARCHITECTURE) ---
# ====================================================================
class GLMigrationProcessor:
    def __init__(self, api_key, client_id):
        print("\n" + "="*50)
        print("🔄 INITIALIZING: HISTORICAL GL MIGRATION ENGINE (DB-BACKED)")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.MODEL_NAME = "gemini-3.1-pro-preview"
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}
        
        # 1. LOAD TIER-1 FOUNDATION FROM DATABASE
        self._load_chart_of_accounts(client_id)

    def calculate_cost(self, usage, model_id):
        rates = {"gemini-3.1-pro-preview": {"in": 1.25, "out": 10.00}, "gemini-3-flash-preview": {"in": 0.10, "out": 0.40}}
        r = rates.get(model_id, {"in": 1.25, "out": 10.00})
        if usage: return ((usage.prompt_token_count / 1e6) * r["in"]) + ((usage.candidates_token_count / 1e6) * r["out"])
        return 0.0

    def _load_chart_of_accounts(self, client_id):
        """Dynamically loads the Chart of Accounts and Rules from the Django Database."""
        try:
            # Fetch all accounts for the selected client
            accounts = Account.objects.filter(client_id=client_id)
            
            # Fetch all custom mapping rules for the client
            rules = AccountMappingRule.objects.filter(client_id=client_id).select_related('account')
            
            # Create a quick dictionary lookup by account_id for fast merging
            rule_dict = {str(rule.account.account_id): rule for rule in rules}

            self.tier_1_prompt = "<TIER_1_CHART_OF_ACCOUNTS>\n"
            self.cash_ids = []
            self.bank_ids = []
            self.ap_ids = []

            for acc in accounts:
                acc_id = str(acc.account_id)
                acc_name = acc.name
                
                # Merge rule data if it exists in AccountMappingRule
                rule = rule_dict.get(acc_id)
                keywords = rule.trigger_keywords if rule else "None specified"
                guideline = rule.ai_guideline if rule else "Apply standard corporate accounting principles."

                # Inject into the Tier 1 Prompt
                self.tier_1_prompt += f"ID: {acc_id} | Name: {acc_name} | Keywords: {keywords} | Rules: {guideline}\n"

                # Build Dynamic Routing Lists (Case-insensitive matching)
                name_lower = acc_name.lower()
                if 'cash' in name_lower and 'bank' not in name_lower:
                    self.cash_ids.append(acc_id)
                if 'bank' in name_lower or 'canadia' in name_lower or 'aba' in name_lower:
                    self.bank_ids.append(acc_id)
                if 'payable' in name_lower:
                    self.ap_ids.append(acc_id)
                    
            self.tier_1_prompt += "</TIER_1_CHART_OF_ACCOUNTS>\n"

            print(f"📊 Loaded Tier-1 CoA from DB: {accounts.count()} Accounts recognized.")
            print(f"🏦 Bank IDs routing: {self.bank_ids}")
            print(f"💵 Cash IDs routing: {self.cash_ids}")
            print(f"🧾 AP IDs routing: {self.ap_ids}")
            
        except Exception as e:
            raise ValueError(f"CRITICAL: Failed to load Chart of Accounts from Database: {str(e)}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _parse_cluster(self, cluster_text, tx_type):
        """Executes the AI Request using the 3-Tier Hierarchy."""
        
        if tx_type == 'PURCHASE':
            schema = MigratedPurchase
            tier_2 = """
            <TIER_2_MIGRATION_RULES>
            Reconstruct the original Accounts Payable Invoice from these General Ledger lines.
            - 'total_usd' MUST BE the exact amount Credited to Trade Payable (e.g., 200000).
            - 'vat_usd' MUST BE the exact amount Debited to VAT Input (115010).
            - 'account_id' MUST BE the underlying expense/asset account (e.g., 181000). Cross-reference TIER_1 to find the exact ID based on Keywords and Rules.
            </TIER_2_MIGRATION_RULES>
            """
        else:
            schema = MigratedBankCash
            tier_2 = f"""
            <TIER_2_MIGRATION_RULES>
            Reconstruct the original {tx_type} transaction from these General Ledger lines.
            - Identify the exact 'ledger_account_id' (The specific Bank or Cash account involved, cross-referencing TIER_1).
            - 'debit' represents Money Received into the {tx_type} account.
            - 'credit' represents Money Paid Out of the {tx_type} account.
            </TIER_2_MIGRATION_RULES>
            """

        prompt = f"""
        {self.tier_1_prompt}
        {tier_2}
        <TIER_3_GL_CLUSTER>\n{cluster_text}\n</TIER_3_GL_CLUSTER>
        """

        response = self.client.models.generate_content(
            model=self.MODEL_NAME, 
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0.0)
        )
        
        cost = self.calculate_cost(response.usage_metadata, self.MODEL_NAME)
        self.cost_stats["pro_cost"] += cost
        print(f"💲 GL Migration AI Cost Log ({tx_type}): ${cost:.5f}")
        
        return response.parsed

    def process_migration_file(self, file_path):
        """Groups GL rows into clusters and routes them based on Dynamic Account IDs."""
        try:
            if file_path.endswith('.csv'): df = pd.read_csv(file_path)
            else: df = pd.read_excel(file_path)
            df = df.replace({np.nan: None})
        except Exception as e:
            raise ValueError(f"Could not read GL file: {str(e)}")

        results = {'purchases': [], 'bank_txns': [], 'cash_txns': []}

        # 1. CLUSTER THE DATA (Group by ID, Date, and Vendor)
        # Using sort=False ensures processing follows the ID's chronological order instead of random/alphabetical
        group_cols = []
        has_id = 'ID' in df.columns
        if has_id:
            group_cols.append('ID')
        group_cols.extend(['Date', 'Vendor / Customer / Employee'])
        
        grouped = df.groupby(group_cols, dropna=False, sort=False)

        for keys, group in grouped:
            if has_id:
                sys_no, date, entity = keys[0], keys[1], keys[2]
            else:
                sys_no = f"R-{group.index[0]}"
                date, entity = keys[0], keys[1]
                
            cluster_text = group.to_string(index=False)
            accounts_involved = " ".join(group['No.'].dropna().astype(str).tolist())
            
            # 2. DYNAMIC ROUTING LOGIC
            # Route A: Purchase Invoice (Is there a Credit to ANY Payable account?)
            ap_rows = group[group['No.'].str.contains('|'.join(self.ap_ids), na=False)]
            is_ap_creation = False
            for _, row in ap_rows.iterrows():
                try: 
                    if float(str(row['Credit']).replace(',','')) > 0: is_ap_creation = True
                except ValueError: pass

            if is_ap_creation:
                print(f"📦 Routing to Purchase AI: ID [{sys_no}] | {date} | {entity}")
                try:
                    parsed_inv = self._parse_cluster(cluster_text, 'PURCHASE')
                    parsed_dict = parsed_inv.model_dump()
                    parsed_dict['gl_no'] = str(sys_no)
                    results['purchases'].append(parsed_dict)
                except Exception as e:
                    print(f"Failed to parse Purchase: {e}")
                continue

            # Route B: Bank Transaction (Contains ANY Bank Account from the CoA)
            if any(b_id in accounts_involved for b_id in self.bank_ids):
                print(f"🏦 Routing to Bank AI: ID [{sys_no}] | {date} | {entity}")
                try:
                    parsed_bank = self._parse_cluster(cluster_text, 'BANK')
                    parsed_dict = parsed_bank.model_dump()
                    parsed_dict['gl_no'] = str(sys_no)
                    results['bank_txns'].append(parsed_dict)
                except Exception as e:
                    print(f"Failed to parse Bank: {e}")
                continue

            # Route C: Cash Transaction (Contains ANY Cash Account from the CoA)
            if any(c_id in accounts_involved for c_id in self.cash_ids):
                print(f"💵 Routing to Cash AI: ID [{sys_no}] | {date} | {entity}")
                try:
                    parsed_cash = self._parse_cluster(cluster_text, 'CASH')
                    parsed_dict = parsed_cash.model_dump()
                    parsed_dict['gl_no'] = str(sys_no)
                    results['cash_txns'].append(parsed_dict)
                except Exception as e:
                    print(f"Failed to parse Cash: {e}")

        total_cost = self.cost_stats['flash_cost'] + self.cost_stats['pro_cost']
        print(f"💰 Total Batch Migration AI Cost: ${total_cost:.5f}")
        return results, self.cost_stats
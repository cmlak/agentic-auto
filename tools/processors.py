import os
import time
import json
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

from .models import Vendor, Client, JournalVoucher
from account.models import Account, AccountMappingRule

# ====================================================================
# PHASE 1: STRICT DATA SCHEMA
# ====================================================================
class RoutingDecision(BaseModel):
    page_type: Literal["invoice", "bank_slip", "empty"]
    complexity: Literal["low", "medium", "high"]

class PurchaseEntry(BaseModel):
    date: Optional[str] = Field(None, description="Date of the invoice (YYYY-MM-DD).")
    
    invoice_no: str = Field("NEEDS_SEQ", description="Extract EXACTLY as printed, OR follow custom starting instructions from prompt. If missing and no instruction, output 'NEEDS_SEQ'.")
    
    vattin: str = Field("N/A", description="VAT Registration Number. CRITICAL: Extract EXACTLY as printed. Do NOT standardize or autocorrect.")
    vendor_name: str = Field(..., description="Vendor name. If in Khmer or Chinese, translate to English.")
    description: str = Field(..., description="Detailed description of the items in the original language.")
    description_en: str = Field(..., description="Summarize the detailed description in English ONLY. Maximum 25 words.")
    
    account_id: Optional[str] = Field(None, description="Main Debit Account ID strictly from the Chart of Accounts.")
    vat_account_id: Optional[str] = Field(None, description="Debit Account ID for VAT Input strictly from the Chart of Accounts. Leave null if no VAT.")
    wht_debit_account_id: Optional[str] = Field(None, description="Debit Account ID for WHT Expense strictly from the Chart of Accounts. Leave null if no WHT.")
    credit_account_id: Optional[str] = Field(None, description="Main Credit Account ID strictly from the Chart of Accounts (e.g., Trade Payable).")
    wht_account_id: Optional[str] = Field(None, description="Credit Account ID for WHT Payable strictly from the Chart of Accounts. Leave null if no WHT.")
    
    account_reasoning: str = Field("", description="Brief reason for assigning these accounts.")
    
    unreg_usd: float = Field(0.0, description="Amount from unregistered vendors without a VAT TIN.")
    exempt_usd: float = Field(0.0, description="Amount from registered vendors (has TIN) but no VAT is charged.")
    vat_base_usd: float = Field(0.0, description="The net base amount subject to 10% VAT.")
    vat_usd: float = Field(0.0, description="The 10% VAT amount.")
    total_usd: float
    page: int = Field(..., description="The page number. Follow instructions if a specific starting page is provided, otherwise use the physical page.")

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
        from .models import Vendor # Lazy import to avoid circular dependencies
        
        general_vendor, _ = Vendor.objects.get_or_create(
            client_id=client_id,
            vendor_id='V-00001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )
        
        if not raw_name or str(raw_name).strip().lower() in ['unknown', 'n/a', 'none', '']:
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

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

        with self.vendor_lock: 
            if target_norm in self.batch_new_vendors:
                return self.batch_new_vendors[target_norm]

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

    def process_page(self, gemini_file_name, pg, client_id, custom_prompt="", batch_name="", rules_context="", memo_context="", current_invoice_seq=1, date_prefix="20260226"):
        # from account.models import Account # Lazy import
        
        try:
            f = self.client.files.get(name=gemini_file_name)
        except Exception as e:
            return [], 0.0, current_invoice_seq, f"Could not retrieve file: {e}"

        ledgers = []
        page_cost = 0.0
        
        coa_qs = Account.objects.filter(client_id=client_id).order_by('account_id')
        coa_context = "\n".join([f"{a.account_id} - {a.name} ({a.account_type})" for a in coa_qs]) if coa_qs.exists() else "No Chart of Accounts provided."
        
        try:
            prompt = f"""
            TASK: Extract accounting data strictly from Page {pg}.
            
            <CRITICAL_VATTIN_INSTRUCTION>
            Extract VATTIN EXACTLY as visually printed. Do NOT autocorrect or apply regex patterns. 
            </CRITICAL_VATTIN_INSTRUCTION>
            
            <CHART_OF_ACCOUNTS>
            {coa_context}
            </CHART_OF_ACCOUNTS>
            
            <ACCOUNTING_HIERARCHY_RULES>
            1. [BATCH LEVEL]: {custom_prompt if custom_prompt else "None"}
            2. [INDUSTRY LEVEL] RULES: {rules_context}
            3. [COMPANY LEVEL] MEMOS: {memo_context if memo_context else "None"}
            </ACCOUNTING_HIERARCHY_RULES>

            <OUTPUT_INSTRUCTIONS>
            1. AGGREGATION & SPLITTING: Normally, output ONE PurchaseEntry per page by combining items. 
               **CRITICAL EXCEPTION:** If the invoice contains BOTH Equipment/Machinery Rental AND a Driver/Operator fee, you MUST split them into exactly TWO PurchaseEntry items.
            2. DATE: Format strictly as YYYY-MM-DD.
            3. VENDOR NAME: Extract the company/shop name.
            4. DESCRIPTION_EN: Summarize in English ONLY. Max 25 words!
            5. TAX AMOUNTS: Map to unreg_usd, exempt_usd, vat_base_usd, or vat_usd appropriately. Ensure you respect the difference between Commercial and Tax Invoices as per the MEMO.
            6. BALANCED ASSIGNMENT (ACCOUNT IDS): Assign ALL appropriate account IDs strictly from the <CHART_OF_ACCOUNTS>. 
               - The Main Credit Account is typically Trade Payable.
               - FIRST, apply any explicit mappings from [INDUSTRY LEVEL] RULES or [COMPANY LEVEL] MEMOS.
               - IF NO RULE APPLIES, you MUST dynamically analyze the transaction description and select the single most accurate Account IDs from the <CHART_OF_ACCOUNTS>.
            7. INVOICE & PAGE NUMBERS: STRICTLY FOLLOW any custom starting 'invoice_no' or 'page' provided in the [BATCH LEVEL], [INDUSTRY LEVEL], or [COMPANY LEVEL] instructions. If not provided, output the invoice number exactly as printed, or 'NEEDS_SEQ' if missing/short.
            </OUTPUT_INSTRUCTIONS>
            
            DOUBLE-CHECK PROTOCOL: Fill out 'self_verification_step' first.
            """
            
            a_resp = self.client.models.generate_content(
                model=self.AUDIT_MODEL,
                contents=[f, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AccountingBatch,
                    temperature=0.0 
                )
            )
            
            page_cost = self.calculate_cost(a_resp.usage_metadata, self.AUDIT_MODEL)
            with self.cost_lock: self.cost_stats["pro_cost"] += page_cost
            
            audit = a_resp.parsed
            
            if audit.purchase_entries:
                # ==========================================================
                # PHASE 5: DETERMINISTIC PYTHON POST-PROCESSING
                # ==========================================================
                first_entry_inv = str(audit.purchase_entries[0].invoice_no).strip()
                
                # Rule: Check length (< 7 characters requires dynamic generation)
                if first_entry_inv == "NEEDS_SEQ" or first_entry_inv.upper() == "UNKNOWN" or len(first_entry_inv) < 7:
                    base_inv_no = f"INV-{date_prefix}-{current_invoice_seq}"
                    current_invoice_seq += 1 
                else:
                    base_inv_no = first_entry_inv

                is_split_invoice = len(audit.purchase_entries) > 1

                for idx, entry in enumerate(audit.purchase_entries, 1):
                    entry_dict = entry.model_dump()
                    
                    if is_split_invoice:
                        entry_dict['invoice_no'] = f"{base_inv_no}-{idx}"
                    else:
                        entry_dict['invoice_no'] = base_inv_no
                    
                    if 'vendor_name' in entry_dict:
                        entry_dict['company'] = entry_dict.pop('vendor_name')
                    
                    reasoning = entry_dict.pop('account_reasoning', '')
                    entry_dict['instruction'] = f"AI Reason: {reasoning}" if reasoning else ""
                    
                    vendor_data = self.resolve_and_assign_vendor(
                        entry_dict.get('company', ''), 
                        entry_dict.get('vattin', ''), 
                        entry_dict.get('vat_usd', 0.0), 
                        client_id
                    )
                    if not vendor_data:
                        vendor_data = {'db_id': None, 'is_new': False, 'temp_vid': None, 'temp_id': None}
                    
                    entry_dict['vendor_db_id'] = vendor_data.get('db_id')
                    entry_dict['is_new_vendor'] = vendor_data.get('is_new', False)
                    entry_dict['temp_vid'] = vendor_data.get('temp_vid')
                    entry_dict['temp_id'] = vendor_data.get('temp_id')
                    entry_dict['vendor_choice'] = vendor_data.get('temp_id') if vendor_data.get('is_new') else vendor_data.get('db_id')
                    entry_dict['batch'] = batch_name
                    entry_dict['page'] = entry_dict.get('page') or pg
                    
                    ledgers.append(entry_dict)

        except Exception as e:
            print(f"❌ [Page {pg}] processing failed: {e}")
            return [], page_cost, current_invoice_seq, str(e)

        return ledgers, page_cost, current_invoice_seq, None

# ====================================================================
# --- PYDANTIC SCHEMAS FOR HISTORICAL DATA MIGRATION ---
# ====================================================================
class HistoricalLine(BaseModel):
    # Relaxed strictness: using defaults prevents Pydantic ValidationErrors when Excel columns are empty
    gl_no: str = Field(default="UNGROUPED", description="The ID or Voucher No. from the original file. Use 'UNGROUPED' if none exists.")
    date: str = Field(default="", description="Transaction Date in YYYY-MM-DD format. Leave empty if missing.")
    account_id: str = Field(..., description="The matched 6-digit GL Account code from the TIER_1 Chart of Accounts.")
    description: str = Field(default="Historical Entry", description="Combined entity name and transaction description.")
    instruction: str = Field(default="Standard mapping applied.", description="Brief AI reasoning for why this specific account_id was chosen.")
    debit: float = Field(default=0.0, description="Debit amount (0 if empty).")
    credit: float = Field(default=0.0, description="Credit amount (0 if empty).")

class HistoricalBatch(BaseModel):
    lines: List[HistoricalLine]

# ====================================================================
# --- THE MIGRATION ENGINE (DB-BACKED 3-TIER ARCHITECTURE) ---
# ====================================================================
class GLMigrationProcessor:
    def __init__(self, api_key, client_id):
        print("\n" + "="*50)
        print("🔄 INITIALIZING: HISTORICAL GL STAGING ENGINE")
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
        # Ensure Account and AccountMappingRule are imported at the top of your file
        from .models import Account, AccountMappingRule 
        try:
            accounts = Account.objects.filter(client_id=client_id)
            rules = AccountMappingRule.objects.filter(client_id=client_id).select_related('account')
            rule_dict = {str(rule.account.account_id): rule for rule in rules}

            self.tier_1_prompt = "<TIER_1_CHART_OF_ACCOUNTS>\n"
            for acc in accounts:
                acc_id = str(acc.account_id)
                acc_name = acc.name
                rule = rule_dict.get(acc_id)
                keywords = rule.trigger_keywords if rule else "None specified"
                guideline = rule.ai_guideline if rule else "Apply standard corporate accounting principles."

                self.tier_1_prompt += f"ID: {acc_id} | Name: {acc_name} | Keywords: {keywords} | Rules: {guideline}\n"
            self.tier_1_prompt += "</TIER_1_CHART_OF_ACCOUNTS>\n"

            print(f"📊 Loaded Tier-1 CoA from DB: {accounts.count()} Accounts recognized.")
        except Exception as e:
            raise ValueError(f"CRITICAL: Failed to load Chart of Accounts from Database: {str(e)}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _parse_cluster(self, cluster_text):
        """Sends a chunk of raw GL lines to Gemini for cleaning and account mapping."""
        
        tier_2 = """
        <TIER_2_MIGRATION_RULES>
        Your objective is to clean, format, and map these historical ledger lines.
        1. Read the original 'Account', 'Reference' and 'Description' columns.
        2. Cross-reference them with the <TIER_1_CHART_OF_ACCOUNTS> to find the precise 6-digit `account_id`.
        3. Combine the 'Vendor/Customer', 'Reference' (which contains commercial and tax invoice) and 'Description' into a single, clean `description` string.
        4. Maintain the exact original Debit and Credit values.
        5. If a column is blank or null, output empty strings or 0.0 as defined in the schema. Do not crash.
        </TIER_2_MIGRATION_RULES>
        """

        prompt = f"""
        {self.tier_1_prompt}
        {tier_2}
        <TIER_3_GL_CHUNK>\n{cluster_text}\n</TIER_3_GL_CHUNK>
        """

        response = self.client.models.generate_content(
            model=self.MODEL_NAME, 
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=HistoricalBatch, temperature=0.0)
        )
        
        cost = self.calculate_cost(response.usage_metadata, self.MODEL_NAME)
        self.cost_stats["pro_cost"] += cost
        print(f"💲 GL Chunk AI Mapping Cost: ${cost:.5f}")
        
        return response.parsed

    def process_migration_file(self, file_path):
        """Processes the GL file in balanced chunks (Grouped by ID)."""
        try:
            if file_path.endswith('.csv'): df = pd.read_csv(file_path)
            else: df = pd.read_excel(file_path)
            df = df.replace({np.nan: None})
        except Exception as e:
            raise ValueError(f"Could not read GL file: {str(e)}")

        results = []
        
        # Group by ID (Voucher/Transaction Number) so we process complete, balanced Double-Entries together
        group_cols = ['ID'] if 'ID' in df.columns else ['Date', 'Vendor / Customer / Employee']
        
        # Fallback if standard grouping columns are missing (e.g., Opening Balance file)
        missing_cols = [col for col in group_cols if col not in df.columns]
        if missing_cols:
            print(f"⚠️ Warning: Missing expected grouping columns {missing_cols}. Grouping by index instead.")
            df['__temp_id__'] = df.index // 5  # Group every 5 rows together
            group_cols = ['__temp_id__']
            
        grouped = df.groupby(group_cols, dropna=False, sort=False)

        # Batch processing to optimize token usage
        current_chunk_text = ""
        current_chunk_size = 0
        
        for keys, group in grouped:
            # Prevent passing completely blank rows
            if group.replace('', np.nan).dropna(how='all').empty:
                continue
                
            current_chunk_text += group.to_string(index=False) + "\n---\n"
            current_chunk_size += len(group)
            
            # Send to AI every ~15 rows to maintain high accuracy
            if current_chunk_size >= 15:
                try:
                    parsed_batch = self._parse_cluster(current_chunk_text)
                    results.extend([line.model_dump() for line in parsed_batch.lines])
                except Exception as e:
                    print(f"❌ Failed to parse GL chunk: {e}")
                
                # Reset chunk
                current_chunk_text = ""
                current_chunk_size = 0

        # Process any remaining lines
        if current_chunk_size > 0:
            try:
                parsed_batch = self._parse_cluster(current_chunk_text)
                results.extend([line.model_dump() for line in parsed_batch.lines])
            except Exception as e:
                print(f"❌ Failed to parse final GL chunk: {e}")

        total_cost = self.cost_stats['flash_cost'] + self.cost_stats['pro_cost']
        print(f"💰 Total Staging AI Cost: ${total_cost:.5f}")
        
        return results, self.cost_stats
        
# --------------------------------------------------------------------
# 1. DEFINE THE STRICT DATA SCHEMA (Pydantic)
# --------------------------------------------------------------------
class ProposalData(BaseModel):
    proposal_date: str = Field(description="The date of the proposal strictly formatted as 'DD-MMM-YY'. E.g., '21-Mar-26'.")
    proposal_number: str = Field(description="The unique proposal code ONLY. Remove 'Proposal code:'. E.g., 'AC-2026-0014'.")
    company_name: str = Field(description="The exact name of the client company receiving the proposal in English.")
    
    # Updated to explicitly exclude reimbursements and support bundling
    service_proposed: str = Field(description="A numbered list of professional services offered, simplified into brief action phrases in English, separated by newlines. NEVER include reimbursements.")
    fee_detail: str = Field(description="A numbered list of the proposed fees. Replace 'USD' with '$' and 'per' with '/'. E.g., '$75/year'. Must correspond exactly line-by-line to the services, separated by newlines.")

# --------------------------------------------------------------------
# 2. THE PROCESSOR CLASS
# --------------------------------------------------------------------
class ProposalPDFProcessor:
    def __init__(self, api_key: str):
        """Initialize the Gemini client using the unified SDK."""
        self.client = genai.Client(api_key=api_key)
        # Using gemini-2.5-flash as it excels at fast, structured document extraction
        self.model_name = 'gemini-2.5-flash'
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def _calculate_cost(self, usage):
        """Calculates the cost of a Gemini API call."""
        if usage:
            return ((usage.prompt_token_count / 1e6) * 0.10) + ((usage.candidates_token_count / 1e6) * 0.40)
        return 0.0

    def extract_proposal_data(self, pdf_bytes: bytes) -> dict:
        """
        Reads PDF bytes, prompts Gemini with detailed examples handling 
        bundled services and ignored reimbursements, and returns a strictly formatted dictionary.
        """
        document_part = types.Part.from_bytes(
            data=pdf_bytes,
            mime_type="application/pdf"
        )

        # --------------------------------------------------------------------
        # THE ENHANCED PROMPT (Few-Shot Prompting with Strict Formatting Rules)
        # --------------------------------------------------------------------
        
        proposal_rule = '3. Proposal Number: Extract the specific value printed in document. Strip out prefixes like "Proposal code:", "Ref:" or "Our ref:" (e.g. If "Our ref: AC-2026-0026-D016" was found in the document, you must formulate the proposal_number as "AC-2026-0026-D016").'

        prompt = f"""
        You are an expert financial auditor, administrative assistant, and data extractor. 
        Your task is to carefully read the attached Client Service Proposal 
        and extract key business information into a highly structured format.

        CRITICAL INSTRUCTIONS & FORMATTING RULES:
        1. English Only: Extract and parse English information ONLY. Ignore Khmer, Chinese, or any other translations.
        2. Proposal Date: Convert all dates strictly to the 'DD-MMM-YY' format (e.g., "21st March 2026" becomes "21-Mar-26").
        {proposal_rule}
        
        4. EXCLUDE REIMBURSEMENTS (CRITICAL): You MUST completely ignore any items labeled as "Reimbursement", "out-of-pocket expenses", or "government official fees". These are NOT firm revenues and must NEVER appear in your services or fees lists.
        
        5. Services (Numbered List & Bundling): Extract professional services as a numbered list, separated by newlines (\n). Simplify the descriptions capturing only the core action, target, and year.
           - BUNDLED SERVICES: If multiple services (e.g., Part A and Part B) are grouped together and billed under a SINGLE combined fee, you MUST combine them into a single line item (e.g., "1. Service A AND Service B"). Do not separate them into multiple lines, as that will cause the fee to duplicate.
           
        6. Fee Formatting ($ and /): Extract the fee amount, but you MUST replace the word 'USD' with '$', and replace 'per' or '/ per' with '/'. (e.g., "USD 660 per year" becomes "$660/year", and "USD 75 / per year" becomes "$75/year"). Ignore KHR amounts.
        
        7. Alignment & Integrity: Ensure that the numbering in 'service_proposed' perfectly matches the numbering in 'fee_detail'. Double-check that all fees actually contain the monetary value. NEVER return an empty number like "1." without the corresponding fee.
        8. Line Breaks: You MUST separate different services and different fees using newlines (\n). Do not combine them into a single paragraph.
        9. Missing Data: If a piece of information is missing entirely, return an empty string ("").

        --- EXAMPLES OF EXPECTED FORMATTING ---
        
        Example 1 (Standard Format & Bundled Services):
        (Scenario: Document lists 'Open ACAR' and 'Fulfill ACAR' grouped together for a single fee of $550/year. Tax compliance is listed separately for $660/year.)
        - proposal_date: "11-Mar-26"
        - proposal_number: "AC-2026-0014"
        - company_name: "SUREWIN WORLDWIDE LIMITED (CAMBODIA) CO., LTD."
        - service_proposed: "1. Open ACAR AND Fulfill ACAR Requirement for FY2024\n2. Tax Compliance"
        - fee_detail: "1. $550/year\n2. $660/year"

        Example 2 (Prefix Stripping, $ and / conversion, Ignored Reimbursements):
        (Scenario: Document lists Bookkeeping, ACAR Registration, and a Reimbursement for a government fee of $300)
        - proposal_date: "21-Mar-26"
        - proposal_number: "AC-2026-0013"
        - company_name: "SUNWAY SOTHEAROS CO., LTD."
        - service_proposed: "1. Monthly Bookkeeping service\n2. ACAR Registration"
        - fee_detail: "1. $440 (set up)\n$330/month\n2. $75/year"

        Now, read the attached document and extract the required fields using the exact same formatting principles shown above.
        """

        # Enforce the Pydantic schema so we are guaranteed a perfect JSON structure
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ProposalData,
            temperature=0.0 # Strict accuracy, zero creativity or hallucination
        )

        try:
            # Send to Gemini
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            
            # Cost Calculation
            cost = self._calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost # It's a flash model
            
            # The response.text is guaranteed to be a JSON string matching our schema
            data = json.loads(response.text)
            
            return data
            
        except Exception as e:
            print(f"AI Extraction Error: {str(e)}")
            # Return empty defaults if the AI fails, preventing the view from crashing
            return {
                "proposal_date": "", "proposal_number": "", 
                "company_name": "ERROR PARSING FILE", 
                "service_proposed": "", "fee_detail": ""
            }

# --------------------------------------------------------------------
# 1. DEFINE THE STRICT DATA SCHEMA (Pydantic)
# --------------------------------------------------------------------
class EngagementData(BaseModel):
    el_date: str = Field(description="The date of the engagement letter strictly formatted as 'DD-MMM-YY'. E.g., '31-Mar-26'.")
    el_number: str = Field(description="The unique engagement letter reference ONLY. Remove 'Our ref:'. E.g., 'AC-2026-0024-P001'.")
    company_name: str = Field(description="The exact legal name of the client company ONLY. Exclude 'The Management', 'Attn:', and all address lines.")
    
    # Separated line-by-line schema
    type_of_services: str = Field(description="A numbered list of the services offered, separated by newlines.")
    total_fee_inclusive: str = Field(description="A numbered list of fees INCLUDING 10% VAT, matching line-by-line with the services.")
    total_fee_exclusive: str = Field(description="A numbered list of fees EXCLUDING 10% VAT, matching line-by-line with the services.")

# --------------------------------------------------------------------
# 2. THE ENGAGEMENT LETTER PROCESSOR
# --------------------------------------------------------------------
class EngagementLetterProcessor:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def _calculate_cost(self, usage):
        if usage:
            return ((usage.prompt_token_count / 1e6) * 0.10) + ((usage.candidates_token_count / 1e6) * 0.40)
        return 0.0

    def extract_el_data(self, pdf_bytes: bytes) -> dict:
        """Extracts Engagement Letter data ensuring bundled services and ignored reimbursements."""
        document_part = types.Part.from_bytes(
            data=pdf_bytes,
            mime_type="application/pdf"
        )
        
        prompt = """
        You are an expert financial auditor, administrative assistant, and data extractor. 
        Your task is to carefully read the attached Engagement Letter and extract key business information.

        CRITICAL INSTRUCTIONS & FORMATTING RULES:
        1. English Only: Extract and parse English information ONLY. Ignore Chinese or Khmer translations.
        2. EL Date: Convert the engagement letter date strictly to the 'DD-MMM-YY' format.
        3. EL Number: Extract the specific reference value. Strip prefixes like "Ref:" or "Our ref:".
        4. Company Name: Extract ONLY the legal company name. NEVER include introductory words like 'The Management', 'Attn:', or the address lines.
        
        5. EXCLUDE REIMBURSEMENTS (CRITICAL): You MUST completely ignore any items labeled as "Reimbursement", "out-of-pocket expenses", or "government official fees". These are NOT firm revenues and must NEVER appear in your services or fees lists.
        
        6. Services (Numbered List & Bundling): Extract professional services as a numbered list, separated by newlines (\n). Simplify the descriptions.
           - BUNDLED SERVICES: If multiple services (e.g., Part A and Part B) are grouped together and billed under a SINGLE combined fee, you MUST combine them into a single line item (e.g., "1. Service A AND Service B"). Do not separate them into multiple lines, as that will cause the fee to duplicate.
        
        7. Fees (LINE-BY-LINE FORMAT - DO NOT SUM): You MUST NOT sum the fees into a grand total. Instead, extract the specific fee for EACH service line-by-line, perfectly matching the numbering of the services list. 
           - Provide BOTH the "10% VAT inclusive" list and "10% VAT exclusive" list.
           - If only one is stated, mathematically calculate the other (VAT is 10%). (e.g., If Inclusive is $880, Exclusive is $800).
           - Replace 'USD' with '$' and 'per' with '/'.
           - CRITICAL: You MUST include the period/frequency exactly as stated (e.g., "for FY 2021", "for FY 2022 to 2024", "/year").

        --- EXAMPLES OF EXPECTED FORMATTING ---
        
        Example 1 (Bundled Services & Ignored Reimbursements):
        (Scenario: Document lists Service A and Service B for a combined USD 880/year inclusive of VAT, plus a reimbursement of KHR 200,000)
        - type_of_services: "1. Submission of English Notification AND Unaudited Financial Statement"
        - total_fee_inclusive: "1. $880/year"
        - total_fee_exclusive: "1. $800/year"

        Example 2 (Line-by-Line Breakdown):
        (Scenario: Service 1 is $660 for FY2021. Service 2 is $330/year for FY2021 to 2024. VAT is exclusive.)
        - type_of_services: "1. Preparation and submission of protest letter to ACAR\n2. Submission of Audited Financial Statement"
        - total_fee_inclusive: "1. $726 for FY 2021\n2. $363/year for FY 2021 to 2024"
        - total_fee_exclusive: "1. $660 for FY 2021\n2. $330/year for FY 2021 to 2024"

        Now, read the attached document and extract the fields using these exact formatting principles.
        """

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EngagementData,
            temperature=0.0 # Strict accuracy, zero hallucination
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            
            cost = self._calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost 
            
            return json.loads(response.text)
            
        except Exception as e:
            print(f"AI Extraction Error: {str(e)}")
            return {
                "el_date": "", "el_number": "", "company_name": "", 
                "type_of_services": "", "total_fee_inclusive": "", "total_fee_exclusive": ""
            }

# ====================================================================
# SCHEMA 1: Tax on Salary (TOS)
# ====================================================================
class TaxOnSalaryData(BaseModel):
    exchange_rate: float = Field(description="Official exchange rate. (e.g., 4050.0)")
    net_salary_usd: float = Field(description="Total Net Salary. Clean float (e.g., 7443.0).")
    tos_tax_resident_khr: float = Field(description="Total Tax on Salary (Resident) in KHR. Strip commas.")
    tos_tax_non_resident_khr: float = Field(description="Total Tax on Salary (Non-resident) in KHR. Strip commas.")
    reasoning: str = Field(description="Explanation of the tax calculation, stating the exchange rate found and how it applies.")

class TOSPDFProcessor:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def _calculate_cost(self, usage):
        """Calculates the cost of a Gemini API call based on token usage."""
        # Input: $0.075 / 1M tokens, Output: $0.30 / 1M tokens (Updated Flash pricing)
        if usage:
            return ((usage.prompt_token_count / 1e6) * 0.075) + ((usage.candidates_token_count / 1e6) * 0.30)
        return 0.0

    def extract_tax_data(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert tax accountant. Read the attached Tax on Salary (TOS) declaration.
        CRITICAL INSTRUCTIONS (EXHAUSTIVE EXTRACTION):
        1. Exhaustive Extraction: Scan the ENTIRE document to find tax amounts for Residents and Non-Residents.
        2. Float Formatting: Return purely numeric floats without commas or currency symbols.
        3. Exchange Rate: Locate the official exchange rate.
        4. Missing Data: If a tax doesn't exist, return 0.0.
        """
        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=TaxOnSalaryData, temperature=0.0)
        try:
            response = self.client.models.generate_content(model=self.model_name, contents=[prompt, document_part], config=config)
            
            # Cost Tracking
            cost = self._calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost
            
            return json.loads(response.text)
        except Exception as e:
            print(f"TOS AI Error: {str(e)}")
            return {"error": True}

# ====================================================================
# SCHEMA 2: Tax Liabilities (WHT & FBT)
# ====================================================================
class TaxLiabilitiesData(BaseModel):
    fbt_usd: float = Field(description="Total 20% Fringe Benefit Tax in USD. Clean float (e.g., 65.82). Return 0.0 if missing.")
    wht_10_usd: float = Field(description="Total 10% Withholding Tax in USD. Clean float. Return 0.0 if missing.")
    wht_15_usd: float = Field(description="Total 15% Withholding Tax in USD. Clean float. Return 0.0 if missing.")
    reasoning: str = Field(description="Brief explanation of the extracted taxes.")

class TaxLiabilitiesProcessor:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def _calculate_cost(self, usage):
        if usage:
            return ((usage.prompt_token_count / 1e6) * 0.075) + ((usage.candidates_token_count / 1e6) * 0.30)
        return 0.0

    def extract_liabilities_data(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert tax accountant. Read the attached Tax Liabilities Declaration.
        CRITICAL INSTRUCTIONS (EXHAUSTIVE EXTRACTION):
        1. Exhaustive Extraction: Scan the ENTIRE document. Find Fringe Benefit Tax and Withholding Taxes (10% and 15%).
        2. Float Formatting: Return purely numeric floats in USD ONLY. Remove commas/symbols.
        3. Missing Data: If a tax category does not exist, return 0.0.
        """
        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=TaxLiabilitiesData, temperature=0.0)
        try:
            response = self.client.models.generate_content(model=self.model_name, contents=[prompt, document_part], config=config)
            
            # Cost Tracking
            cost = self._calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost
            
            return json.loads(response.text)
        except Exception as e:
            print(f"Liabilities AI Error: {str(e)}")
            return {"error": True}

# ====================================================================
# UNIFIED SCHEMA: Tax on Salary, FBT, and WHT
# ====================================================================
class UnifiedTaxData(BaseModel):
    exchange_rate: float = Field(description="Official exchange rate. (e.g., 4000.0)")
    net_salary_usd: float = Field(default=0.0, description="Total Net Salary base in USD if present on the document. Return 0.0 if not found.")
    tos_resident_usd: float = Field(default=0.0, description="Total Tax on Salary (Resident) in USD. Clean float.")
    tos_non_resident_usd: float = Field(default=0.0, description="Total Tax on Salary (Non-resident) in USD. Clean float.")
    fbt_usd: float = Field(default=0.0, description="Total 20% Fringe Benefit Tax in USD. Clean float.")
    wht_10_usd: float = Field(default=0.0, description="Total 10% Withholding Tax in USD. Clean float.")
    wht_15_usd: float = Field(default=0.0, description="Total 15% Withholding Tax in USD. Clean float.")
    staff_meals_usd: float = Field(default=0.0, description="Total Staff meals in USD if present on the document. Return 0.0 if not found.")
    tos_instruction: str = Field(default="", description="Specific explanation for Tax on Salary (e.g., 'TOS Resident: 204.03 USD. Rate: 3988').")
    fbt_instruction: str = Field(default="", description="Specific explanation for Fringe Benefit Tax.")
    wht_instruction: str = Field(default="", description="Specific explanation for Withholding Tax, including the nature (e.g., '10% WHT for Rental, 15% for Services').")
    general_instruction: str = Field(default="", description="Explanation for Net Salary or Staff Meals.")

class UnifiedTaxProcessor:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def _calculate_cost(self, usage):
        """Calculates the cost of a Gemini API call based on token usage."""
        if usage:
            return ((usage.prompt_token_count / 1e6) * 0.075) + ((usage.candidates_token_count / 1e6) * 0.30)
        return 0.0

    def extract_tax_data(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert tax accountant. Read the attached Notification of Tax Declaration.
        CRITICAL INSTRUCTIONS (EXHAUSTIVE EXTRACTION):
        1. Unified Extraction: Scan the table to find Tax on Salary (Resident & Non-Resident), Fringe Benefit Tax, and Withholding Taxes (10% and 15%).
        2. Extract USD Values: Locate the USD column and extract the USD float values directly. If only KHR is available, divide by the exchange rate to get USD.
        3. Float Formatting: Return purely numeric floats without commas or currency symbols.
        4. Missing Data: If a specific tax category does not exist, return 0.0.
        5. Staff Meals: Extract Staff meals if available on the document.
        6. Explanations: Provide distinct, specific explanations for TOS, WHT, FBT, and a General note for Salary/Meals. Include the exchange rate and the exact nature of the tax (e.g. 'WHT 10% for Rental').
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json", 
            response_schema=UnifiedTaxData, 
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name, 
                contents=[prompt, document_part], 
                config=config
            )
            
            # Cost Tracking
            cost = self._calculate_cost(response.usage_metadata)
            self.cost_stats["flash_cost"] += cost
            
            return json.loads(response.text)
        except Exception as e:
            print(f"Unified Tax AI Error: {str(e)}")
            return {"error": True}
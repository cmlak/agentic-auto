import os
import time
from pypdf import PdfReader
from pydantic import BaseModel, Field, field_validator
from typing import List
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

class BankTransaction(BaseModel):
    sys_id: str = Field(..., description="Sequential ID (e.g., 2025-01-001).")
    bank_ref_id: str = Field(..., description="The unique Bank Reference Number.")
    tr_date: str = Field(..., description="Transaction date in YYYY-MM-DD format.")
    trans_type: str = Field(..., description="The main transaction type.")
    counterparty: str = Field(..., description="The counterparty name, if available.")
    purpose: str = Field(..., description="The text of the transaction details.")
    remark: str = Field(..., description="The specific digit-hyphen-digit pattern.")
    raw_remark: str = Field(..., description="The full text following 'REMARK:'.") 
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
                # Default values in case date is missing or malformed
                year, month = "2025", "01"

                date_source = None
                if isinstance(transaction, dict):
                    date_source = transaction.get('tr_date')
                elif hasattr(transaction, 'tr_date'):
                    date_source = getattr(transaction, 'tr_date', None)

                # Safely parse year and month from 'YYYY-MM-DD' format
                if date_source and isinstance(date_source, str) and date_source.count('-') == 2:
                    try:
                        parts = date_source.split('-')
                        year = parts[0]
                        month = parts[1]
                    except (IndexError, ValueError):
                        # Keep default values if date string is invalid
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
        extraction_prompt = "Extract all bank transactions from the text below.\nStrictly follow the JSON schema provided.\n"
        if custom_prompt: extraction_prompt += f"\nADDITIONAL INSTRUCTIONS:\n{custom_prompt}\n"
        extraction_prompt += f"\nDATA:\n---\n{raw_text}\n---\n"

        try:
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=extraction_prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=BankInfo, temperature=0.0)
            )
            self.cost_stats["flash_cost"] += self.calculate_cost(response.usage_metadata)
            
            structured_data = response.parsed
            if not structured_data: raise ValueError("Model returned unparseable schema.")
                
            transactions = [t.model_dump() for t in structured_data.transactions]
            for t in transactions:
                t['batch'] = batch_name
                t['date'] = t.pop('tr_date', None) 
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

###

class CashStandardExcelProcessor:
    """Strategy Processor for parsing structured Cash Book Excel/CSV files."""
    
    def __init__(self, api_key=None):
        print("\n" + "="*50)
        print("💵 INITIALIZING: CASH BOOK EXCEL PROCESSOR")
        print("="*50)
        # Note: We don't strictly need Gemini API here since the data is already tabular,
        # but we accept the api_key to strictly match the Strategy Pattern signature.
        self.vendor_lock = threading.Lock()
        self.batch_new_vendors = {}
        self.cost_stats = {"flash_cost": 0.0, "pro_cost": 0.0}

    def resolve_and_assign_vendor(self, raw_name, client_id):
        """Matches vendor strictly within the selected client's isolated database."""
        general_vendor, _ = Vendor.objects.get_or_create(
            client_id=client_id,
            vendor_id='V001', 
            defaults={'name': 'General Vendor', 'normalized_name': 'general vendor'}
        )
        
        if not raw_name or str(raw_name).lower() in ['nan', 'none', 'unknown', '']:
            return {'db_id': general_vendor.id, 'is_new': False, 'temp_vid': None}

        name_str = str(raw_name).lower().replace('&', ' and ')
        target_norm = re.sub(r'[\W_]+', ' ', name_str).strip()

        # 1. Exact Match
        exact_match = Vendor.objects.filter(client_id=client_id, normalized_name=target_norm).first()
        if exact_match:
            return {'db_id': exact_match.id, 'is_new': False, 'temp_vid': None}

        # 2. Fuzzy Match
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

        # 3. New Vendor Cache
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

    def process(self, file_path, client_id, batch_name="", custom_prompt=""):
        print(f"📄 Reading Tabular File natively: {os.path.basename(file_path)}...")
        
        try:
            # Handle both CSV and Excel seamlessly
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
                
            # Clean NaNs to prevent JSON serialization errors later
            df = df.replace({np.nan: None})
            
        except Exception as e:
            print(f"❌ File Reading Error: {str(e)}")
            raise ValueError(f"Could not read the file. Please ensure it is a valid CSV or Excel file. Details: {str(e)}")

        ledgers = []
        
        # Standardize column mapping based on your sample CSV
        for index, row in df.iterrows():
            raw_vendor = str(row.get('vendor', '')).strip() if row.get('vendor') else ''
            vendor_data = self.resolve_and_assign_vendor(raw_vendor, client_id)
            
            # Safely handle dates
            raw_date = row.get('date')
            clean_date = str(raw_date)[:10] if raw_date else None # Keep YYYY-MM-DD
            
            entry_dict = {
                'batch': batch_name,
                'date': clean_date,
                'voucher_no': row.get('voucher_no', ''),
                'description': row.get('description', ''),
                
                # Vendor Mappings
                'company': raw_vendor,
                'vendor_db_id': vendor_data['db_id'],
                'is_new_vendor': vendor_data['is_new'],
                'temp_vid': vendor_data['temp_vid'],
                'temp_id': vendor_data.get('temp_id'),
                'vendor_choice': vendor_data['temp_id'] if vendor_data['is_new'] else vendor_data['db_id'],
                
                'invoice_no': row.get('invoice_no', ''),
                'debit': float(row.get('debit', 0.0) or 0.0),
                'credit': float(row.get('credit', 0.0) or 0.0),
                'balance': float(row.get('balance', 0.0) or 0.0),
                'note': row.get('note', ''),
            }
            ledgers.append(entry_dict)

        print(f"🎉 SUCCESS: Parsed {len(ledgers)} cash rows.")
        return ledgers, 1, self.cost_stats # Returns ledgers, total_pages (1 for Excel), costs
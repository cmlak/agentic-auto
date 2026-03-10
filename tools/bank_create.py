import os
import logging
import pandas as pd
from pypdf import PdfReader
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
from google import genai
from google.genai import types

# --- 1. Configuration and Setup ---
load_dotenv()

# FIX 1: Use the correct variable name from your .env file
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_2")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# --- 2. Define the Pydantic Data Models ---

class Transaction(BaseModel):
    """Represents a single bank transaction with structured details."""
    sys_id: str = Field(..., description="Sequential ID (e.g., 2025-01-001).")
    bank_ref_id: str = Field(..., description=r"The unique Bank Reference Number.")
    tr_date: str = Field(..., description="Transaction date in YYYY-MM-DD format.")
    trans_type: str = Field(..., description="The main transaction type.")
    counterparty: str = Field(..., description="The counterparty name, if available.")
    purpose: str = Field(..., description="The text of the transaction details.")
    remark: str = Field(..., description="The specific digit-hyphen-digit pattern (e.g., 1-124).")
    raw_remark: str = Field(..., description="The full text following 'REMARK:'.") 
    debit: float = Field(..., description="Money In (0.0 if empty).")
    credit: float = Field(..., description="Money Out (0.0 if empty).")
    balance: float = Field(..., description="Balance column.")

class BankInfo(BaseModel):
    """Container for the list of all parsed bank transactions."""
    transactions: list[Transaction]
    
    @field_validator('transactions', mode='before') 
    @classmethod
    def set_sys_id(cls, v):
        if v is not None:
            for i, transaction in enumerate(v):
                if isinstance(transaction, dict):
                    transaction['sys_id'] = f"2025-01-{i+1:03d}" 
                elif hasattr(transaction, 'sys_id'):
                    transaction.sys_id = f"2025-01-{i+1:03d}"
        return v

# --- 3. Core Integrated Function ---

def pdf_to_structured_csv(
    pdf_file_path: str, 
    output_csv_filename: str,
    gemini_client: genai.Client,
    output_model: type[BaseModel]
) -> str:
    if not os.path.exists(pdf_file_path):
        raise FileNotFoundError(f"File not found: {pdf_file_path}")

    # Step 1: Extract Text
    try:
        reader = PdfReader(pdf_file_path)
        raw_text = ""
        for page in reader.pages:
            raw_text += page.extract_text(extraction_strategy="layout") + "\n"
        
        if len(raw_text) < 100:
            return "Extraction failed: Not enough content."
        logger.info(f"Successfully extracted {len(raw_text)} characters from PDF.")

    except Exception as e:
        logger.exception(f"Error extracting text from PDF")
        raise RuntimeError(f"PDF Error: {str(e)}")

    # Step 2: Gemini Parsing
    try:
        extraction_prompt = f"""
        Extract all bank transactions from the text below.
        Strictly follow the JSON schema.
        
        DATA:
        ---
        {raw_text}
        ---
        """
        
        logger.info("Sending request to Gemini (Model: gemini-2.5-flash)...")
        
        # FIX 3: Use a model explicitly found in your 'check_models.py' list
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=extraction_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=output_model,
                temperature=0.0
            )
        )

        structured_data = response.parsed
        # If parsing fails, log the raw response text for debugging
        if not structured_data:
            error_message = "Model returned a response, but it could not be parsed into the specified Pydantic schema."
            # Log the raw text for inspection
            logger.error(f"RAW MODEL RESPONSE:\n---\n{response.text}\n---")
            raise ValueError(error_message)
             
        logger.info(f"Successfully parsed {len(structured_data.transactions)} transactions.")

    except Exception as e:
        logger.exception("Error generating structured data with Gemini API.")
        raise RuntimeError(f"AI Error: {str(e)}")
        
    # Step 3: Save CSV
    try:
        transactions_list = structured_data.model_dump()['transactions']
        df = pd.DataFrame(transactions_list)
        
        output_dir = os.path.dirname(pdf_file_path)
        output_path = os.path.join(output_dir, output_csv_filename)
        
        df.to_csv(output_path, index=False, encoding='utf-8')
        return output_path

    except Exception as e:
        raise RuntimeError(f"CSV Error: {str(e)}")

# --- 4. Main Execution ---

if __name__ == "__main__":
    
    # Check for the key variable name you actually have
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY_2 not found. Check your .env file.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Paths
    # BASE_DIR = 'D:/Baker Tilly/AllFine/04. Working/05. Python' 
    BASE_DIR = 'C:/Users/CCTimes Per-2/bakertilly/Allfine/Data/Bank Statement'
    FILE_NAME = "6. ABA_Jun.pdf"
    OUTPUT_CSV_NAME = "ABA_Jun_Structured_Data.csv"
    PDF_FILE_PATH = os.path.join(BASE_DIR, FILE_NAME) 

    try:
        final_path = pdf_to_structured_csv(
            pdf_file_path=PDF_FILE_PATH,
            output_csv_filename=OUTPUT_CSV_NAME,
            gemini_client=client,
            output_model=BankInfo
        )
        print(f"\n✅ SUCCESS: Output saved to: {final_path}")

    except Exception as e:
        print(f"\n❌ FAILURE: {str(e)}")
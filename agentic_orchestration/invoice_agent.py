import io
import re
from pypdf import PdfReader
from google.genai import types
from .base_agent import BaseAutonomousAgent
from .finance_schemas import AccountingBatch

class InvoiceAgent(BaseAutonomousAgent):
    """
    Specialized agent for extracting and mapping accounting data from invoices.
    Decoupled from Django ORM to run anywhere (Cloud Run, FastAPI, CLI).
    """
    
    def process_single_page(self, pdf_bytes: bytes, page_num: int, coa_context: str, rag_rules: str, custom_prompt: str = "") -> list:
        """Extracts data from a single PDF page and returns framework-agnostic dictionaries."""
        
        # Handle page sequence overrides dynamically
        computed_page = page_num
        page_match = re.search(r'(?:start page numbering from|start page|page number starts from|page)\s*[:=]?\s*(\d+)', custom_prompt.lower())
        if page_match:
            base_offset = int(page_match.group(1))
            computed_page = base_offset + (page_num - 1)
            
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        
        prompt = f"""
        TASK: Extract accounting data strictly from the attached invoice page.
        This is Page {computed_page} of the transaction sequence.
        <CRITICAL_VATTIN_INSTRUCTION>Extract VATTIN EXACTLY as visually printed. Do NOT autocorrect.</CRITICAL_VATTIN_INSTRUCTION>
        <CHART_OF_ACCOUNTS>\n{coa_context}\n</CHART_OF_ACCOUNTS>
        <ACCOUNTING_HIERARCHY_RULES>
        1. [BATCH LEVEL]: {custom_prompt if custom_prompt else "None"}
        2. [AGENT KNOWLEDGE BASE] RETRIEVED RULES:
        {rag_rules}
        </ACCOUNTING_HIERARCHY_RULES>
        <OUTPUT_INSTRUCTIONS>
        1. AGGREGATION & SPLITTING: Output ONE PurchaseEntry per page. EXCEPTION: Split Equipment Rental and Driver Fee into TWO entries.
        2. ACCOUNTS PAYABLE: ALL invoices must credit Trade Payable. You MUST output '200000' for the credit_account_id.
        3. MULTI-MONTH ACCRUALS (CRITICAL): If an invoice bills for past months AND the current month, map the past months' amounts to `debit_account_id_2` and `debit_amount_2`. Map the current month's expense to the main `account_id`.
        </OUTPUT_INSTRUCTIONS>
        """

        # Inherited from BaseAutonomousAgent: handles retries, token counting, and Schema validation automatically
        audit_batch: AccountingBatch = self.execute_task(
            contents=[document_part, prompt],
            response_schema=AccountingBatch
        )
        
        for entry in audit_batch.purchase_entries:
            entry.page = computed_page
            
        return [entry.model_dump() for entry in audit_batch.purchase_entries]
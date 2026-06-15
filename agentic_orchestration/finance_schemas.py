from pydantic import BaseModel, Field, model_validator
from typing import List, Literal, Optional

class PurchaseEntry(BaseModel):
    date: Optional[str] = Field(None, description="Date of the invoice (YYYY-MM-DD).")
    invoice_no: str = Field("NEEDS_SEQ", description="Extract EXACTLY as printed. If missing, output 'NEEDS_SEQ'.")
    vattin: str = Field("N/A", description="VAT Registration Number. CRITICAL: Extract EXACTLY as printed.")
    vendor_name: str = Field(..., description="Vendor name. If in Khmer or Chinese, translate to English.")
    description: str = Field(..., description="Detailed description of the items in the original language.")
    description_en: str = Field(..., description="Summarize the detailed description in English ONLY. Maximum 25 words.")
    
    account_id: Optional[str] = Field(None, description="Main Debit Account ID strictly from the Chart of Accounts. For recurring bills, use this for the CURRENT month's expense.")
    debit_account_id_2: Optional[str] = Field(None, description="Secondary Debit Account ID (e.g., 215090) used for clearing past accruals.")
    debit_amount_2: float = Field(0.0, description="Amount allocated to the secondary debit account.")
    debit_desc_2: str = Field("", description="Description of what the secondary debit covers.")
    debit_account_id_3: Optional[str] = Field(None, description="Tertiary Debit Account ID.")
    debit_amount_3: float = Field(0.0, description="Amount allocated to the tertiary debit account.")
    debit_desc_3: str = Field("", description="Description of what the tertiary debit covers.")
    
    vat_account_id: Optional[str] = Field(None, description="Debit Account ID for VAT Input. Leave null if no VAT.")
    wht_debit_account_id: Optional[str] = Field(None, description="Debit Account ID for WHT Expense. Leave null if no WHT.")    
    wht_account_id: Optional[str] = Field(None, description="Credit Account ID for WHT Payable. Leave null if no WHT.")
    credit_account_id: str = Field("200000", description="CRITICAL: MUST ALWAYS be '200000' (Trade Payable).")
    account_reasoning: str = Field("", description="Brief reason for assigning these accounts.")
    
    unreg_usd: float = Field(0.0, description="Amount for ALL non-tax invoices (no VAT is charged).")
    exempt_usd: float = Field(0.0, description="Leave as 0.0. All non-tax amounts should go to unreg_usd instead.")
    vat_base_usd: float = Field(0.0, description="The net base amount subject to 10% VAT.")
    vat_usd: float = Field(0.0, description="The 10% VAT amount.")
    total_usd: float
    page: int = Field(..., description="The physical page number.")

    @model_validator(mode='after')
    def validate_tax_integrity(self):
        if self.date and str(self.date).lower() in ["unknown", "n/a", "none"]:
            self.date = None
        if self.vat_usd == 0.0 and self.exempt_usd > 0.0:
            self.unreg_usd += self.exempt_usd
            self.exempt_usd = 0.0
        return self

class AccountingBatch(BaseModel):
    self_verification_step: str = Field(..., description="Write a short summary verifying aggregation and mapping.")
    purchase_entries: List[PurchaseEntry] = []
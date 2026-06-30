import json
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

class AssetItem(BaseModel):
    item_no: int = Field(description="Item number or line number if available", default=0)
    name: str = Field(description="Name or description of the asset/item (e.g., LOADER, FORKLIFT TRUCK)")
    cdc: str = Field(description="CDC code or number if present", default="")
    qty: float = Field(description="Quantity of the item")
    unit: str = Field(description="Unit of measurement (e.g., SET)", default="")
    unit_purchase_price: float = Field(description="Unit purchase price of the item in USD")
    amount_usd: float = Field(description="Total amount for the item in USD", default=0.0)
    gross_weight_kg: float = Field(description="Gross weight of the item in kg, if available", default=0.0)

class CommercialInvoiceData(BaseModel):
    invoice_number: str = Field(description="Commercial invoice number", default="")
    date: str = Field(description="Date of the invoice", default="")
    total_value: float = Field(description="Total value of the commercial invoice", default=0.0)
    total_gross_weight: float = Field(description="Total gross weight in kg, if available", default=0.0)
    items: list[AssetItem] = Field(description="List of items extracted from the commercial invoice")

class CustomsItem(BaseModel):
    item_no: int = Field(description="The '32 Item No' printed on the declaration", default=0)
    name: str = Field(description="Commercial description of the item")
    reasoning_for_taxes: str = Field(description="Step-by-step reasoning looking at '47 CALCUL OF TAXES' for this specific item. Explicitly identify the 'Amount' for COP, SOP, and VOP before outputting the floats.", default="")
    customs_duty_riel: float = Field(description="Customs duty (COP) amount in Riel", default=0.0)
    special_tax_riel: float = Field(description="Special Tax (SOP) amount in Riel", default=0.0)
    vat_riel: float = Field(description="Value Added Tax (VOP) amount in Riel", default=0.0)

class CustomsDeclarationData(BaseModel):
    customs_declaration_number: str = Field(description="Customs declaration number (e.g., I 79523)", default="")
    exchange_rate: float = Field(description="Exchange rate found on the declaration form (e.g., Exch. rate)", default=0.0)
    items: list[CustomsItem] = Field(description="List of items extracted from the customs declaration")

class AuxiliaryCostsData(BaseModel):
    reasoning_for_net_values: str = Field(description="Explain your step-by-step math for finding the NET amounts for EACH invoice. List the explicitly printed Subtotal/Net amounts you found on the pages. DO NOT divide gross by 1.1.", default="")
    invoice_number: str = Field(description="Invoice or receipt number (e.g., AHKW26050005, INV2026-0258, 81836)", default="")
    freight_charge_net_usd: float = Field(description="Freight charge NET amount (excluding VAT) in USD", default=0.0)
    freight_charge_gross_usd: float = Field(description="Freight charge GROSS amount (including VAT) in USD", default=0.0)
    insurance_net_usd: float = Field(description="Insurance NET amount (excluding VAT) in USD", default=0.0)
    insurance_gross_usd: float = Field(description="Insurance GROSS amount (including VAT) in USD", default=0.0)
    terminal_handling_charge_net_usd: float = Field(description="Terminal Handling Charge (THC), DOC Fee, Agency Fee, Delivery Fee NET. MUST include ALL values from 'THC and DO' invoices. Do not divide gross by 1.1. EXCLUDE PAS invoices.", default=0.0)
    terminal_handling_charge_gross_usd: float = Field(description="Terminal Handling Charge (THC), DOC Fee, Agency Fee, Delivery Fee GROSS. MUST include ALL values from 'THC and DO' invoices. EXCLUDE PAS invoices.", default=0.0)
    port_charges_net_usd: float = Field(description="Port Charges NET in USD. MUST include ALL values from Sihanoukville Autonomous Port (PAS) invoices. You MUST sum the explicitly printed Net/Subtotal values from each invoice page (do NOT divide gross by 1.1).", default=0.0)
    port_charges_gross_usd: float = Field(description="Port Charges GROSS in USD. MUST include ALL values from Sihanoukville Autonomous Port (PAS) invoices. Do not mix other invoice values here.", default=0.0)
    clearance_trucking_net_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight fees NET. DO NOT include values from PAS invoices or THC/DO invoices.", default=0.0)
    clearance_trucking_gross_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight fees GROSS. DO NOT include values from PAS invoices or THC/DO invoices.", default=0.0)
    
class ReimbursementData(BaseModel):
    invoice_number: str = Field(description="Reimbursement invoice or reference number", default="")
    total_reimbursement_usd: float = Field(description="Total reimbursement amount in USD", default=0.0)
    thc_usd: float = Field(description="THC / D.O Fee in USD", default=0.0)
    port_charges_usd: float = Field(description="Port Charges (e.g. LoLo.Port Charges) in USD", default=0.0)


class DocAgent:
    def __init__(self, api_key: str):
        print("\n" + "="*50)
        print("🚀 INITIALIZING DOC AGENT")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        
    def extract_commercial_invoice(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert data extractor. Extract the key values from the attached commercial invoice and packing list.
        Extract the invoice number, date, total value, total gross weight (usually found on the packing list, look for 'G.W. (KGS)'), and the line items.
        For each line item, extract the item number (if available), name, CDC, quantity, unit, unit purchase price, amount, and gross weight (match the items from the invoice to the packing list to find their G.W. (KGS)).
        Make sure to return the exact structure requested in the JSON schema.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CommercialInvoiceData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}

    def extract_customs_declaration(self, file_bytes: bytes, mime_type: str = "application/pdf") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = """
        You are an expert data extractor. Extract the key values from the attached customs declaration form.
        Extract the customs declaration number (found near 'Customs' or 'OFFICE OF LODGEMENT', e.g. 'I 79523').
        Extract the exchange rate (usually found under 'Exch. rate' or box 23).
        For each line item (which has a '32 Item No' and '31 DESCRIPTION OF GOODS' e.g. 'Commercial Description'), extract:
        - item_no: The number found in box 32 'Item No'.
        - name: The commercial description of the goods.
        Look closely at the '47 CALCUL OF TAXES' section which contains the taxes for each item.
        - reasoning_for_taxes: Step-by-step explanation. Write out exactly what you see in the '47 CALCUL OF TAXES' grid for this specific item number. List the rows for COP, SOP, and VOP.
        - customs_duty_riel: The amount in the 'Amount' column for Type 'COP'. If none, use 0.0.
        - special_tax_riel: The amount in the 'Amount' column for Type 'SOP'. If none, use 0.0.
        - vat_riel: The amount in the 'Amount' column for Type 'VOP'. If none, use 0.0.
        Make sure to return the exact structure requested in the JSON schema.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CustomsDeclarationData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}

    def extract_auxiliary_costs(self, file_bytes: bytes, mime_type: str = "application/pdf", filename: str = "") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = f"""
        You are an expert data extractor. Extract shipment-level costs from the attached document.
        This could be a freight invoice, a tax invoice for shipping, or a customs receipt.
        
        DOCUMENT CONTEXT: The filename of this document is "{filename}". Use this to guide your semantic classification.
        
        CRITICAL INSTRUCTION: A single attached PDF may contain MULTIPLE separate invoices or receipts across different pages. You MUST extract the data from ALL invoices in the document. DO NOT mistakenly extract only the first invoice and ignore the rest!
        
        You must act as a Semantic Classifier:
        1. Scan EVERY SINGLE PAGE of the document.
        2. Identify EVERY distinct invoice, receipt, or fee breakdown.
        3. For EACH identified fee across ALL invoices, explicitly identify its Net Amount (Subtotal excluding VAT/Tax) AND its Gross Amount (Total including VAT/Tax). If an invoice does not explicitly break down VAT, treat the stated fee as BOTH the Net and Gross amount. DO NOT skip fees.
        4. Semantically classify each fee into one of the exact categories below.
        5. SUM the Net amounts of ALL fees across ALL invoices that belong to the same semantic category, and assign the grand sum to the corresponding `_net_usd` fields below.
           -> CRITICAL RULE: NEVER calculate the Net value by dividing the Gross value by 1.1 or any other percentage! Some items (like specific PAS port charges) are exempt from VAT.
           -> Instead, you MUST locate the explicitly written 'Subtotal' or 'Amount excluding VAT' printed on EACH invoice and add those exact numbers together (e.g. 1660.28 + 99 + 40).
        6. SUM the Gross amounts of ALL fees across ALL invoices that belong to the same semantic category, and assign the grand sum to the corresponding `_gross_usd` fields below.
        
        - reasoning_for_net_values: Step-by-step explanation listing the explicitly printed Subtotal/Net amounts for each invoice. Show your math (e.g., Invoice 1 Net + Invoice 2 Net). Prove you did not just divide by 1.1!
        - invoice_number: Combine ALL invoice/receipt numbers found across the document (e.g., "INV-1, INV-2, INV-3").
        - freight_charge (Ocean/Air Freight receipts. CRITICAL: If a fee combines both Freight and Insurance into a single value, parse the ENTIRE value into the freight_charge category and assign 0 to insurance.)
        - insurance (Insurance receipts. Do not include combined freight and insurance fees here.)
        - terminal_handling_charge (Terminal Handling (THC), DOC Fee, Agency Fee, Delivery Order (D/O), EDI fee. CRITICAL: If the document filename contains 'THC and DO', you MUST classify ALL fees in this document here, including any minor trucking/clearance fees. EXCLUDE ALL charges from PAS invoices.)
        - port_charges (Port Charges, LoLo, LOLO, Wharfage, Harbour, Lift on/off, Stevedoring, Port Dues, Storage. CRITICAL: If an invoice is from Sihanoukville Autonomous Port (PAS), you MUST classify ALL of its fees here, including any port terminal handling or trucking/weighing fees. Do NOT mix values from non-port invoices here.)
        - clearance_trucking (Customs Clearance, Inland Trucking, Truck Standby, Over Weight fees, Tolls, Transport. CRITICAL: EXCLUDE ALL charges from Sihanoukville Autonomous Port (PAS) invoices. CRITICAL: If the document filename contains 'THC and DO', put those fees in terminal_handling_charge instead.)
        
        If a fee is not present on this document, use 0.0.
        Make sure to return the exact structure requested in the JSON schema.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AuxiliaryCostsData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}

    def extract_reimbursement(self, file_bytes: bytes, mime_type: str = "application/pdf") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = """
        You are an expert data extractor. Extract costs from the attached RE-IMBURSEMENT document.
        Extract:
        - invoice_number: The No-Date or DN number or reference.
        - total_reimbursement_usd: The Total or Amount in Due at the bottom (e.g., 5677.61).
        - thc_usd: Terminal Handling (THC), DOC Fee, Agency Fee, Delivery Order (D/O). IMPORTANT: Extract EXACT amounts. Do not sum unrelated fees (e.g., do not add $10 bank charges, admin fees, or anything not explicitly THC/DO). Be very careful with digit recognition (e.g., 376.40 vs 386.40).
        - port_charges_usd: Port Charges (LoLo, Wharfage, Harbour, Lift on/off).
        If a fee is not present, use 0.0.
        Make sure to return the exact structure requested in the JSON schema.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ReimbursementData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}

class CustomAgentAssetItem(BaseModel):
    item_no: int = Field(description="Item number or line number if available", default=0)
    name: str = Field(description="Name or description of the asset/item")
    amount_usd: float = Field(description="Total amount for the item in USD", default=0.0)
    reasoning: str = Field(description="Chain of thought: explain how you identified this item and its amount", default="")

class CustomAgentCommercialInvoiceData(BaseModel):
    invoice_number: str = Field(description="Commercial invoice number", default="")
    total_value: float = Field(description="Total value of the commercial invoice", default=0.0)
    reasoning: str = Field(description="Chain of thought: explain how you found the invoice number and total value", default="")
    items: list[CustomAgentAssetItem] = Field(description="List of items extracted")

class CustomAgentCustomsItem(BaseModel):
    item_no: int = Field(description="The '32 Item No' printed on the declaration", default=0)
    name: str = Field(description="Commercial description of the item")
    customs_value_riel: float = Field(description="The '46 Customs Value' in Riel", default=0.0)
    
    step_1_locate_taxes: str = Field(description="Step 1: Locate the '47 CALCUL OF TAXES' section for this specific item.", default="")
    step_2_extract_tax_base: str = Field(description="Step 2: Identify the Tax Base for COP, SOP, and VOP. Compare with Customs Value.", default="")
    step_3_extract_amount: str = Field(description="Step 3: Extract the explicit Amount for COP, SOP, and VOP.", default="")
    
    customs_duty_riel: float = Field(description="Customs duty (COP) amount in Riel", default=0.0)
    special_tax_riel: float = Field(description="Special Tax (SOP) amount in Riel", default=0.0)
    vat_riel: float = Field(description="Value Added Tax (VOP) amount in Riel", default=0.0)

class CustomAgentCustomsDeclarationData(BaseModel):
    customs_declaration_number: str = Field(description="Customs declaration number (e.g., I 79523)", default="")
    exchange_rate: float = Field(description="Exchange rate found on the declaration form (e.g., Exch. rate, Box 23)", default=0.0)
    reasoning: str = Field(description="Chain of thought: explain how you found the declaration number and exchange rate", default="")
    items: list[CustomAgentCustomsItem] = Field(description="List of items extracted from the customs declaration")

class CustomAgent:
    def __init__(self, api_key: str):
        print("\n" + "="*50)
        print("🚀 INITIALIZING CUSTOM AGENT")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        
    def extract_commercial_invoice(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert data extractor. Extract the key values from the attached commercial invoice.
        Extract the invoice number, total value, and the line items.
        For each line item, extract the item number (if available), name, and amount.
        Make sure to return the exact structure requested in the JSON schema. Provide your step-by-step reasoning where requested.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CustomAgentCommercialInvoiceData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}

    def extract_customs_declaration(self, file_bytes: bytes, mime_type: str = "application/pdf") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = """
        You are an expert data extractor. Extract the key values from the attached customs declaration form.
        Extract the customs declaration number (found near 'Customs' or 'OFFICE OF LODGEMENT', e.g. 'I 79523').
        Extract the exchange rate (usually found under 'Exch. rate' or box 23).
        For each line item (which has a '32 Item No' and '31 DESCRIPTION OF GOODS' e.g. 'Commercial Description'), extract:
        - item_no: The number found in box 32 'Item No'.
        - name: The commercial description of the goods.
        - customs_value_riel: The number found in box '46 Customs Value'.
        Look closely at the '47 CALCUL OF TAXES' section which contains the taxes for each item. Follow the chain of thought instructions carefully.
        - step_1_locate_taxes: Step-by-step explanation. Write out exactly what you see in the '47 CALCUL OF TAXES' grid for this specific item number. List the rows for COP, SOP, and VOP.
        - step_2_extract_tax_base: Note the Tax Base for COP, SOP, and VOP. The base for COP should equal the '46 Customs Value'.
        - step_3_extract_amount: Note the Amount for COP, SOP, and VOP. 
        - customs_duty_riel: The amount in the 'Amount' column for Type 'COP'. If none, use 0.0.
        - special_tax_riel: The amount in the 'Amount' column for Type 'SOP'. If none, use 0.0.
        - vat_riel: The amount in the 'Amount' column for Type 'VOP'. If none, use 0.0.
        Make sure to return the exact structure requested in the JSON schema.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CustomAgentCustomsDeclarationData,
            temperature=0.0
        )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, document_part],
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Extraction Error: {e}")
            return {}


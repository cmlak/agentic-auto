import json
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

class AssetItem(BaseModel):
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
    vendor_name: str = Field(description="Name of the vendor/supplier. If in foreign language, translate to English.", default="")
    reasoning: str = Field(description="Reasonings or instructions for users to check about the extraction logic.", default="")
    items: list[AssetItem] = Field(description="List of items extracted from the commercial invoice")

class CustomsItem(BaseModel):
    name: str = Field(description="Commercial description of the item")
    customs_duty_riel: float = Field(description="Extract the exact number from the 'Amount' column on the row where 'Type' is 'COP'. Do NOT extract the 'Tax Base'. Do NOT extract the VOP amount.", default=0.0)
    special_tax_riel: float = Field(description="Extract the exact number from the 'Amount' column on the row where 'Type' is 'SOP'.", default=0.0)
    vat_riel: float = Field(description="Extract the exact number from the 'Amount' column on the row where 'Type' is 'VOP'. Do NOT extract the COP amount.", default=0.0)

class CustomsDeclarationData(BaseModel):
    customs_declaration_number: str = Field(description="Customs declaration number (e.g., I 79523)", default="")
    exchange_rate: float = Field(description="Exchange rate found on the declaration form (e.g., Exch. rate)", default=0.0)
    items: list[CustomsItem] = Field(description="List of items extracted from the customs declaration")

class AuxiliaryInvoice(BaseModel):
    invoice_number: str = Field(description="Invoice or receipt number (e.g., AHKW26050005, INV2026-0258, 81836)", default="")
    provider_name: str = Field(description="Name of the company providing the service or issuing the invoice", default="")
    provider_type: str = Field(description="Classify as either 'International Carrier' or 'Local Forwarder/Broker'", default="Local Forwarder/Broker")
    local_agent_name: str = Field(description="Name of the local agent, if the invoice indicates they are acting on behalf of an international carrier (e.g., Arrow Shipping on behalf of RCL Feeder). Leave empty if not applicable.", default="")
    freight_charge_net_usd: float = Field(description="Freight charge NET amount (excluding VAT) in USD", default=0.0)
    freight_charge_gross_usd: float = Field(description="Freight charge GROSS amount (including VAT) in USD", default=0.0)
    insurance_net_usd: float = Field(description="Insurance NET amount (excluding VAT) in USD", default=0.0)
    insurance_gross_usd: float = Field(description="Insurance GROSS amount (including VAT) in USD", default=0.0)
    terminal_handling_charge_net_usd: float = Field(description="Terminal Handling Charge (THC), DOC Fee, Agency Fee, or Delivery Fee NET amount in USD.", default=0.0)
    terminal_handling_charge_gross_usd: float = Field(description="Terminal Handling Charge (THC), DOC Fee, Agency Fee, or Delivery Fee GROSS amount in USD.", default=0.0)
    port_charges_net_usd: float = Field(description="Port Charges NET amount in USD", default=0.0)
    port_charges_gross_usd: float = Field(description="Port Charges GROSS amount in USD", default=0.0)
    clearance_trucking_net_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight fees NET amount in USD", default=0.0)
    clearance_trucking_gross_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight fees GROSS amount in USD", default=0.0)

class AuxiliaryCostsData(BaseModel):
    invoices: list[AuxiliaryInvoice] = Field(description="List of all distinct invoices found in the document")
    
class ReimbursementData(BaseModel):
    invoice_number: str = Field(description="Reimbursement invoice or reference number", default="")
    total_reimbursement_usd: float = Field(description="Total reimbursement amount in USD", default=0.0)
    thc_usd: float = Field(description="THC / D.O Fee in USD", default=0.0)
    port_charges_usd: float = Field(description="Port Charges (e.g. LoLo.Port Charges) in USD", default=0.0)

class CapitalizationAgent:
    def __init__(self, api_key: str):
        print("\n" + "="*50)
        print("🚀 INITIALIZING CAPITALIZATION AGENT")
        print("="*50)
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.5-flash'
        
    def extract_commercial_invoice(self, pdf_bytes: bytes) -> dict:
        document_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        prompt = """
        You are an expert data extractor. Extract the key values from the attached commercial invoice and packing list.
        Extract the invoice number, date, total value, total gross weight (usually found on the packing list, look for 'G.W. (KGS)'), and the line items.
        For each line item, extract the name, CDC, quantity, unit, unit purchase price, amount, and gross weight (match the items from the invoice to the packing list to find their G.W. (KGS)).
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
        
        CRITICAL INSTRUCTION FOR MULTIPLE ITEMS:
        The items are listed in the main body (each with a '32 Item No' and '31 DESCRIPTION OF GOODS' e.g. 'Commercial Description' and '46 Customs Value').
        The taxes are listed separately at the bottom in the '47 CALCUL OF TAXES' section, broken down into multiple tax boxes (one box per item).
        To correctly link the taxes to the item, you MUST match the '46 Customs Value' of the item to the 'Tax Base' of the 'COP' tax in the tax box.
        For example, if Item 2 has '46 Customs Value' of 80,520,000, you must find the tax box in section 47 where the COP Tax Base is 80,520,000. That tax box contains the taxes for Item 2.
        
        For each line item, extract:
        - name: The commercial description of the goods.
        - customs_duty_riel: The amount in the 'Amount' column for Type 'COP' in the corresponding tax box. If none, use 0.0.
        - special_tax_riel: The amount in the 'Amount' column for Type 'SOP' in the corresponding tax box. If none, use 0.0.
        - vat_riel: The amount in the 'Amount' column for Type 'VOP' in the corresponding tax box. If none, use 0.0.
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

    def extract_auxiliary_costs(self, file_bytes: bytes, mime_type: str = "application/pdf") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = """
        You are an expert data extractor. Extract shipment-level costs from the attached document.
        This could be a freight invoice, a tax invoice for shipping, or a customs receipt.
        
        CRITICAL INSTRUCTION: A single attached PDF may contain MULTIPLE separate invoices or receipts across different pages. You MUST extract the data from ALL invoices in the document. DO NOT mistakenly extract only the first invoice and ignore the rest!
        
        You must act as a Semantic Classifier:
        1. Scan EVERY SINGLE PAGE of the document.
        2. Identify EVERY distinct invoice, receipt, or fee breakdown.
        3. For EACH identified invoice, extract the provider_name and carefully classify the provider_type.
           - If it is a non-resident international shipping line, classify as 'International Carrier'.
           - If it is a locally registered Cambodian freight forwarder or logistics company, classify as 'Local Forwarder/Broker'.
           - Extract any local_agent_name if the invoice was issued by a local agent on behalf of the international carrier.
        4. For EACH fee in the invoice, explicitly identify its Net Amount (Subtotal excluding VAT/Tax) AND its Gross Amount (Total including VAT/Tax). If an invoice does not explicitly break down VAT, treat the stated fee as BOTH the Net and Gross amount. DO NOT skip fees.
        5. Semantically classify each fee into one of the exact categories below, and sum them per invoice:
           - freight_charge (Ocean/Air Freight receipts)
           - insurance (Insurance receipts)
           - terminal_handling_charge (Terminal Handling (THC), DOC Fee, Agency Fee, Delivery Order (D/O), EDI fee)
           - port_charges (Port Charges (LoLo, Wharfage, Harbour, Lift on/off))
           - clearance_trucking (Customs Clearance, Inland Trucking, Truck Standby, Over Weight fees, Tolls, Transport)
        
        If a fee is not present on this invoice, use 0.0.
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
        - thc_usd: Terminal Handling (THC), DOC Fee, Agency Fee, Delivery Order (D/O).
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

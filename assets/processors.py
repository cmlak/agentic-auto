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
    items: list[AssetItem] = Field(description="List of items extracted from the commercial invoice")

class CustomsItem(BaseModel):
    name: str = Field(description="Commercial description of the item")
    customs_duty_riel: float = Field(description="Customs duty (COP) amount in Riel", default=0.0)
    special_tax_riel: float = Field(description="Special Tax (SOP) amount in Riel", default=0.0)
    vat_riel: float = Field(description="Value Added Tax (VOP) amount in Riel", default=0.0)

class CustomsDeclarationData(BaseModel):
    customs_declaration_number: str = Field(description="Customs declaration number (e.g., I 79523)", default="")
    exchange_rate: float = Field(description="Exchange rate found on the declaration form (e.g., Exch. rate)", default=0.0)
    items: list[CustomsItem] = Field(description="List of items extracted from the customs declaration")

class AuxiliaryCostsData(BaseModel):
    invoice_number: str = Field(description="Invoice or receipt number (e.g., AHKW26050005, INV2026-0258, 81836)", default="")
    custom_document_fee: float = Field(description="Custom document fee or Other Payment Receipt fee", default=0.0)
    custom_document_fee_currency: str = Field(description="Currency of custom document fee (e.g., KHR, USD)", default="USD")
    freight_charge_usd: float = Field(description="Freight charge amount in USD", default=0.0)
    insurance_usd: float = Field(description="Insurance amount in USD", default=0.0)
    terminal_handling_charge_usd: float = Field(description="Terminal Handling Charge (THC), DOC Fee, Agency Fee, or Delivery Fee in USD. Sum these up if multiple exist.", default=0.0)
    port_charges_usd: float = Field(description="Port Charges in USD", default=0.0)
    clearance_trucking_demurrage_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight, Demurrage fees in USD", default=0.0)

class ReimbursementData(BaseModel):
    invoice_number: str = Field(description="Reimbursement invoice or reference number", default="")
    total_reimbursement_usd: float = Field(description="Total reimbursement amount in USD", default=0.0)
    custom_document_fee_usd: float = Field(description="Customs Procedure Fee (CPF) or Custom document fee in USD", default=0.0)
    thc_usd: float = Field(description="THC / D.O Fee in USD", default=0.0)
    port_charges_usd: float = Field(description="Port Charges (e.g. LoLo.Port Charges) in USD", default=0.0)
    clearance_trucking_demurrage_usd: float = Field(description="Clearance, Trucking, Truck Standby, Over Weight, Demurrage fees in USD", default=0.0)


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
        For each line item (which has a '32 Item No' and '31 DESCRIPTION OF GOODS' e.g. 'Commercial Description'), extract:
        - name: The commercial description of the goods.
        Look closely at the '47 CALCUL OF TAXES' section which contains the taxes for each item.
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

    def extract_auxiliary_costs(self, file_bytes: bytes, mime_type: str = "application/pdf") -> dict:
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        prompt = """
        You are an expert data extractor. Extract shipment-level costs from the attached document.
        This could be a freight invoice, a tax invoice for shipping, or a customs receipt.
        Extract:
        - invoice_number: The reference, receipt, or invoice number (e.g., Job No, Inv No, Receipt No).
        - custom_document_fee: ONLY extract this if the document is titled 'OTHER PAYMENT RECEIPT'. Look for 'Total Amount for Fee' (e.g., 40,000). Return 0.0 for any other type of invoice.
        - custom_document_fee_currency: The currency of the custom document fee. Default to 'KHR' if no currency is specified on a Cambodian Other Payment Receipt.
        - freight_charge_usd: The Freight Charge amount in USD.
        - insurance_usd: The Insurance amount in USD.
        - terminal_handling_charge_usd: The Terminal Handling Charge (THC), or sum of DOC fees, Agency fees, and Delivery fees in USD.
        - port_charges_usd: The Port Charges amount in USD.
        - clearance_trucking_demurrage_usd: The Clearance, Trucking, or Demurrage amount in USD.
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
        - custom_document_fee_usd: Look for CPF or Custom Procedure Fee or Custom Document Fee.
        - thc_usd: Look for THC or D.O Fee.
        - port_charges_usd: Look for Port Charges or LoLo.Port Charges.
        - clearance_trucking_demurrage_usd: Sum up any Truck Standby, Over Weight, Demurrage, or Clearance fees.
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

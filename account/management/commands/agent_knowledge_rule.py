from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from account.models import AgentKnowledgeRule # Adjust import based on your app name

class Command(BaseCommand):
    help = 'Creates default agent knowledge rules.'

    def add_arguments(self, parser):
        parser.add_argument('-s', '--schema', type=str, required=True, help='The tenant schema name (e.g., CCKT or public)')

    def handle(self, *args, **kwargs):
        schema_name = kwargs['schema']
        
        # Define all the rules to be seeded
        rules = [
            # ---------------------------------------------------------
            # 1. TAX INVOICE VS. COMMERCIAL INVOICE (VAT RECOGNITION)
            # ---------------------------------------------------------
            {
                'title': 'VAT Input Recognition Law',
                'agent_scope': 'TAX',
                'rule_type': 'TAX_LAW',
                'tags': 'VAT, tax invoice, commercial invoice, receipt, វិក្កយបត្រ, វិក្កយបត្រអាករ, input tax',
                'priority_weight': 100,  # High priority legal override
                'condition': 'When determining if 10% Input VAT can be extracted and claimed from an invoice or receipt.',
                'action_or_fact': (
                    'Only claim VAT if the document header explicitly states "Tax Invoice" (English) or "វិក្កយបត្រអាករ" (Khmer). '
                    'If the document simply states "Invoice", "Commercial Invoice", "Receipt", or "វិក្កយបត្រ" (without the word "អាករ"), '
                    'you MUST NOT extract any Input VAT. Set vat_usd = 0.0 and vat_base_usd = 0.0. '
                    'Place the entire gross total into exempt_usd, even if the vendor printed their VAT TIN on the receipt.'
                )
            },
            # ---------------------------------------------------------
            # 2. GENERAL VENDOR ROUTING FOR RETAIL/COMMERCIAL RECEIPTS
            # ---------------------------------------------------------
            {
                'title': 'General Vendor Routing for Non-Tax Documents',
                'agent_scope': 'GLOBAL',
                'rule_type': 'ACCOUNT_MAPPING',
                'tags': 'vendor, commercial invoice, receipt, retail, general vendor',
                'priority_weight': 50,
                'condition': 'When processing a standard Commercial Invoice, retail receipt, or fuel slip (i.e., any document that is NOT a valid Tax Invoice).',
                'action_or_fact': (
                    'Do not attempt to extract a unique vendor name from the receipt header (e.g., do not extract specific restaurant or shop names). '
                    'For all commercial/retail receipts, you MUST output the exact string "General Vendor" for the vendor name. '
                    'This ensures the backend system correctly maps the transaction to V-00001.'
                )
            },
            # ---------------------------------------------------------
            # 3. EQUIPMENT RENTAL VS. DRIVER WHT SPLITTING
            # ---------------------------------------------------------
            {
                'title': 'WHT Compliance Split: Equipment vs. Operator',
                'agent_scope': 'TAX',
                'rule_type': 'TAX_LAW',
                'tags': 'rental, equipment, driver, machinery, split, excavator, dump truck, operator',
                'priority_weight': 100, # High priority legal override
                'condition': 'When a single invoice bundles Equipment/Machinery Rental (e.g., dump trucks, excavators) AND a Driver/Operator Fee.',
                'action_or_fact': (
                    'You MUST NOT aggregate these items. You MUST output exactly TWO separate PurchaseEntry items for that single page. '
                    'Entry 1: The equipment rental amount. Entry 2: The driver/operator service fee. '
                    'This is legally required because equipment rental and service performance are subject to different Withholding Tax (WHT) rates.'
                )
            },
            # ---------------------------------------------------------
            # 4. KHR TO USD CONVERSION VALIDATION
            # ---------------------------------------------------------
            {
                'title': 'Currency Validation & Handwritten Overrides',
                'agent_scope': 'GLOBAL',
                'rule_type': 'ANTI_PATTERN',
                'tags': 'currency, KHR, USD, exchange, handwritten, conversion',
                'priority_weight': 50,
                'condition': 'When extracting financial amounts to ensure they are reported in US Dollars (USD).',
                'action_or_fact': (
                    'Actively analyze the billed currency. If the total is unusually large (e.g., 100,000+), it is likely billed in KHR. '
                    'You MUST scan the bottom and margins of the document for hand-written numbers. '
                    'If a handwritten USD equivalent is present, you MUST extract and use that handwritten USD value for all final outputs.'
                )
            },
            # ---------------------------------------------------------
            # 5. REASONING FORMAT TEMPLATE 1 (From Original Command)
            # ---------------------------------------------------------
            {
                'title': 'Reasoning Format Template 1 (Matched Invoice)',
                'agent_scope': 'RECON',
                'rule_type': 'DOCUMENT_PARSING', # Updated from MACRO_FACT based on previous corrections
                'tags': 'reasoning, matched, template, page, purchase id',
                'priority_weight': 10,
                'condition': 'When a transaction is matched to an open purchase invoice.',
                'action_or_fact': (
                    'You MUST format your `reasoning` output EXACTLY as: '
                    '"[Explanation of what the payment was for]. Matched with open purchase ID: [ID] (Page: [Page Number])." '
                    'Failure to include the page information and matched_purchase_id is a critical failure.'
                )
            },
            # ---------------------------------------------------------
            # 6. REASONING FORMAT TEMPLATE 2 (From Original Command)
            # ---------------------------------------------------------
            {
                'title': 'Reasoning Format Template 2 (Unmatched / Direct Expense)',
                'agent_scope': 'RECON',
                'rule_type': 'DOCUMENT_PARSING', # Updated from MACRO_FACT
                'tags': 'reasoning, unmatched, direct expense, template',
                'priority_weight': 10,
                'condition': 'When a transaction is an unmatched payment or direct expense.',
                'action_or_fact': (
                    'You MUST format your `reasoning` output EXACTLY as: '
                    '"[Explanation of what the payment was for]. No matching open purchase found, classified as [Expense/Asset/Prepayment]."'
                )
            },
        ]

        with schema_context(schema_name):
            self.stdout.write(f'🔗 Connected to schema: {schema_name}')
            for rule_data in rules:
                title = rule_data.pop('title')
                rule, created = AgentKnowledgeRule.objects.get_or_create(
                    title=title,
                    defaults=rule_data
                )
                
                if created:
                    self.stdout.write(self.style.SUCCESS(f'Successfully created rule: "{rule.title}"'))
                else:
                    # Optional: Update existing rules if you want the command to overwrite old data
                    AgentKnowledgeRule.objects.filter(id=rule.id).update(**rule_data)
                    self.stdout.write(self.style.WARNING(f'Rule already exists (and was updated): "{rule.title}"'))
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from account.models import AgentKnowledgeRule

class Command(BaseCommand):
    help = 'Creates default agent knowledge rules.'

    def add_arguments(self, parser):
        parser.add_argument('-s', '--schema', type=str, required=True, help='The tenant schema name (e.g., CCKT or public)')

    def handle(self, *args, **kwargs):
        schema_name = kwargs['schema']
        rules = [
            {
                'title': 'Reasoning Format Template 1 (Matched Invoice)',
                'agent_scope': 'RECON',
                'rule_type': 'MACRO_FACT',
                'tags': 'reasoning, matched, template, page, purchase id',
                'condition': 'When a transaction is matched to an open purchase invoice.',
                'action_or_fact': (
                    'You MUST format your `reasoning` output EXACTLY as: '
                    '"[Explanation of what the payment was for]. Matched with open purchase ID: [ID] (Page: [Page Number])." '
                    'Failure to include the page information and matched_purchase_id is a critical failure.'
                )
            },
            {
                'title': 'Reasoning Format Template 2 (Unmatched / Direct Expense)',
                'agent_scope': 'RECON',
                'rule_type': 'MACRO_FACT',
                'tags': 'reasoning, unmatched, direct expense, template',
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
                    self.stdout.write(self.style.WARNING(f'Rule already exists: "{rule.title}"'))
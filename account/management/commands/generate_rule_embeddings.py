import os
from django.core.management.base import BaseCommand
from django.conf import settings
from django_tenants.utils import schema_context
from account.models import AgentKnowledgeRule
from google import genai
from google.genai import types

class Command(BaseCommand):
    help = 'Generates pgvector embeddings for AgentKnowledgeRule instances.'

    def add_arguments(self, parser):
        parser.add_argument('-s', '--schema', type=str, required=True, help='The tenant schema name (e.g., CCKT)')
        parser.add_argument('--force', action='store_true', help='Force regenerate embeddings for all rules.')

    def handle(self, *args, **kwargs):
        schema_name = kwargs['schema']
        force = kwargs['force']

        api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
        if not api_key:
            self.stdout.write(self.style.ERROR("Error: GEMINI_API_KEY_2 is missing. Cannot generate embeddings."))
            return

        client = genai.Client(api_key=api_key)

        with schema_context(schema_name):
            self.stdout.write(self.style.SUCCESS(f"🔗 Connected to schema: {schema_name}"))
            
            if force:
                rules = AgentKnowledgeRule.objects.all()
            else:
                rules = AgentKnowledgeRule.objects.filter(embedding__isnull=True)

            total_rules = rules.count()
            if total_rules == 0:
                self.stdout.write(self.style.WARNING("No rules found needing embeddings. Use --force to regenerate all."))
                return

            self.stdout.write(f"🔄 Found {total_rules} rules to process...")
            success_count = 0
            error_count = 0

            for i, rule in enumerate(rules, 1):
                self.stdout.write(f"  [{i}/{total_rules}] Processing Rule: {rule.title[:40]}...")
                content_to_embed = f"Title: {rule.title}\nCondition: {rule.condition}\nAction/Fact: {rule.action_or_fact}\nTags: {rule.tags}"
                
                try:
                    try:
                        embed_res = client.models.embed_content(
                            model='gemini-embedding-2',
                            contents=content_to_embed[:8000],
                            config=types.EmbedContentConfig(output_dimensionality=768)
                        )
                    except Exception as e:
                        # Fallback to the universally available legacy model if 404
                        if '404' in str(e):
                            embed_res = client.models.embed_content(
                                model='gemini-embedding-001',
                                contents=content_to_embed[:8000],
                                config=types.EmbedContentConfig(output_dimensionality=768)
                            )
                        else:
                            raise e
                    if embed_res.embeddings:
                        rule.embedding = embed_res.embeddings[0].values
                        rule.save(update_fields=['embedding'])
                        success_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ❌ Failed: {str(e)}"))
                    error_count += 1

            self.stdout.write(self.style.SUCCESS(
                f"\n✅ Embedding Generation Complete!\n"
                f"   Success: {success_count}\n"
                f"   Failed:  {error_count}"
            ))

import os
from django.core.management.base import BaseCommand
from django.conf import settings
from account.models import AgentKnowledgeRule
from google import genai
from google.genai import types

class Command(BaseCommand):
    help = 'Analyzes the AgentKnowledgeRule database for contradictions, overlaps, and clarity issues using Gemini.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('🔍 Fetching active RAG rules from the database...'))
        
        # 1. Fetch all active rules
        rules = AgentKnowledgeRule.objects.filter(is_active=True).order_by('agent_scope', 'rule_type')
        
        if not rules.exists():
            self.stdout.write(self.style.WARNING('No active rules found in the database.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Found {rules.count()} active rules. Preparing context payload...'))

        # 2. Format the rules into a structured text string for the AI to read
        formatted_rules = []
        for rule in rules:
            rule_text = (
                f"Rule ID: {rule.id} | Scope: {rule.agent_scope} | Type: {rule.rule_type}\n"
                f"Title: {rule.title}\n"
                f"Condition: {rule.condition}\n"
                f"Action/Fact: {rule.action_or_fact}\n"
                f"---"
            )
            formatted_rules.append(rule_text)
        
        rules_context = "\n".join(formatted_rules)

        # 3. Construct the Prompt for the Audit Agent
        prompt = f"""
        You are an elite QA Engineer and Systems Auditor for an Agentic Accounting Platform in Cambodia.
        Below is the entire active database of rules used in the system's RAG (Retrieval-Augmented Generation) pipeline.
        
        Your task is to analyze these rules strictly looking for:
        1. CONTRADICTIONS: Rule A tells the AI to do X, but Rule B tells the AI to do NOT X.
        2. DILUTION / OVERLAP: Two rules handle the exact same scenario but with slightly different instructions, which will confuse the vector search.
        3. CLARITY ISSUES: The "Condition" is too vague, or the "Action" does not give a definitive instruction.
        
        Do not critique the accounting laws themselves, only the logical consistency of the rules as written.
        If you find issues, list them clearly referencing the Rule IDs. If the rulebase is perfect, say so.
        
        <ACTIVE_RULES>
        {rules_context}
        </ACTIVE_RULES>
        """

        self.stdout.write(self.style.NOTICE('🧠 Submitting rulebook to Gemini 1.5 Pro for analysis...'))

        # 4. Invoke Gemini API
        try:
            api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv("GEMINI_API_KEY"))
            if not api_key:
                self.stdout.write(self.style.ERROR("GEMINI_API_KEY not found in settings or environment variables."))
                return

            client = genai.Client(api_key=api_key)
            
            response = client.models.generate_content(
                model='gemini-2.5-pro',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2, # Low temperature for analytical, deterministic thinking
                )
            )

            # 5. Output the results beautifully to the terminal
            self.stdout.write(self.style.SUCCESS('\n======================================================='))
            self.stdout.write(self.style.SUCCESS('📋 AI RULE AUDIT REPORT'))
            self.stdout.write(self.style.SUCCESS('=======================================================\n'))
            
            print(response.text)
            
            self.stdout.write(self.style.SUCCESS('\n======================================================='))
            self.stdout.write(self.style.SUCCESS('Audit Complete.'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to run AI analysis: {str(e)}'))
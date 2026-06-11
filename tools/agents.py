# tools/agents.py (New file for your autonomous logic)
import os
from django.conf import settings
from google import genai
from .models import Purchase
from account.models import AgentNotification
from django.utils import timezone
from clients.models import Client
from django_tenants.utils import schema_context

class TaxAgent:
    @staticmethod
    def audit_monthly_purchases():
        # 1. Check current state
        missing_tins = Purchase.objects.filter(payment_status='Open', vattin__isnull=True)
        
        if missing_tins.exists():
            # Create or update the active alert
            AgentNotification.objects.update_or_create(
                agent_type='TAX',
                title="WHT Exposure Warning",
                is_resolved=False, # Only look for unresolved ones
                defaults={
                    'message': f"Detected {missing_tins.count()} open invoices lacking VAT TINs.",
                    'severity': 'WARNING',
                    'action_url': '/tools/invoices/?missing_tin=true',
                    'action_label': 'Review Pending Invoices'
                }
            )
        else:
            # 2. AUTO-RESOLUTION: The AI sees the problem is gone!
            # Find any open warnings about this and resolve them autonomously.
            AgentNotification.objects.filter(
                agent_type='TAX', 
                title="WHT Exposure Warning", 
                is_resolved=False
            ).update(
                is_resolved=True,
                resolved_at=timezone.now()
            )
            
class EconAgent:
    @staticmethod
    def evaluate_currency_risk(current_rate, average_last_month):
        print(f"DEBUG: Entering evaluate_currency_risk. Rate: {current_rate}, Avg: {average_last_month:.2f}")
        # Generate AI Analysis using Gemini
        ai_analysis = ""
        try:
            api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
            if api_key:
                print("DEBUG: Gemini API Key found. Requesting analysis...")
                client = genai.Client(api_key=api_key)
                prompt = (
                    f"Act as an expert corporate currency risk analyst. "
                    f"The current NBC official exchange rate is {current_rate} KHR/USD, compared to the 30-day "
                    f"average of {average_last_month:.2f} KHR/USD. Provide a concise, 2-3 sentence analysis "
                    f"of this fluctuation and a brief actionable recommendation for corporate cash management."
                )
                response = client.models.generate_content(
                    model='gemini-2.5-pro',
                    contents=prompt,
                )
                if response.text:
                    ai_analysis = f"\n\nAI Analysis: {response.text.strip()}"
                    print("DEBUG: Gemini analysis successfully generated.")
        except Exception as e:
            print(f"EconAgent AI Analysis Error: {e}")

        print("DEBUG: Broadcasting AgentNotification to all tenant schemas...")
        for tenant in Client.objects.exclude(schema_name='public'):
            with schema_context(tenant.schema_name):
                AgentNotification.objects.create(
                    agent_type='ECON',
                    severity='INFO',
                    title="Daily KHR Exchange Rate Analysis",
                    message=f"The NBC official exchange rate is {current_rate} KHR/USD.{ai_analysis}",
                    is_resolved=True,
                    resolved_at=timezone.now()
                )
        print("DEBUG: AgentNotifications successfully broadcasted.")
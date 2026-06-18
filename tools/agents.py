# tools/agents.py (New file for your autonomous logic)
import os
from django.conf import settings
from google import genai
from google.genai import types
from .models import Purchase
from account.models import AgentNotification
from django.utils import timezone
from clients.models import Client
from django_tenants.utils import schema_context

def create_agent_notification(agent_type: str, title: str, message: str, severity: str, action_url: str = "") -> str:
    """Tool that allows an agent to broadcast an official notification to all client dashboards."""
    print(f"DEBUG: Triggering broadcast notification: {title}")
    count = 0
    for tenant in Client.objects.exclude(schema_name='public'):
        with schema_context(tenant.schema_name):
            AgentNotification.objects.create(
                agent_type=agent_type,
                title=title,
                message=message,
                severity=severity,
                action_url=action_url,
                is_resolved=False
            )
            count += 1
    print(f"DEBUG: Broadcast complete to {count} tenants.")
    return f"Notification '{title}' successfully broadcasted to {count} tenants."

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
        
        deviation_pct = 0.0
        if average_last_month > 0:
            deviation_pct = abs(current_rate - average_last_month) / average_last_month * 100
            
        print(f"DEBUG: Market deviation is {deviation_pct:.3f}%. Generating daily AI analysis.")
            
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
                    severity='INFO' if deviation_pct < 0.5 else 'WARNING',
                    title="Daily Currency Analysis" if deviation_pct < 0.5 else "Currency Volatility Risk Detected",
                    message=f"The NBC official exchange rate has deviated to {current_rate} KHR/USD (a {deviation_pct:.2f}% change).{ai_analysis}",
                    is_resolved=False
                )
        print("DEBUG: AgentNotifications successfully broadcasted.")

    @staticmethod
    def evaluate_incoming_data(raw_data_stream: str):
        api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
        client = genai.Client(api_key=api_key)
        
        prompt = (
            f"Analyze the following incoming economic data stream: \n\n{raw_data_stream}\n\n"
            f"If you detect significant inflation (CPI changes), structural shifts, or risks "
            f"that impact corporate cash management, use the 'create_agent_notification' tool "
            f"to inform the team immediately. Otherwise, take no action."
        )
        
        try:
            # The Chat API automatically executes the function if Gemini decides to call it
            chat = client.chats.create(
                model='gemini-2.5-pro',
                config=types.GenerateContentConfig(
                    tools=[create_agent_notification]  # Registers the function as a tool
                )
            )
            response = chat.send_message(prompt)
            print(f"DEBUG: Agent Response: {response.text}")
        except Exception as e:
            print(f"EconAgent evaluate_incoming_data Error: {e}")
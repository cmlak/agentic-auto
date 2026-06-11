# tools/agents.py (New file for your autonomous logic)
import os
from django.conf import settings
from google import genai
from .models import Purchase
from account.models import AgentNotification

class TaxAgent:
    @staticmethod
    def audit_monthly_purchases():
        """Scans open purchases for missing VAT TINs or WHT anomalies."""
        unregistered_vendors = Purchase.objects.filter(payment_status='Open', vattin__isnull=True).count()
        
        if unregistered_vendors > 0:
            AgentNotification.objects.create(
                agent_type='TAX',
                severity='WARNING',
                title="WHT Exposure Warning",
                message=f"Detected {unregistered_vendors} open invoices lacking VAT TINs. Cambodian tax law requires 15% Withholding Tax on services for unregistered entities. Please verify vendor profiles.",
                action_url="/tools/review-invoices/",
                action_label="Review Pending Invoices"
            )

class EconAgent:
    @staticmethod
    def evaluate_currency_risk(current_rate, average_last_month):
        if current_rate > (average_last_month * 1.02): # 2% depreciation
            # Generate AI Analysis using Gemini
            ai_analysis = ""
            try:
                api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
                if api_key:
                    client = genai.Client(api_key=api_key)
                    prompt = (
                        f"Act as an expert corporate currency risk analyst. The Cambodian Riel (KHR) has depreciated. "
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
            except Exception as e:
                print(f"EconAgent AI Analysis Error: {e}")

            AgentNotification.objects.create(
                agent_type='ECON',
                severity='INFO',
                title="KHR Depreciation Alert",
                message=f"The NBC official exchange rate has hit {current_rate} KHR/USD. Consider holding USD cash reserves and delaying KHR conversions.{ai_analysis}",
            )
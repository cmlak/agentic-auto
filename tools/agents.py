# tools/agents.py (New file for your autonomous logic)
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
            AgentNotification.objects.create(
                agent_type='ECON',
                severity='INFO',
                title="KHR Depreciation Alert",
                message=f"The NBC official exchange rate has hit {current_rate} KHR/USD. Consider holding USD cash reserves and delaying KHR conversions.",
            )
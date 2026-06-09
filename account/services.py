# account/services.py (Create this file)
from django.db.models import Sum
from .models import JournalLine, Account, DashboardSnapshot
from clients.models import ExchangeRate
from django.utils import timezone
import datetime

def generate_tenant_dashboard_snapshot():
    """
    Calculates current financial standing for the active tenant schema
    and saves a snapshot for the Dashboard to consume instantly.
    """
    now = timezone.now()
    period = now.strftime("%b %Y")

    # 1. Calculate Total Cash (Asset accounts starting with '100')
    cash_dr = JournalLine.objects.filter(account__account_id__startswith='100').aggregate(Sum('debit'))['debit__sum'] or 0
    cash_cr = JournalLine.objects.filter(account__account_id__startswith='100').aggregate(Sum('credit'))['credit__sum'] or 0
    total_cash = cash_dr - cash_cr

    # 2. Calculate Total Trade Payables (Liability accounts starting with '200')
    ap_cr = JournalLine.objects.filter(account__account_id__startswith='200').aggregate(Sum('credit'))['credit__sum'] or 0
    ap_dr = JournalLine.objects.filter(account__account_id__startswith='200').aggregate(Sum('debit'))['debit__sum'] or 0
    total_ap = ap_cr - ap_dr

    # 3. Calculate YTD Revenue & Expenses
    current_year = now.year
    rev_cr = JournalLine.objects.filter(account__account_type='Revenue', journal_entry__date__year=current_year).aggregate(Sum('credit'))['credit__sum'] or 0
    exp_dr = JournalLine.objects.filter(account__account_type='Expense', journal_entry__date__year=current_year).aggregate(Sum('debit'))['debit__sum'] or 0
    net_profit = rev_cr - exp_dr

    # 4. Compile flexible chart data (e.g., simple Expense vs Revenue)
    chart_payload = {
        "labels": ["Revenue", "Expenses", "Cash on Hand", "Payables"],
        "data": [rev_cr, exp_dr, total_cash, total_ap]
    }

    # 5. Retrieve the most recent exchange rate (falling back to 4050 if none exist)
    latest_rate_obj = ExchangeRate.objects.order_by('-date').first()
    current_exchange_rate = latest_rate_obj.rate if latest_rate_obj else 4050

    # Save to the isolated tenant schema
    snapshot = DashboardSnapshot.objects.create(
        period_label=period,
        total_cash_usd=total_cash,
        total_ap_usd=total_ap,
        net_profit_usd=net_profit,
        chart_data_payload=chart_payload,
        exchange_rate=current_exchange_rate
    )
    
    return snapshot
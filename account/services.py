# account/services.py (Create this file)
from django.db.models import Sum
from .models import JournalLine, Account, DashboardSnapshot
from clients.models import ExchangeRate
from django.utils import timezone
from dateutil.relativedelta import relativedelta
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

    # ---------------------------------------------------------
    # NEW: BUILD CHART.JS DATA PAYLOADS
    # ---------------------------------------------------------
    
    # Chart 1: Trailing 6-Month Revenue vs Expenses (Bar Chart)
    months_labels = []
    rev_data = []
    exp_data = []
    
    for i in range(5, -1, -1):
        target_month = now - relativedelta(months=i)
        months_labels.append(target_month.strftime("%b"))
        
        # Calculate revenue for that specific month
        m_rev = JournalLine.objects.filter(
            account__account_type='Revenue', 
            journal_entry__date__year=target_month.year,
            journal_entry__date__month=target_month.month
        ).aggregate(Sum('credit'))['credit__sum'] or 0
        
        # Calculate expenses for that specific month
        m_exp = JournalLine.objects.filter(
            account__account_type='Expense', 
            journal_entry__date__year=target_month.year,
            journal_entry__date__month=target_month.month
        ).aggregate(Sum('debit'))['debit__sum'] or 0
        
        rev_data.append(float(m_rev))
        exp_data.append(float(m_exp))

    # Chart 2: Current Month Expense Breakdown (Doughnut Chart)
    # Grouping by first two digits (e.g., 70=Payroll, 72=OPEX/Taxes) or by name
    payroll_exp = float(JournalLine.objects.filter(account__account_id__startswith='705', journal_entry__date__month=now.month, journal_entry__date__year=now.year).aggregate(Sum('debit'))['debit__sum'] or 0)
    tax_exp = float(JournalLine.objects.filter(account__account_id__startswith='7254', journal_entry__date__month=now.month, journal_entry__date__year=now.year).aggregate(Sum('debit'))['debit__sum'] or 0)
    general_exp = float(JournalLine.objects.filter(account__account_id__startswith='7250', journal_entry__date__month=now.month, journal_entry__date__year=now.year).aggregate(Sum('debit'))['debit__sum'] or 0)

    # Combine into a single JSON-serializable dictionary
    chart_payload = {
        "bar_chart": {
            "labels": months_labels,
            "revenue": rev_data,
            "expenses": exp_data
        },
        "doughnut_chart": {
            "labels": ["Payroll & Benefits", "Tax Accruals", "General OPEX"],
            "data": [payroll_exp, tax_exp, general_exp]
        }
    }

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
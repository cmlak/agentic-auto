# account/services.py (Create this file)
from django.db.models import Sum
from .models import JournalLine, Account, DashboardSnapshot
from clients.models import ExchangeRate
from django.utils import timezone
from dateutil.relativedelta import relativedelta
import datetime
from google import genai
from google.genai import types
from django.conf import settings
import os
import json
import re

def generate_tenant_dashboard_snapshot():
    """
    Calculates current financial standing for the active tenant schema
    and saves a snapshot for the Dashboard to consume instantly.
    """
    now = timezone.now()
    period = now.strftime("%b %Y")

    # ---------------------------------------------------------
    # Part 1: Show key financial markers
    # ---------------------------------------------------------

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
    # Part 2: BUILD CHART.JS DATA PAYLOADS
    # ---------------------------------------------------------
    
    # Chart 1: Trailing 6-Month Revenue vs Expenses (Bar Chart)
    months_labels = []
    rev_data = []
    exp_data = []
    payroll_data = []
    tax_data = []
    general_data = []
    other_exp_data = []
    
    print("\n--- DEBUG: STARTING 6-MONTH CHART AGGREGATION ---")
    for i in range(5, -1, -1):
        target_month = now - relativedelta(months=i)
        months_labels.append(target_month.strftime("%b"))
        
        # Calculate revenue for that specific month
        m_rev_qs = JournalLine.objects.filter(
            account__account_type__iexact='Revenue', 
            journal_entry__date__year=target_month.year,
            journal_entry__date__month=target_month.month
        )
        m_rev_agg = m_rev_qs.aggregate(Sum('credit'))
        m_rev = m_rev_agg['credit__sum'] or 0
        
        # Calculate expenses for that specific month
        m_exp_qs = JournalLine.objects.filter(
            account__account_type__iexact='Expense', 
            journal_entry__date__year=target_month.year,
            journal_entry__date__month=target_month.month
        )
        m_exp_agg = m_exp_qs.aggregate(Sum('debit'))
        m_exp = m_exp_agg['debit__sum'] or 0
        
        p_exp = m_exp_qs.filter(account__account_id__startswith='705').aggregate(Sum('debit'))['debit__sum'] or 0
        t_exp = m_exp_qs.filter(account__account_id__startswith='7254').aggregate(Sum('debit'))['debit__sum'] or 0
        g_exp = m_exp_qs.filter(account__account_id__startswith='7250').aggregate(Sum('debit'))['debit__sum'] or 0
        o_exp = float(m_exp) - (float(p_exp) + float(t_exp) + float(g_exp))
        if o_exp < 0: o_exp = 0.0
        
        print(f"DEBUG [{target_month.strftime('%Y-%m')}]: Revenue Query = {m_rev_qs.query}")
        print(f"DEBUG [{target_month.strftime('%Y-%m')}]: Revenue Agg = {m_rev_agg} -> {m_rev}")
        print(f"DEBUG [{target_month.strftime('%Y-%m')}]: Expense Query = {m_exp_qs.query}")
        print(f"DEBUG [{target_month.strftime('%Y-%m')}]: Expense Agg = {m_exp_agg} -> {m_exp}")
        
        rev_data.append(float(m_rev))
        exp_data.append(float(m_exp))
        payroll_data.append(float(p_exp))
        tax_data.append(float(t_exp))
        general_data.append(float(g_exp))
        other_exp_data.append(float(o_exp))
    print("--- DEBUG: END 6-MONTH CHART AGGREGATION ---\n")

    # Chart 2: Current Month Expense Breakdown (Doughnut Chart)
    # Grouping by first two digits (e.g., 70=Payroll, 72=OPEX/Taxes) or by name
    print("--- DEBUG: STARTING MONTHLY EXPENSE BREAKDOWN ---")
    
    current_year = now.year
    monthly_doughnut_data = {}
    
    for m in range(1, 13):
        m_name = datetime.date(current_year, m, 1).strftime("%b")
        
        payroll_qs = JournalLine.objects.filter(account__account_id__startswith='705', journal_entry__date__month=m, journal_entry__date__year=current_year)
        payroll_exp = float(payroll_qs.aggregate(Sum('debit'))['debit__sum'] or 0)
        
        tax_qs = JournalLine.objects.filter(account__account_id__startswith='7254', journal_entry__date__month=m, journal_entry__date__year=current_year)
        tax_exp = float(tax_qs.aggregate(Sum('debit'))['debit__sum'] or 0)
        
        general_qs = JournalLine.objects.filter(account__account_id__startswith='7250', journal_entry__date__month=m, journal_entry__date__year=current_year)
        general_exp = float(general_qs.aggregate(Sum('debit'))['debit__sum'] or 0)
        
        monthly_doughnut_data[m_name] = [payroll_exp, tax_exp, general_exp]

    default_month_name = now.strftime("%b")
    current_m_exp = sum(monthly_doughnut_data[default_month_name])
    
    # Fallback to the latest active month if the current month is empty
    latest_expense = JournalLine.objects.filter(account__account_type__iexact='Expense').order_by('-journal_entry__date').first()
    
    if latest_expense and current_m_exp == 0 and latest_expense.journal_entry.date.year == current_year:
        default_month_name = latest_expense.journal_entry.date.strftime("%b")
        print(f"DEBUG: Current month has 0 expenses. Falling back to {default_month_name} for Doughnut chart.")

    print("--- DEBUG: END MONTHLY EXPENSE BREAKDOWN ---\n")

    # Combine into a single JSON-serializable dictionary
    chart_payload = {
        "bar_chart": {
            "labels": months_labels,
            "revenue": rev_data,
            "expenses": exp_data,
            "payroll": payroll_data,
            "tax": tax_data,
            "general": general_data,
            "other": other_exp_data
        },
        "doughnut_chart": {
            "labels": ["Payroll & Benefits", "Tax Accruals", "General OPEX"],
            "monthly_data": monthly_doughnut_data,
            "default_month": default_month_name
        }
    }
    print(f"DEBUG: Final Chart Payload -> {chart_payload}")

    # ---------------------------------------------------------
    # Part 3:  Generate the Executive Summary via Gemini API
    # ---------------------------------------------------------

    # Build the dictionary that the AI prompt is expecting
    expense_breakdown = {
        "Payroll & Benefits": monthly_doughnut_data[default_month_name][0],
        "Tax Accruals": monthly_doughnut_data[default_month_name][1],
        "General OPEX": monthly_doughnut_data[default_month_name][2]
    }

    ai_summary_text = "AI Executive Summary is currently unavailable. The background task may have encountered an API error or is pending its next scheduled run."
    
    print("\n--- DEBUG: STARTING AI EXECUTIVE SUMMARY GENERATION ---")
    try:
        # Initialize client explicitly using your project's configured env variable
        api_key = getattr(settings, 'GEMINI_API_KEY_2', os.getenv("GEMINI_API_KEY_2"))
        print(f"DEBUG [AI]: API Key loaded = {'YES' if api_key else 'NO'}")
        
        if not api_key:
            raise ValueError("GEMINI_API_KEY_2 is missing or empty. Please ensure it is passed to the Cloud Run Job via --set-secrets or environment variables.")
            
        client = genai.Client(api_key=api_key)
        
        # Populate prompt variables
        system_instruction = (
            "You are an expert Corporate Financial Analyst and Chief Financial Officer advisor. "
            "Analyze the corporate snapshot and produce a high-impact, professional summary. "
            "Start directly with the analysis. Max 3-4 sentences. Use markdown bold for numbers."
        )
        
        # Combine system instruction and user content into a single prompt
        # This is the standard and most reliable method used across your project.
        prompt_content = f"""{system_instruction}

        Analyze this financial snapshot:
        - Reporting Period: {period}
        - Cash on Hand: ${total_cash:,.2f}
        - Trade Payables (AP): ${total_ap:,.2f}
        - Year-to-Date Net Profit: ${net_profit:,.2f}
        - Current Month Expense Breakdown: {json.dumps(expense_breakdown)}
        """

        print("DEBUG [AI]: Sending prompt to Gemini-2.5-Pro...")
        response = client.models.generate_content(
            model='gemini-2.5-pro',
            contents=prompt_content,
            config=types.GenerateContentConfig(
                temperature=0.2, # Low temperature ensures consistent, professional terminology
            )
        )
        if response.text:
            raw_summary = response.text.strip()
            # Convert Markdown bold (**text**) to HTML (<strong>text</strong>)
            ai_summary_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', raw_summary)
            print("DEBUG [AI]: Summary successfully generated.")
        else:
            print("DEBUG [AI]: Warning - Gemini returned an empty response.")
            ai_summary_text = "⚠️ AI Generation Failed: Gemini returned an empty response."
            
    except Exception as e:
        print(f"DEBUG [AI]: Gemini API execution anomaly encountered: {str(e)}")
        ai_summary_text = f"**AI System Error:** {str(e)}"

    print("--- DEBUG: END AI EXECUTIVE SUMMARY GENERATION ---\n")

    # Save to the isolated tenant schema
    snapshot = DashboardSnapshot.objects.create(
        period_label=period,
        total_cash_usd=total_cash,
        total_ap_usd=total_ap,
        net_profit_usd=net_profit,
        chart_data_payload=chart_payload,
        exchange_rate=current_exchange_rate,
        ai_executive_summary=ai_summary_text
    )
    
    return snapshot
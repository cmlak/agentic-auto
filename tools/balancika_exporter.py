import pandas as pd
import re
import io
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from django.db.models import QuerySet

def clean_invoice_number(val):
    """Helper to fix scientific notation in invoice numbers."""
    s = str(val).strip()
    if s.lower() in ['nan', 'none', 'n/a', '']:
        return ''
    if re.match(r'^-?\d+(\.\d+)?[eE][+\-]?\d+$', s):
        try:
            return '{:.0f}'.format(float(s))
        except:
            return s
    return s.replace('.0', '')

def export_to_balancika(
    purchase_queryset: QuerySet,
    base_month_int: int,
    entry_no_start: int,
    current_year: int
) -> io.BytesIO:
    """
    Processes Purchase model instances and generates a two-sheet Excel file
    for Balancika system upload.

    Args:
        purchase_queryset: A Django QuerySet of Purchase model instances.
        base_month_int: The base month (1-12) for date fallbacks.
        entry_no_start: The starting number for the 'Entry No' sequence.
        current_year: The current year to use for date fallbacks.

    Returns:
        An io.BytesIO object containing the Excel file.
    """

    sheet1_data = []
    sheet2_data = []
    entry_counter = entry_no_start

    # Determine BASE_MONTH_START_STR and BASE_MONTH_END_STR based on base_month_int and current_year
    # This assumes the base month is in the current year if no date is found.
    base_month_date = date(current_year, base_month_int, 1)
    base_month_start_str = base_month_date.strftime('%d-%b-%Y')
    base_month_end_str = (base_month_date + relativedelta(months=1, days=-1)).strftime('%d-%b-%Y')

    for purchase in purchase_queryset.order_by('date', 'id'): # Order for consistent Entry No
        entry_no = f"PIN{entry_counter:05d}"
        entry_counter += 1

        # --- DYNAMIC FIELD MAPPING ---
        # Ensure account_id is a string, even if it's None in the model
        original_acct_id = str(purchase.account_id).strip() if purchase.account_id is not None else ''
        
        # Vendor ID from related Vendor model
        original_vendor_id = purchase.vendor.vendor_id if purchase.vendor else ''
        
        original_invoice = clean_invoice_number(purchase.invoice_no)
        
        # Note/Remark fallback: preference for description_en, then description
        note_remark = purchase.description_en or purchase.description or ''
        note_remark = note_remark.strip()[:250] # Truncate to Balancika limit if any

        # Date Parsing
        final_date = base_month_start_str
        final_due_date = base_month_end_str
        if purchase.date:
            try:
                # Ensure purchase.date is a datetime.date object before formatting
                date_obj = purchase.date
                final_date = date_obj.strftime('%d-%b-%Y')
                # Calculate due date as end of the month of the purchase date
                final_due_date = (date_obj + relativedelta(day=31)).strftime('%d-%b-%Y')
            except Exception:
                # Fallback if date conversion fails for some reason
                pass

        # --- SHEET 1 POPULATION ---
        sheet1_data.append({
            "Entry No": entry_no,
            "Date (dd-MMM-YYYY)": final_date,
            "Type": "Apply to GL Account",
            "Reference": original_invoice,
            "Remark": note_remark,
            "Vendor ID": original_vendor_id,
            "Employee ID": "",
            "Class ID": "",
            "Due Date (dd-MMM-YYYY)": final_due_date,
            "Purchase Order": "",
            "Currency ID": "USD",
            "Exchange Rate": 1
        })

        # --- SHEET 2 SCENARIO LOGIC ---
        # Accessing fields directly from the Purchase instance
        vat_amt = float(purchase.vat_usd or 0.0)
        unreg_amt = float(purchase.unreg_usd or 0.0)
        exempt_amt = float(purchase.exempt_usd or 0.0)
        total_amt = float(purchase.total_usd or 0.0)
        description = purchase.description or '' # Use original description for line items

        desc_lower = description.lower()
        rows_

import csv
import io
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponse
from tools.models import Client
from tools.forms import ClientSelectionForm
from register.models import Profile
from django.core.paginator import Paginator
from .models import Account, AccountMappingRule, JournalEntry, JournalLine
from .filters import ReportFilter, BalanceSheetFilter
from django.db.models.functions import ExtractMonth, ExtractYear
import datetime
from tablib import Dataset 
from .resources import AccountResource, TrialBalanceResource, ProfitAndLossResource, BalanceSheetResource, GeneralLedgerSummaryResource, AccountLedgerDetailResource
from .forms import AccountImportForm

def classify_account(acct_type, acct_name):
    """Centralized logic to classify accounts cleanly and consistently."""
    safe_type = str(acct_type).strip().lower() if acct_type else ''
    
    # Since database types are verified and clean, route strictly based on acct_type
    if 'asset' in safe_type: return 'asset'
    if 'liability' in safe_type: return 'liability'
    if 'equity' in safe_type: return 'equity'
    if 'revenue' in safe_type or 'income' in safe_type: return 'revenue'
    if 'expense' in safe_type or 'cogs' in safe_type: return 'expense'
    
    return 'liability'

@login_required
def upload_mapping_rules_view(request):
    """Superuser view to batch upload AI Mapping Rules via CSV."""
    user = request.user
    if user.is_staff or user.is_superuser:
        clients = Client.objects.all()
    else:
        try:
            clients = user.profile.clients.all()
        except Profile.DoesNotExist:
            clients = Client.objects.none()

    if request.method == "POST":
        print("\n" + "="*50)
        print("📥 STARTING CSV RULE UPLOAD")
        print("="*50)

        client_id = request.POST.get('client_id')
        csv_file = request.FILES.get('csv_file')

        if not client_id or not csv_file:
            print("❌ ABORT: Missing Client ID or CSV File.")
            messages.error(request, "Please select a client and upload a file.")
            return redirect('account:upload_mapping_rules')

        if not csv_file.name.endswith('.csv'):
            print(f"❌ ABORT: Invalid file extension ({csv_file.name}).")
            messages.error(request, 'Error: Please upload a valid .csv file.')
            return redirect('account:upload_mapping_rules')

        if not (user.is_staff or user.is_superuser):
            try:
                if not user.profile.clients.filter(id=client_id).exists():
                    messages.error(request, "You do not have permission to upload mapping rules for this client.")
                    return redirect('account:upload_mapping_rules')
            except Profile.DoesNotExist:
                messages.error(request, "You do not have permission to upload mapping rules for this client.")
                return redirect('account:upload_mapping_rules')

        try:
            client = Client.objects.get(id=client_id)
            print(f"🏢 Client Selected: {client.name} (ID: {client.id})")
            print(f"📄 Processing File: {csv_file.name}")
            
            # Decode the uploaded file into a readable string
            dataset = csv_file.read().decode('utf-8-sig')
            io_string = io.StringIO(dataset)
            
            # Skip the header row
            next(io_string) 

            rules_created = 0
            rules_updated = 0
            
            # Convert to list to track progress and robustly iterate
            csv_rows = list(csv.reader(io_string, delimiter=',', quotechar='"'))
            total_rows = len(csv_rows)
            print(f"📊 Found {total_rows} rows to process.")

            # Read the CSV (Account ID, Account Name, Description/Keywords, Reasoning)
            for i, row in enumerate(csv_rows):
                if len(row) < 4:
                    print(f"⚠️ [Row {i+1}] Skipped (Insufficient Columns): {row}")
                    continue # Skip malformed rows
                    
                account_id = str(row[0]).strip()
                account_name = str(row[1]).strip()
                keywords = str(row[2]).strip()
                guideline = str(row[3]).strip()
                
                print(f"   🔹 [{i+1}/{total_rows}] Account: {account_id}")

                # 1. Ensure the Chart of Account exists for this client
                account, acct_created = Account.objects.get_or_create(
                    client=client,
                    account_id=account_id,
                    defaults={'name': account_name, 'account_type': 'Expense'} # Defaulting to expense
                )

                # 2. Update or Create the AI Mapping Rule
                rule, rule_created = AccountMappingRule.objects.update_or_create(
                    client=client,
                    account=account,
                    defaults={
                        'trigger_keywords': keywords,
                        'ai_guideline': guideline
                    }
                )
                
                if rule_created:
                    print(f"      ✨ Rule Created")
                    rules_created += 1
                else:
                    print(f"      ✏️ Rule Updated")
                    rules_updated += 1

            print("-" * 30)
            print(f"✅ UPLOAD COMPLETE: {rules_created} Created, {rules_updated} Updated.")
            print("=" * 50 + "\n")
            messages.success(request, f"Success! Created {rules_created} new rules and updated {rules_updated} existing rules for {client.name}.")
            
        except Exception as e:
            print(f"❌ CRITICAL ERROR during upload: {str(e)}")
            messages.error(request, f"An error occurred while processing the CSV: {str(e)}")

        return redirect('account:upload_mapping_rules')

    return render(request, 'account/upload_rules.html', {'clients': clients})

@login_required
def trial_balance_view(request):
    """Generates the Trial Balance."""
    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('account:trial_balance')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to view this client's accounts.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    # Apply filter
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)

    # Aggregate Debits and Credits by Account
    accounts_data = report_filter.qs.values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    )

    # Preload ALL accounts to ensure zero-balance accounts are rendered
    all_accounts = Account.objects.filter(client_id=client_id)
    tb_dict = {}
    for acct in all_accounts:
        tb_dict[acct.account_id] = {
            'id': acct.account_id, 'name': acct.name, 'type': acct.account_type,
            'debit': 0.0, 'credit': 0.0, 'classification': classify_account(acct.account_type, acct.name)
        }
        
    for item in accounts_data:
        acct_id = item['account__account_id']
        if acct_id not in tb_dict:
            tb_dict[acct_id] = {
                'id': acct_id, 'name': item['account__name'], 'type': item['account__account_type'],
                'debit': 0.0, 'credit': 0.0, 'classification': classify_account(item['account__account_type'], item['account__name'])
            }
        tb_dict[acct_id]['debit'] += item['total_debit'] or 0.0
        tb_dict[acct_id]['credit'] += item['total_credit'] or 0.0

    trial_balance = []
    total_dr = 0
    total_cr = 0

    for acct_id in sorted(tb_dict.keys()):
        data = tb_dict[acct_id]
        dr = data['debit']
        cr = data['credit']

        if data['classification'] in ['asset', 'expense']:
            net_balance = dr - cr
            is_debit = net_balance > 0
        else:
            net_balance = cr - dr
            is_debit = net_balance < 0

        trial_balance.append({
            'id': data['id'],
            'name': data['name'],
            'type': data['type'],
            'debit': abs(net_balance) if is_debit else 0,
            'credit': abs(net_balance) if not is_debit else 0,
        })
        
        if is_debit:
            total_dr += abs(net_balance)
        else:
            total_cr += abs(net_balance)

    context = {
        'trial_balance': trial_balance,
        'total_dr': total_dr,
        'total_cr': total_cr,
        'is_balanced': round(total_dr, 2) == round(total_cr, 2),
        'client_form': ClientSelectionForm(initial={'client': client_id}),
        'filter': report_filter,
    }
    return render(request, 'account/trial_balance.html', context)


@login_required
def profit_and_loss_view(request):
    """Generates the Income Statement (P&L)."""
    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('account:profit_and_loss')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to view this client's accounts.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)

    accounts_data = report_filter.qs.annotate(
        year=ExtractYear('journal_entry__date'),
        month=ExtractMonth('journal_entry__date')
    ).values(
        'account__account_id', 'account__name', 'account__account_type', 'year', 'month'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    revenues = {}
    expenses = {}
    
    monthly_revenue = [0] * 12
    monthly_expense = [0] * 12
    monthly_net_income = [0] * 12
    total_revenue_all = 0
    total_expense_all = 0
    
    # Preload ALL accounts to ensure zero-balance accounts are rendered
    all_accounts = Account.objects.filter(client_id=client_id)
    for acct in all_accounts:
        cls = classify_account(acct.account_type, acct.name)
        if cls == 'revenue':
            revenues[acct.account_id] = {'name': acct.name, 'months': [0]*12, 'total': 0.0}
        elif cls == 'expense':
            expenses[acct.account_id] = {'name': acct.name, 'months': [0]*12, 'total': 0.0}

    target_year = datetime.date.today().year
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    if start_date_str:
        try: target_year = int(start_date_str.split('-')[0])
        except: pass
    elif end_date_str:
        try: target_year = int(end_date_str.split('-')[0])
        except: pass

    for item in accounts_data:
        mo = item['month']
        yr = item['year']
        
        # Filter out other years from the monthly breakdown columns
        if yr != target_year:
            continue
            
        acct_id = item['account__account_id']
        acct_name = item['account__name']
        acct_type = item['account__account_type']
        
        dr = item['total_debit'] or 0
        cr = item['total_credit'] or 0
        
        cls = classify_account(acct_type, acct_name)
        
        if cls not in ['revenue', 'expense']:
            continue
            
        if cls == 'revenue':
            balance = cr - dr
            if acct_id not in revenues:
                revenues[acct_id] = {'name': acct_name, 'months': [0]*12, 'total': 0}
            if mo:
                revenues[acct_id]['months'][mo - 1] += balance
                monthly_revenue[mo - 1] += balance
            revenues[acct_id]['total'] += balance
            total_revenue_all += balance
        else:
            balance = dr - cr
            if acct_id not in expenses:
                expenses[acct_id] = {'name': acct_name, 'months': [0]*12, 'total': 0}
            if mo:
                expenses[acct_id]['months'][mo - 1] += balance
                monthly_expense[mo - 1] += balance
            expenses[acct_id]['total'] += balance
            total_expense_all += balance

    for i in range(12):
        monthly_net_income[i] = monthly_revenue[i] - monthly_expense[i]

    net_income_all = total_revenue_all - total_expense_all

    context = {
        'revenues': revenues.values(),
        'expenses': expenses.values(),
        'monthly_revenue': monthly_revenue,
        'monthly_expense': monthly_expense,
        'monthly_net_income': monthly_net_income,
        'total_revenue': total_revenue_all,
        'total_expense': total_expense_all,
        'net_income': net_income_all,
        'client_form': ClientSelectionForm(initial={'client': client_id}),
        'filter': report_filter,
        'months_headers': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    }
    return render(request, 'account/profit_and_loss.html', context)


@login_required
def balance_sheet_view(request):
    """Generates the Balance Sheet."""
    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('account:balance_sheet')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to view this client's accounts.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})

    target_year = datetime.date.today().year
    end_date_str = request.GET.get('end_date')
    
    # 1. BASE QUERYSET: Fetch ALL lines. We MUST NOT use a standard FilterSet here
    # to avoid dropping offsetting entries (e.g., filtering out specific accounts).
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    
    if end_date_str:
        try:
            target_year = int(end_date_str.split('-')[0])
            # Strictly apply ONLY the 'As-Of' date to preserve historical opening balances
            base_qs = base_qs.filter(journal_entry__date__lte=end_date_str)
        except (ValueError, IndexError):
            pass

    # Instantiate the filter purely for rendering the UI form, NOT for data execution
    bs_filter = BalanceSheetFilter(request.GET, queryset=JournalLine.objects.none())

    # 2. Execute aggregation on the SAFE base_qs
    bs_data = base_qs.annotate(
        year=ExtractYear('journal_entry__date'),
        month=ExtractMonth('journal_entry__date')
    ).values(
        'account__account_id', 'account__name', 'account__account_type', 'year', 'month'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    assets = {}
    liabilities = {}
    equities = {}
    retained_earnings = [0] * 12
    
    # Preload ALL accounts to ensure zero-balance accounts are rendered
    all_accounts = Account.objects.filter(client_id=client_id)
    for acct in all_accounts:
        cls = classify_account(acct.account_type, acct.name)
        if cls == 'asset': assets[acct.account_id] = {'name': acct.name, 'months': [0]*12}
        elif cls == 'equity': equities[acct.account_id] = {'name': acct.name, 'months': [0]*12}
        elif cls == 'liability': liabilities[acct.account_id] = {'name': acct.name, 'months': [0]*12}

    for item in bs_data:
        acct_id = item['account__account_id']
        if not acct_id:
            continue # Skip broken journal lines safely
            
        yr = item['year']
        mo = item['month']
        
        dr = item['total_debit'] or 0
        cr = item['total_credit'] or 0
        
        cls = classify_account(item['account__account_type'], item['account__name'])
        
        # P&L Routing
        if cls in ['revenue', 'expense']:
            net = cr - dr
            if yr and yr < target_year:
                for i in range(12):
                    retained_earnings[i] += net
            elif yr == target_year and mo:
                for i in range(mo - 1, 12):
                    retained_earnings[i] += net
                    
        # Balance Sheet Routing
        else:
            if cls == 'asset':
                balance = dr - cr
                target_dict = assets
            elif cls == 'equity':
                balance = cr - dr
                target_dict = equities
            else:
                balance = cr - dr
                target_dict = liabilities
                
            if acct_id not in target_dict:
                target_dict[acct_id] = {'name': item['account__name'], 'months': [0]*12}
                
            if yr and yr < target_year:
                for i in range(12):
                    target_dict[acct_id]['months'][i] += balance
            elif yr == target_year and mo:
                for i in range(mo - 1, 12):
                    target_dict[acct_id]['months'][i] += balance

    # Add Net Income to Equity
    equities['RETAINED'] = {'name': 'Current & Retained Earnings', 'months': retained_earnings}

    total_assets = [sum(a['months'][i] for a in assets.values()) for i in range(12)]
    total_liabilities = [sum(l['months'][i] for l in liabilities.values()) for i in range(12)]
    total_equity = [sum(e['months'][i] for e in equities.values()) for i in range(12)]
    total_liabilities_and_equity = [total_liabilities[i] + total_equity[i] for i in range(12)]
    
    # Check for balance safely using rounding
    is_balanced = all(round(total_assets[i], 2) == round(total_liabilities_and_equity[i], 2) for i in range(12))

    context = {
        'assets': assets.values(),
        'liabilities': liabilities.values(),
        'equities': equities.values(),
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity,
        'total_liabilities_and_equity': total_liabilities_and_equity,
        'is_balanced': is_balanced,
        'client_form': ClientSelectionForm(initial={'client': client_id}),
        'filter': bs_filter, # Passed strictly for UI rendering
        'months_headers': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    }
    return render(request, 'account/balance_sheet.html', context)

@login_required
def general_ledger_view(request):
    """List of all accounts with their aggregated all-time balances."""
    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            return redirect('account:general_ledger_list')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to view this client's accounts.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})
    
    # 1. Get all accounts for the client
    db_accounts = Account.objects.filter(client_id=client_id).order_by('account_id')
    
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)

    # 2. Calculate total Debits and Credits per account in a single query
    sums = report_filter.qs.values('account_id').annotate(
        total_dr=Sum('debit'),
        total_cr=Sum('credit')
    )
    
    # Map the sums to a dictionary for quick lookup by account.id
    sum_dict = {item['account_id']: item for item in sums}

    account_list = []
    for acct in db_accounts:
        acct_sums = sum_dict.get(acct.id, {'total_dr': 0, 'total_cr': 0})
        dr = acct_sums['total_dr'] or 0
        cr = acct_sums['total_cr'] or 0
        
        # Calculate normal balance based on account type
        cls = classify_account(acct.account_type, acct.name)
        is_debit_normal = cls in ['asset', 'expense']
                    
        if is_debit_normal:
            balance = dr - cr
        else:
            balance = cr - dr
            
        account_list.append({
            'account_id': acct.account_id, 
            'name': acct.name,
            'account_type': acct.account_type,
            'debit': dr,
            'credit': cr,
            'balance': balance,
        })

    context = {
        'accounts': account_list, 
        'client_form': ClientSelectionForm(initial={'client': client_id}),
        'filter': report_filter,
    }
    return render(request, 'account/gl_list.html', context)

@login_required
def account_ledger_detail_view(request, account_id):
    """Detailed transaction list for a specific account."""
    if request.method == 'POST' and 'client' in request.POST:
        form = ClientSelectionForm(request.POST)
        if form.is_valid():
            selected_client = form.cleaned_data.get('client')
            if selected_client:
                request.session['active_client_id'] = selected_client.id
            else:
                request.session.pop('active_client_id', None)
            # Redirect to the main GL list when switching clients to avoid 404s on account_id
            return redirect('account:general_ledger_list')

    client_id = request.session.get('active_client_id')
    user = request.user
    
    if client_id:
        has_access = user.is_staff or user.is_superuser
        if not has_access:
            try:
                if user.profile.clients.filter(id=client_id).exists():
                    has_access = True
            except Profile.DoesNotExist:
                pass
        if not has_access:
            messages.error(request, "You do not have permission to view this client's accounts.")
            request.session.pop('active_client_id', None)
            client_id = None
            
    if not client_id:
        form = ClientSelectionForm()
        messages.error(request, "Please select an active client.")
        return render(request, 'main.html', {'form': form, 'title': 'Select Client'})
        
    account = get_object_or_404(Account, account_id=account_id, client_id=client_id)
    
    base_qs = JournalLine.objects.filter(
        account=account, 
        journal_entry__client_id=client_id
    ).select_related('journal_entry').order_by('journal_entry__date', 'id')

    report_filter = ReportFilter(request.GET, queryset=base_qs)
    lines = report_filter.qs

    cls = classify_account(account.account_type, account.name)
    is_debit_normal = cls in ['asset', 'expense']

    # Calculate running balance
    running_balance = 0
    ledger_data = []
    
    for line in lines:
        if is_debit_normal:
            running_balance += (line.debit - line.credit)
        else:
            running_balance += (line.credit - line.debit)
            
        ledger_data.append({
            'date': line.journal_entry.date,
            'description': line.description or line.journal_entry.description,
            'source': line.journal_entry.source_type,
            'purchase_id': line.journal_entry.purchase_id,
            'bank_id': line.journal_entry.bank_id,
            'cash_id': line.journal_entry.cash_id,
            'debit': line.debit,
            'credit': line.credit,
            'balance': running_balance
        })

    # Pagination
    paginator = Paginator(ledger_data, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'account': account, 
        'ledger_data': page_obj, 
        'page_obj': page_obj,
        'client_form': ClientSelectionForm(initial={'client': client_id}),
        'filter': report_filter,
    }
    return render(request, 'account/gl_detail.html', context)

@login_required
def import_accounts_view(request):
    """
    Clean view to batch upload Chart of Accounts.
    Accepts standardized CSV/Excel files (pre-processed by transformation script).
    Only creates NEW accounts to prevent duplication.
    """
    user = request.user
    
    if request.method == "POST":
        form = AccountImportForm(request.POST, request.FILES)
        
        # Dynamically limit client selection to user's managed clients
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()

        if form.is_valid():
            client = form.cleaned_data['client']
            import_file = form.cleaned_data['import_file']
            
            # 1. Load the file into a Tablib Dataset
            dataset = Dataset()
            try:
                content = import_file.read()
                if import_file.name.endswith('.csv'):
                    dataset.load(content.decode('utf-8-sig'), format='csv')
                elif import_file.name.endswith('.xlsx'):
                    dataset.load(content, format='xlsx')
                elif import_file.name.endswith('.xls'):
                    dataset.load(content, format='xls')
                else:
                    messages.error(request, "Unsupported file format.")
                    return redirect('account:import_accounts')
            except Exception as e:
                messages.error(request, f"Error reading file: {str(e)}")
                return redirect('account:import_accounts')

            # 2. Standardize headers to map to the Account Model
            header_map = {
                'account id': 'account_id',
                'account_id': 'account_id',
                'account name': 'name',
                'account_name': 'name',
                'type': 'account_type',
                'account type': 'account_type'
            }
            dataset.headers = [header_map.get(str(h).strip().lower(), str(h).strip().lower()) for h in dataset.headers]

            # Failsafe: Ensure account_id exists
            if 'account_id' not in dataset.headers:
                messages.error(request, "Upload failed: Column 'account_id' not found. Ensure you used the transformation script.")
                return redirect('account:import_accounts')

            # 3. LOGIC BLOCK: PREVENT DUPLICATION
            # Get existing IDs for this specific client to perform a delta check
            existing_ids = set(
                Account.objects.filter(client=client)
                .values_list('account_id', flat=True)
            )

            # Create a filtered dataset containing only truly NEW accounts
            new_accounts_dataset = Dataset()
            new_accounts_dataset.headers = ['account_id', 'name', 'account_type', 'client_id']

            # Find indices for the source dataset
            idx_id = dataset.headers.index('account_id')
            idx_name = dataset.headers.index('name')
            idx_type = dataset.headers.index('account_type')

            for row in dataset:
                acc_id = str(row[idx_id]).strip()
                
                # Check against existing IDs in the database
                if acc_id not in existing_ids:
                    new_accounts_dataset.append([
                        acc_id,
                        row[idx_name],
                        row[idx_type],
                        client.id
                    ])

            # 4. Import the filtered data
            if len(new_accounts_dataset) > 0:
                account_resource = AccountResource()
                # dry_run=False since we've already manually validated the delta
                result = account_resource.import_data(new_accounts_dataset, dry_run=False)
                
                messages.success(
                    request, 
                    f"Success: {len(new_accounts_dataset)} new accounts created for {client.name}. "
                    f"Duplicates found in file were ignored."
                )
            else:
                messages.info(request, "All accounts in the uploaded file already exist in the database.")

            return redirect('account:import_accounts')

    else:
        # GET Request: Initialize empty form
        form = AccountImportForm()
        if not (user.is_staff or user.is_superuser):
            try:
                form.fields['client'].queryset = user.profile.clients.all()
            except Profile.DoesNotExist:
                form.fields['client'].queryset = Client.objects.none()

    return render(request, 'account/import_accounts.html', {'form': form})
    
@login_required
def export_trial_balance(request):
    """Exports the Trial Balance report to XLSX."""
    client_id = request.session.get('active_client_id')
    if not client_id:
        messages.error(request, "Please select an active client.")
        return redirect('account:trial_balance')

    # Replicate data fetching from trial_balance_view
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)
    accounts_data = report_filter.qs.values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    )

    all_accounts = Account.objects.filter(client_id=client_id)
    tb_dict = {
        acct.account_id: {
            'id': acct.account_id, 'name': acct.name, 'type': acct.account_type,
            'debit': 0.0, 'credit': 0.0, 'classification': classify_account(acct.account_type, acct.name)
        } for acct in all_accounts
    }

    for item in accounts_data:
        acct_id = item['account__account_id']
        if acct_id not in tb_dict:
            tb_dict[acct_id] = {
                'id': acct_id, 'name': item['account__name'], 'type': item['account__account_type'],
                'debit': 0.0, 'credit': 0.0, 'classification': classify_account(item['account__account_type'], item['account__name'])
            }
        tb_dict[acct_id]['debit'] += item['total_debit'] or 0.0
        tb_dict[acct_id]['credit'] += item['total_credit'] or 0.0

    trial_balance_data = []
    for acct_id in sorted(tb_dict.keys()):
        data = tb_dict[acct_id]
        dr = data['debit']
        cr = data['credit']
        net_balance = (dr - cr) if data['classification'] in ['asset', 'expense'] else (cr - dr)
        is_debit = net_balance > 0 if data['classification'] in ['asset', 'expense'] else net_balance < 0

        trial_balance_data.append({
            'id': data['id'],
            'name': data['name'],
            'type': data['type'],
            'debit': abs(net_balance) if is_debit else 0,
            'credit': abs(net_balance) if not is_debit else 0,
        })

    # Export using django-import-export
    resource = TrialBalanceResource()
    dataset = resource.export(trial_balance_data)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Trial_Balance_{client_id}_{datetime.date.today()}.xlsx"'
    return response

@login_required
def export_profit_and_loss(request):
    """Exports the Profit & Loss report to XLSX."""
    client_id = request.session.get('active_client_id')
    if not client_id:
        messages.error(request, "Please select an active client.")
        return redirect('account:profit_and_loss')

    # Replicate data fetching from profit_and_loss_view
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)
    accounts_data = report_filter.qs.values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    export_data = []
    total_revenue = 0
    
    # Revenues
    export_data.append({'category': 'Revenue', 'account_id': '', 'account_name': '', 'total': ''})
    for item in accounts_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls == 'revenue':
            balance = (item['total_credit'] or 0) - (item['total_debit'] or 0)
            if balance != 0:
                export_data.append({
                    'category': 'Revenue',
                    'account_id': item['account__account_id'],
                    'account_name': item['account__name'],
                    'total': balance
                })
                total_revenue += balance
    export_data.append({'category': 'Total Revenue', 'account_id': '', 'account_name': '', 'total': total_revenue})
    export_data.append({'category': '', 'account_id': '', 'account_name': '', 'total': ''}) # Spacer

    # Expenses
    total_expense = 0
    export_data.append({'category': 'Expenses', 'account_id': '', 'account_name': '', 'total': ''})
    for item in accounts_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls == 'expense':
            balance = (item['total_debit'] or 0) - (item['total_credit'] or 0)
            if balance != 0:
                export_data.append({
                    'category': 'Expense',
                    'account_id': item['account__account_id'],
                    'account_name': item['account__name'],
                    'total': balance
                })
                total_expense += balance
    export_data.append({'category': 'Total Expenses', 'account_id': '', 'account_name': '', 'total': total_expense})
    export_data.append({'category': '', 'account_id': '', 'account_name': '', 'total': ''}) # Spacer

    # Net Income
    net_income = total_revenue - total_expense
    export_data.append({'category': 'Net Income', 'account_id': '', 'account_name': '', 'total': net_income})

    resource = ProfitAndLossResource()
    dataset = resource.export(export_data)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Profit_and_Loss_{client_id}_{datetime.date.today()}.xlsx"'
    return response

@login_required
def export_balance_sheet(request):
    """Exports the Balance Sheet report to XLSX."""
    client_id = request.session.get('active_client_id')
    if not client_id:
        messages.error(request, "Please select an active client.")
        return redirect('account:balance_sheet')

    end_date_str = request.GET.get('end_date')
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    if end_date_str:
        base_qs = base_qs.filter(journal_entry__date__lte=end_date_str)

    bs_data = base_qs.values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    export_data = []
    
    # Calculate Retained Earnings
    retained_earnings = 0
    for item in bs_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls in ['revenue', 'expense']:
            net = (item['total_credit'] or 0) - (item['total_debit'] or 0)
            retained_earnings += net

    # Assets
    total_assets = 0
    export_data.append({'category': 'Assets', 'account_id': '', 'account_name': '', 'balance': ''})
    for item in bs_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls == 'asset':
            balance = (item['total_debit'] or 0) - (item['total_credit'] or 0)
            if balance != 0:
                export_data.append({'category': 'Asset', 'account_id': item['account__account_id'], 'account_name': item['account__name'], 'balance': balance})
                total_assets += balance
    export_data.append({'category': 'Total Assets', 'account_id': '', 'account_name': '', 'balance': total_assets})
    export_data.append({'category': '', 'account_id': '', 'account_name': '', 'balance': ''})

    # Liabilities
    total_liabilities = 0
    export_data.append({'category': 'Liabilities', 'account_id': '', 'account_name': '', 'balance': ''})
    for item in bs_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls == 'liability':
            balance = (item['total_credit'] or 0) - (item['total_debit'] or 0)
            if balance != 0:
                export_data.append({'category': 'Liability', 'account_id': item['account__account_id'], 'account_name': item['account__name'], 'balance': balance})
                total_liabilities += balance
    export_data.append({'category': 'Total Liabilities', 'account_id': '', 'account_name': '', 'balance': total_liabilities})
    export_data.append({'category': '', 'account_id': '', 'account_name': '', 'balance': ''})

    # Equity
    total_equity = 0
    export_data.append({'category': 'Equity', 'account_id': '', 'account_name': '', 'balance': ''})
    for item in bs_data:
        cls = classify_account(item['account__account_type'], item['account__name'])
        if cls == 'equity':
            balance = (item['total_credit'] or 0) - (item['total_debit'] or 0)
            if balance != 0:
                export_data.append({'category': 'Equity', 'account_id': item['account__account_id'], 'account_name': item['account__name'], 'balance': balance})
                total_equity += balance
    
    # Add Retained Earnings to Equity
    export_data.append({'category': 'Equity', 'account_id': 'RETAINED', 'account_name': 'Current & Retained Earnings', 'balance': retained_earnings})
    total_equity += retained_earnings
    export_data.append({'category': 'Total Equity', 'account_id': '', 'account_name': '', 'balance': total_equity})
    export_data.append({'category': '', 'account_id': '', 'account_name': '', 'balance': ''})

    # Total Liabilities and Equity
    total_liab_equity = total_liabilities + total_equity
    export_data.append({'category': 'Total Liabilities and Equity', 'account_id': '', 'account_name': '', 'balance': total_liab_equity})

    resource = BalanceSheetResource()
    dataset = resource.export(export_data)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Balance_Sheet_{client_id}_{datetime.date.today()}.xlsx"'
    return response

@login_required
def export_general_ledger_summary(request):
    """Exports the General Ledger summary to XLSX."""
    client_id = request.session.get('active_client_id')
    if not client_id:
        messages.error(request, "Please select an active client.")
        return redirect('account:general_ledger_list')

    # Replicate data fetching from general_ledger_view
    db_accounts = Account.objects.filter(client_id=client_id).order_by('account_id')
    base_qs = JournalLine.objects.filter(journal_entry__client_id=client_id)
    report_filter = ReportFilter(request.GET, queryset=base_qs)
    sums = report_filter.qs.values('account_id').annotate(
        total_dr=Sum('debit'),
        total_cr=Sum('credit')
    )
    sum_dict = {item['account_id']: item for item in sums}

    account_list = []
    for acct in db_accounts:
        acct_sums = sum_dict.get(acct.id, {'total_dr': 0, 'total_cr': 0})
        dr = acct_sums['total_dr'] or 0
        cr = acct_sums['total_cr'] or 0
        
        cls = classify_account(acct.account_type, acct.name)
        is_debit_normal = cls in ['asset', 'expense']
        balance = (dr - cr) if is_debit_normal else (cr - dr)
            
        account_list.append({
            'account_id': acct.account_id, 
            'name': acct.name,
            'account_type': acct.account_type,
            'debit': dr,
            'credit': cr,
            'balance': balance,
        })

    resource = GeneralLedgerSummaryResource()
    dataset = resource.export(account_list)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="GL_Summary_{client_id}_{datetime.date.today()}.xlsx"'
    return response

@login_required
def export_account_ledger_detail(request, account_id):
    """Exports the detailed transaction list for a specific account to XLSX."""
    client_id = request.session.get('active_client_id')
    if not client_id:
        messages.error(request, "Please select an active client.")
        return redirect('account:general_ledger_list')

    account = get_object_or_404(Account, account_id=account_id, client_id=client_id)
    
    # Replicate data fetching from account_ledger_detail_view
    base_qs = JournalLine.objects.filter(
        account=account, 
        journal_entry__client_id=client_id
    ).select_related('journal_entry').order_by('journal_entry__date', 'id')

    report_filter = ReportFilter(request.GET, queryset=base_qs)
    lines = report_filter.qs

    cls = classify_account(account.account_type, account.name)
    is_debit_normal = cls in ['asset', 'expense']

    running_balance = 0
    ledger_data = []
    for line in lines:
        if is_debit_normal:
            running_balance += (line.debit - line.credit)
        else:
            running_balance += (line.credit - line.debit)
            
        ledger_data.append({
            'date': line.journal_entry.date,
            'description': line.description or line.journal_entry.description,
            'source': line.journal_entry.source_type,
            'debit': line.debit,
            'credit': line.credit,
            'balance': running_balance
        })

    resource = AccountLedgerDetailResource()
    dataset = resource.export(ledger_data)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="GL_Detail_{account.account_id}_{client_id}_{datetime.date.today()}.xlsx"'
    return response
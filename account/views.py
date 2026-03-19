
import csv
import io
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.db.models import Sum
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from tools.models import Client
from tools.forms import ClientSelectionForm
from register.models import Profile
from .models import Account, AccountMappingRule, JournalEntry, JournalLine

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

    # Aggregate Debits and Credits by Account
    accounts_data = JournalLine.objects.filter(journal_entry__client_id=client_id).values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    trial_balance = []
    total_dr = 0
    total_cr = 0

    for item in accounts_data:
        dr = item['total_debit'] or 0
        cr = item['total_credit'] or 0
        
        # Calculate net balance based on normal account balances
        if item['account__account_type'] in ['Asset', 'Expense']:
            net_balance = dr - cr
            is_debit = net_balance > 0
        else:
            net_balance = cr - dr
            is_debit = net_balance < 0

        trial_balance.append({
            'id': item['account__account_id'],
            'name': item['account__name'],
            'type': item['account__account_type'],
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
        'client_form': ClientSelectionForm(initial={'client': client_id})
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

    accounts_data = JournalLine.objects.filter(
        journal_entry__client_id=client_id,
        account__account_type__in=['Revenue', 'Expense']
    ).values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    revenues = []
    expenses = []
    total_revenue = 0
    total_expense = 0

    for item in accounts_data:
        dr = item['total_debit'] or 0
        cr = item['total_credit'] or 0
        
        if item['account__account_type'] == 'Revenue':
            balance = cr - dr # Revenue normal balance is Credit
            revenues.append({'name': item['account__name'], 'balance': balance})
            total_revenue += balance
        else:
            balance = dr - cr # Expense normal balance is Debit
            expenses.append({'name': item['account__name'], 'balance': balance})
            total_expense += balance

    net_income = total_revenue - total_expense

    context = {
        'revenues': revenues,
        'expenses': expenses,
        'total_revenue': total_revenue,
        'total_expense': total_expense,
        'net_income': net_income,
        'client_form': ClientSelectionForm(initial={'client': client_id})
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

    # 1. Calculate Net Income (to add to Equity)
    pl_data = JournalLine.objects.filter(
        journal_entry__client_id=client_id,
        account__account_type__in=['Revenue', 'Expense']
    ).aggregate(
        total_dr=Sum('debit'),
        total_cr=Sum('credit')
    )
    # Net Income = (Revenue Cr - Revenue Dr) - (Expense Dr - Expense Cr) 
    # Simplified: Total P&L Credits - Total P&L Debits
    net_income = (pl_data['total_cr'] or 0) - (pl_data['total_dr'] or 0)

    # 2. Get Assets, Liabilities, Equity
    accounts_data = JournalLine.objects.filter(
        journal_entry__client_id=client_id,
        account__account_type__in=['Asset', 'Liability', 'Equity']
    ).values(
        'account__account_id', 'account__name', 'account__account_type'
    ).annotate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    ).order_by('account__account_id')

    assets = []
    liabilities = []
    equities = []
    total_assets = 0
    total_liabilities = 0
    total_equity = 0

    for item in accounts_data:
        dr = item['total_debit'] or 0
        cr = item['total_credit'] or 0
        
        if item['account__account_type'] == 'Asset':
            balance = dr - cr
            assets.append({'name': item['account__name'], 'balance': balance})
            total_assets += balance
        elif item['account__account_type'] == 'Liability':
            balance = cr - dr
            liabilities.append({'name': item['account__name'], 'balance': balance})
            total_liabilities += balance
        elif item['account__account_type'] == 'Equity':
            balance = cr - dr
            equities.append({'name': item['account__name'], 'balance': balance})
            total_equity += balance

    # Add Net Income to Equity
    equities.append({'name': 'Current Year Earnings', 'balance': net_income})
    total_equity += net_income

    context = {
        'assets': assets,
        'liabilities': liabilities,
        'equities': equities,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity,
        'total_liabilities_and_equity': total_liabilities + total_equity,
        'is_balanced': round(total_assets, 2) == round(total_liabilities + total_equity, 2),
        'client_form': ClientSelectionForm(initial={'client': client_id})
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
    
    # 2. Calculate all-time total Debits and Credits per account in a single query
    sums = JournalLine.objects.filter(journal_entry__client_id=client_id).values('account_id').annotate(
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
        if acct.account_type in ['Asset', 'Expense']:
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

    return render(request, 'account/gl_list.html', {'accounts': account_list, 'client_form': ClientSelectionForm(initial={'client': client_id})})

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
    
    lines = JournalLine.objects.filter(
        account=account, 
        journal_entry__client_id=client_id
    ).select_related('journal_entry').order_by('journal_entry__date', 'id')

    # Calculate running balance
    running_balance = 0
    ledger_data = []
    
    for line in lines:
        if account.account_type in ['Asset', 'Expense']:
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

    return render(request, 'account/gl_detail.html', {'account': account, 'ledger_data': ledger_data, 'client_form': ClientSelectionForm(initial={'client': client_id})})
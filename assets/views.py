import json
import calendar
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from .models import Asset, DepreciationEntry, AssetDisposal
from .forms import AssetRegistrationForm, RunDepreciationForm, AssetDisposalForm, AssetDepreciationFormSet
from account.models import JournalEntry, JournalLine
from tools.models import Purchase
from datetime import date

@login_required(login_url="register:login")
def asset_dashboard(request):
    """Comprehensive Control Dashboard"""
    assets = Asset.objects.all().order_by('-depreciation_start_date')
    
    # Calculate Dashboard Aggregates
    total_cost = sum(a.purchase_cost for a in assets if a.status == 'ACTIVE')
    total_nbv = sum(a.net_book_value for a in assets if a.status == 'ACTIVE')
    
    context = {
        'assets': assets,
        'total_cost': total_cost,
        'total_nbv': total_nbv,
    }
    return render(request, 'assets/dashboard.html', context)

@login_required(login_url="register:login")
def register_asset(request):
    """Handles the creation of a new Fixed Asset"""
    purchases = Purchase.objects.filter(account_id__startswith='1500')
    purchase_costs = {p.id: float(p.total_usd or 0.0) for p in purchases}

    if request.method == 'POST':
        form = AssetRegistrationForm(request.POST)
        form.fields['purchase'].queryset = purchases
        if form.is_valid():
            # Save the new asset
            asset = form.save(commit=False)
            
            # Status defaults to 'ACTIVE' based on our model definition
            asset.save()
            
            messages.success(request, f"Successfully registered new asset: {asset.asset_code}")
            
            # NOTE: If your app is named 'assets', use 'assets:asset_dashboard'
            # If your app is named 'tools', use 'tools:asset_dashboard'
            return redirect('assets:asset_dashboard') 
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        # If GET request, show empty form
        form = AssetRegistrationForm()
        form.fields['purchase'].queryset = purchases

    return render(request, 'assets/asset_registration.html', {'form': form, 'purchase_costs_json': json.dumps(purchase_costs)})

@transaction.atomic
@login_required(login_url="register:login")
def run_monthly_depreciation(request):
    """Automates Monthly Calculation and GL Posting"""
    active_assets = Asset.objects.filter(status='ACTIVE').order_by('asset_code')
    
    if request.method == 'POST':
        form = RunDepreciationForm(request.POST)
        formset = AssetDepreciationFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            run_date = form.cleaned_data['run_date']
            
            # Full Month basic: ensure we include assets placed in service anytime during this month
            _, last_day = calendar.monthrange(run_date.year, run_date.month)
            end_of_month = run_date.replace(day=last_day)
            
            selected_asset_ids = []
            for f in formset:
                if f.cleaned_data and f.cleaned_data.get('select'):
                    selected_asset_ids.append(f.cleaned_data.get('asset_id'))
            
            assets_to_process = active_assets.filter(id__in=selected_asset_ids, depreciation_start_date__lte=end_of_month)
            
            entries_created = 0
            skipped_entries = 0
            for asset in assets_to_process:
                # Prevent running twice in the same month
                if DepreciationEntry.objects.filter(asset=asset, date__month=run_date.month, date__year=run_date.year).exists():
                    skipped_entries += 1
                    continue
                
                # Straight Line Math: (Cost - Salvage) / Useful Life
                if asset.depreciation_method == 'SL':
                    dep_basis = asset.purchase_cost - asset.salvage_value
                    monthly_dep = dep_basis / Decimal(asset.useful_life_months)
                
                # Ensure we don't depreciate below salvage value
                if asset.net_book_value - monthly_dep < asset.salvage_value:
                    monthly_dep = asset.net_book_value - asset.salvage_value
                    
                if monthly_dep <= 0:
                    continue

                # 1. Create Ledger Entry
                entry = DepreciationEntry.objects.create(
                    asset=asset, date=run_date, amount=monthly_dep
                )
                
                # 2. Create GL Journal Entry
                # Debit: Dep Expense | Credit: Acc Dep
                je = JournalEntry.objects.create(
                    date=run_date, description=f"Monthly Dep - {asset.asset_code}", reference_number=f"DEP-{entry.id}"
                )
                JournalLine.objects.create(journal_entry=je, account=asset.dep_expense_account, debit=monthly_dep)
                JournalLine.objects.create(journal_entry=je, account=asset.acc_dep_account, credit=monthly_dep)
                
                entry.journal_entry = je
                entry.save()
                entries_created += 1
                
            if entries_created > 0:
                msg = f"Successfully processed {entries_created} depreciation entries."
                if skipped_entries > 0:
                    msg += f" (Skipped {skipped_entries} assets already depreciated for this month)."
                messages.success(request, msg)
            else:
                if skipped_entries > 0:
                    messages.warning(request, f"No new depreciation entries created. All {skipped_entries} selected assets were already depreciated for this month.")
                else:
                    messages.info(request, "No assets were eligible for depreciation.")
                    
            return redirect('assets:asset_dashboard')
    else:
        today = date.today()
        _, last_day = calendar.monthrange(today.year, today.month)
        default_run_date = today.replace(day=last_day)
        form = RunDepreciationForm(initial={'run_date': default_run_date})

        initial_data = []
        for asset in active_assets:
            initial_data.append({
                'asset_id': asset.id,
                'asset_code': asset.asset_code,
                'asset_type': asset.get_asset_type_display(),
                'purchase_cost': asset.purchase_cost,
                'depreciation_start_date': asset.depreciation_start_date,
                'select': True
            })
        formset = AssetDepreciationFormSet(initial=initial_data)
        
    return render(request, 'assets/run_depreciation.html', {'form': form, 'formset': formset})

@transaction.atomic
@login_required(login_url="register:login")
def dispose_asset(request, asset_id):
    """Records Proceeds, Calculates Gain/Loss, and Clears Asset from GL"""
    asset = get_object_or_404(Asset, id=asset_id, status='ACTIVE')
    
    if request.method == 'POST':
        form = AssetDisposalForm(request.POST)
        if form.is_valid():
            disposal = form.save(commit=False)
            disposal.asset = asset
            disposal.net_book_value_at_disposal = asset.net_book_value
            
            # Gain/Loss = Proceeds - Net Book Value
            disposal.gain_loss_amount = disposal.proceeds - asset.net_book_value
            
            # 1. Create GL Journal Entry to wipe asset off the books
            je = JournalEntry.objects.create(
                date=disposal.disposal_date, description=f"Disposal - {asset.asset_code}"
            )
            
            # Clear Accumulated Dep (Debit)
            JournalLine.objects.create(journal_entry=je, account=asset.acc_dep_account, debit=asset.accumulated_depreciation)
            # Record Proceeds (Debit - e.g., to a clearing/bank account. Assuming user maps this via disposal_income_account temporarily or we hardcode a Cash Equivalent)
            # *Note: Usually proceeds debit Cash/AR. For simplicity, we debit the account they select, or you add a 'Proceeds Account' field.*
            
            # Clear Asset Cost (Credit)
            JournalLine.objects.create(journal_entry=je, account=asset.asset_account, credit=asset.purchase_cost)
            
            # Plug the Gain/Loss
            if disposal.gain_loss_amount > 0:
                # Gain = Credit
                JournalLine.objects.create(journal_entry=je, account=disposal.disposal_income_account, credit=disposal.gain_loss_amount)
            elif disposal.gain_loss_amount < 0:
                # Loss = Debit
                JournalLine.objects.create(journal_entry=je, account=disposal.disposal_income_account, debit=abs(disposal.gain_loss_amount))

            disposal.journal_entry = je
            disposal.save()
            
            # 2. Update Asset Status
            asset.status = 'DISPOSED'
            asset.save()
            
            messages.success(request, f"Asset {asset.asset_code} disposed. Gain/Loss: ${disposal.gain_loss_amount}")
            return redirect('assets:asset_dashboard')
    else:
        form = AssetDisposalForm()
        
    return render(request, 'assets/dispose.html', {'form': form, 'asset': asset})
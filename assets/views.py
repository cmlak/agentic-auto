import json
import calendar
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from .models import Asset, DepreciationEntry, AssetDisposal
from .forms import AssetRegistrationForm, RunDepreciationForm, AssetDisposalForm, AssetDepreciationFormSet
from .forms import AssetForm, DepreciationEntryForm
from .filters import AssetFilter, DepreciationEntryFilter, AssetDisposalFilter
from account.models import JournalEntry, JournalLine, Account
from tools.models import Purchase
from datetime import date
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from .resources import AssetResource, DepreciationEntryResource, AssetDisposalResource, DepreciationScheduleResource

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


class AssetListView(LoginRequiredMixin, ListView):
    login_url = "register:login"
    model = Asset
    template_name = 'assets/asset_list.html'
    context_object_name = 'assets'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-id')
        self.filterset = AssetFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.filterset.form
        return context

# class AssetCreateView(LoginRequiredMixin, CreateView):
#     login_url = "register:login"
#     model = Asset
#     form_class = AssetForm
#     template_name = 'assets/asset_form.html'
#     success_url = reverse_lazy('assets:asset_list')

class AssetUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = Asset
    form_class = AssetForm
    template_name = 'assets/asset_form.html'
    success_url = reverse_lazy('assets:asset_list')

    def form_valid(self, form):
        with transaction.atomic():
            asset = form.save()

            if asset.purchase:
                purchase = asset.purchase
                
                # Update related Purchase fields based on changes in the Asset form
                purchase.total_usd = float(asset.purchase_cost) + float(purchase.vat_usd or 0.0)
                purchase.account_id = asset.asset_account.account_id
                purchase.save()

                # Atomically recalculate the Journal Entry for the acquisition
                je, created = JournalEntry.objects.get_or_create(
                    purchase=purchase,
                    defaults={
                        'date': purchase.date or date.today(),
                        'description': f"Purchase: {purchase.company}",
                        'reference_number': purchase.invoice_no,
                    }
                )

                if not created:
                    je.date = purchase.date or date.today()
                    je.description = f"Updated Purchase via Asset: {purchase.company}"
                    je.reference_number = purchase.invoice_no
                    je.save(update_fields=['date', 'description', 'reference_number'])
                    je.lines.all().delete()

                # Re-create JE lines using consistent logic from tools app
                total_amount = float(purchase.total_usd or 0.0)
                vat_amount = float(purchase.vat_usd or 0.0)
                unreg_amount = float(purchase.unreg_usd or 0.0)
                
                wht_amount = 0.0
                if purchase.wht_account_id and unreg_amount > 0:
                    wht_amount = round(total_amount - unreg_amount, 2)

                main_net = round(total_amount - vat_amount - wht_amount, 2)

                if asset.asset_account and main_net > 0:
                    JournalLine.objects.create(journal_entry=je, account=asset.asset_account, description=purchase.description_en or "Asset Purchase", debit=main_net)

                if vat_amount > 0 and purchase.vat_account_id:
                    vat_acct, _ = Account.objects.get_or_create(account_id=str(purchase.vat_account_id), defaults={'name': 'VAT input', 'account_type': 'Asset'})
                    JournalLine.objects.create(journal_entry=je, account=vat_acct, description="Input VAT", debit=vat_amount)

                if total_amount > 0 and purchase.credit_account_id:
                    cr_acct, _ = Account.objects.get_or_create(account_id=str(purchase.credit_account_id), defaults={'name': 'Trade Payable', 'account_type': 'Liability'})
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, description=f"Payable - {purchase.company}", credit=total_amount)

                if wht_amount > 0 and purchase.wht_account_id:
                    wht_pay_acct, _ = Account.objects.get_or_create(account_id=str(purchase.wht_account_id), defaults={'name': 'WHT Payable', 'account_type': 'Liability'})
                    JournalLine.objects.create(journal_entry=je, account=wht_pay_acct, description="WHT Payable to GDT", credit=wht_amount)
                
                messages.success(self.request, f"Asset '{asset.asset_code}' and its related acquisition Journal Entry have been updated successfully.")
            else:
                messages.success(self.request, f"Asset '{asset.asset_code}' was updated. No acquisition Journal Entry was modified as no purchase is linked.")

        return redirect(self.get_success_url())

class AssetDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = Asset
    template_name = 'assets/asset_confirm_delete.html'
    success_url = reverse_lazy('assets:asset_list')

    def form_valid(self, form):
        asset = self.object
        je_ids_to_delete = []

        with transaction.atomic():
            # 1. Collect Depreciation Journal Entries
            for dep_entry in asset.depreciation_entries.exclude(journal_entry__isnull=True):
                je_ids_to_delete.append(dep_entry.journal_entry_id)

            # 2. Collect Disposal Journal Entry
            disposal = AssetDisposal.objects.filter(asset=asset).first()
            if disposal and disposal.journal_entry_id:
                je_ids_to_delete.append(disposal.journal_entry_id)

            # Delete associated Journal Entries (JournalLines will also be deleted via CASCADE)
            if je_ids_to_delete:
                JournalEntry.objects.filter(id__in=je_ids_to_delete).delete()

            # The asset's related DepreciationEntry and AssetDisposal instances 
            # are automatically deleted here due to on_delete=models.CASCADE
            response = super().form_valid(form)
            
        messages.success(self.request, f"Asset '{asset.asset_code}' and its associated depreciation and journal entries have been deleted successfully.")
        return response


class DepreciationEntryListView(LoginRequiredMixin, ListView):
    login_url = "register:login"
    model = DepreciationEntry
    template_name = 'assets/depreciation_entry_list.html'
    context_object_name = 'entries'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-date', '-id')
        self.filterset = DepreciationEntryFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.filterset.form
        return context

class DepreciationEntryCreateView(LoginRequiredMixin, CreateView):
    login_url = "register:login"
    model = DepreciationEntry
    form_class = DepreciationEntryForm
    template_name = 'assets/depreciation_entry_form.html'
    success_url = reverse_lazy('assets:depreciation_entry_list')

    def form_valid(self, form):
        with transaction.atomic():
            entry = form.save()
            asset = entry.asset
            
            je = JournalEntry.objects.create(
                date=entry.date, 
                description=f"Manual Dep - {asset.asset_code}", 
                reference_number=f"DEP-{entry.id}"
            )
            JournalLine.objects.create(journal_entry=je, account=asset.dep_expense_account, debit=entry.amount)
            JournalLine.objects.create(journal_entry=je, account=asset.acc_dep_account, credit=entry.amount)
            
            entry.journal_entry = je
            entry.save()
            messages.success(self.request, f"Depreciation entry for {asset.asset_code} created successfully.")
        return redirect(self.get_success_url())

class DepreciationEntryUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = DepreciationEntry
    form_class = DepreciationEntryForm
    template_name = 'assets/depreciation_entry_form.html'
    success_url = reverse_lazy('assets:depreciation_entry_list')

    def form_valid(self, form):
        with transaction.atomic():
            entry = form.save()
            asset = entry.asset

            je, created = JournalEntry.objects.get_or_create(
                id=entry.journal_entry_id if entry.journal_entry_id else None,
                defaults={
                    'date': entry.date,
                    'description': f"Updated Dep - {asset.asset_code}",
                    'reference_number': f"DEP-{entry.id}"
                }
            )
            
            if not created:
                je.date = entry.date
                je.description = f"Updated Dep - {asset.asset_code}"
                je.save(update_fields=['date', 'description'])
                je.lines.all().delete()
            
            JournalLine.objects.create(journal_entry=je, account=asset.dep_expense_account, debit=entry.amount)
            JournalLine.objects.create(journal_entry=je, account=asset.acc_dep_account, credit=entry.amount)
            
            if not entry.journal_entry:
                entry.journal_entry = je
                entry.save()
            
            messages.success(self.request, f"Depreciation entry for {asset.asset_code} updated successfully.")
        return redirect(self.get_success_url())

class DepreciationEntryDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = DepreciationEntry
    template_name = 'assets/depreciation_entry_confirm_delete.html'
    success_url = reverse_lazy('assets:depreciation_entry_list')

    def form_valid(self, form):
        entry = self.object
        with transaction.atomic():
            if entry.journal_entry:
                entry.journal_entry.delete()
            response = super().form_valid(form)
        messages.success(self.request, f"Depreciation entry deleted successfully.")
        return response

class AssetDisposalListView(LoginRequiredMixin, ListView):
    login_url = "register:login"
    model = AssetDisposal
    template_name = 'assets/asset_disposal_list.html'
    context_object_name = 'disposals'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-disposal_date', '-id')
        self.filterset = AssetDisposalFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.filterset.form
        return context

class AssetDisposalUpdateView(LoginRequiredMixin, UpdateView):
    login_url = "register:login"
    model = AssetDisposal
    form_class = AssetDisposalForm
    template_name = 'assets/asset_disposal_form.html'
    success_url = reverse_lazy('assets:asset_disposal_list')

    def form_valid(self, form):
        with transaction.atomic():
            disposal = form.save(commit=False)
            asset = disposal.asset
            
            disposal.gain_loss_amount = disposal.proceeds - disposal.net_book_value_at_disposal
            disposal.save()

            je, created = JournalEntry.objects.get_or_create(
                id=disposal.journal_entry_id if disposal.journal_entry_id else None,
                defaults={
                    'date': disposal.disposal_date,
                    'description': f"Disposal - {asset.asset_code}",
                }
            )
            
            if not created:
                je.date = disposal.disposal_date
                je.description = f"Updated Disposal - {asset.asset_code}"
                je.save(update_fields=['date', 'description'])
                je.lines.all().delete()
            
            accumulated_dep_at_disposal = asset.purchase_cost - disposal.net_book_value_at_disposal
            if accumulated_dep_at_disposal > 0:
                JournalLine.objects.create(journal_entry=je, account=asset.acc_dep_account, debit=accumulated_dep_at_disposal)
            
            if asset.purchase_cost > 0:
                JournalLine.objects.create(journal_entry=je, account=asset.asset_account, credit=asset.purchase_cost)
            
            if disposal.gain_loss_amount > 0:
                JournalLine.objects.create(journal_entry=je, account=disposal.disposal_income_account, credit=disposal.gain_loss_amount)
            elif disposal.gain_loss_amount < 0:
                JournalLine.objects.create(journal_entry=je, account=disposal.disposal_income_account, debit=abs(disposal.gain_loss_amount))

            if not disposal.journal_entry:
                disposal.journal_entry = je
                disposal.save()

            messages.success(self.request, f"Asset disposal for {asset.asset_code} updated successfully.")
        return redirect(self.get_success_url())

class AssetDisposalDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = AssetDisposal
    template_name = 'assets/asset_disposal_confirm_delete.html'
    success_url = reverse_lazy('assets:asset_disposal_list')

    def form_valid(self, form):
        disposal = self.object
        with transaction.atomic():
            asset = disposal.asset
            asset.status = 'ACTIVE'
            asset.save()
            
            if disposal.journal_entry:
                disposal.journal_entry.delete()
                
            response = super().form_valid(form)
            
        messages.success(self.request, f"Asset disposal deleted successfully. Asset {asset.asset_code} is now ACTIVE.")
        return response

@login_required(login_url="register:login")
def export_assets(request):
    queryset = Asset.objects.all().order_by('-id')
    filterset = AssetFilter(request.GET, queryset=queryset)
    resource = AssetResource()
    dataset = resource.export(queryset=filterset.qs)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="assets_{date.today()}.xlsx"'
    return response

@login_required(login_url="register:login")
def export_depreciation_entries(request):
    queryset = DepreciationEntry.objects.all().order_by('-date', '-id')
    filterset = DepreciationEntryFilter(request.GET, queryset=queryset)
    resource = DepreciationEntryResource()
    dataset = resource.export(queryset=filterset.qs)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="depreciation_entries_{date.today()}.xlsx"'
    return response

@login_required(login_url="register:login")
def export_asset_disposals(request):
    queryset = AssetDisposal.objects.all().order_by('-disposal_date', '-id')
    filterset = AssetDisposalFilter(request.GET, queryset=queryset)
    resource = AssetDisposalResource()
    dataset = resource.export(queryset=filterset.qs)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="asset_disposals_{date.today()}.xlsx"'
    return response

@login_required(login_url="register:login")
def asset_depreciation_schedule(request, pk):
    """Calculates and projects the depreciation schedule for an asset."""
    asset = get_object_or_404(Asset, pk=pk)
    
    schedule = []
    if asset.depreciation_method == 'SL' and asset.useful_life_months > 0:
        dep_basis = asset.purchase_cost - asset.salvage_value
        monthly_dep = dep_basis / Decimal(asset.useful_life_months)
        
        current_nbv = asset.purchase_cost
        acc_dep = Decimal('0.00')
        current_date = asset.depreciation_start_date
        
        for month in range(1, asset.useful_life_months + 1):
            _, last_day = calendar.monthrange(current_date.year, current_date.month)
            period_date = current_date.replace(day=last_day)
            
            # Adjust the last month's depreciation to prevent rounding remainders
            if month == asset.useful_life_months:
                actual_dep = current_nbv - asset.salvage_value
            else:
                actual_dep = round(monthly_dep, 2)
                
            acc_dep += actual_dep
            current_nbv -= actual_dep
            
            schedule.append({
                'period': month,
                'date': period_date,
                'depreciation_expense': actual_dep,
                'accumulated_depreciation': acc_dep,
                'net_book_value': current_nbv
            })
            
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1, day=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1, day=1)
                
    return render(request, 'assets/depreciation_schedule.html', {'asset': asset, 'schedule': schedule})

@login_required(login_url="register:login")
def export_asset_depreciation_schedule(request, pk):
    """Exports the dynamically calculated depreciation schedule for an asset."""
    asset = get_object_or_404(Asset, pk=pk)
    
    schedule = []
    if asset.depreciation_method == 'SL' and asset.useful_life_months > 0:
        dep_basis = asset.purchase_cost - asset.salvage_value
        monthly_dep = dep_basis / Decimal(asset.useful_life_months)
        
        current_nbv = asset.purchase_cost
        acc_dep = Decimal('0.00')
        current_date = asset.depreciation_start_date
        
        for month in range(1, asset.useful_life_months + 1):
            _, last_day = calendar.monthrange(current_date.year, current_date.month)
            period_date = current_date.replace(day=last_day)
            
            if month == asset.useful_life_months:
                actual_dep = current_nbv - asset.salvage_value
            else:
                actual_dep = round(monthly_dep, 2)
                
            acc_dep += actual_dep
            current_nbv -= actual_dep
            
            schedule.append({
                'period': month,
                'date': period_date.strftime("%Y-%m-%d"),
                'depreciation_expense': float(actual_dep),
                'accumulated_depreciation': float(acc_dep),
                'net_book_value': float(current_nbv)
            })
            
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1, day=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1, day=1)
                
    resource = DepreciationScheduleResource()
    dataset = resource.export(schedule)
    response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Depreciation_Schedule_{asset.asset_code}.xlsx"'
    return response

from .views_cap import capitalization_upload_view, capitalization_review_view, capitalization_list_view, capitalization_edit_view, capitalization_delete_view
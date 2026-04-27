from django import forms
from django.forms import formset_factory
from crispy_forms.helper import FormHelper
from django.urls import reverse_lazy
from datetime import date
from crispy_forms.layout import Layout, Row, Column, Field, Submit, HTML
from .models import Purchase, Vendor, Client, Old, JournalVoucher, AICostLog
from account.models import Account

# ====================================================================
# 1. INITIAL UPLOAD FORMS
# ====================================================================

class BatchUploadForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    invoice_pdf = forms.FileField(
        label="Upload Invoice Batch (PDF)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'})
    )
    batch_name = forms.CharField(
        label="Batch Name", max_length=255, required=True,
        help_text="e.g., CCKT Batch 1 - 10 March 2026",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    ai_prompt = forms.CharField(
        label="Custom AI Instructions (Optional)",
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'e.g., Extract sequences starting from 20260305...'}),
        required=False
    )

class ClientSelectionForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Client / Company",
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select fw-bold border-primary',
            'autocomplete': 'off'
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('client', css_class='form-group col-md-12'),
            ),
            Submit('submit', 'Select Client', css_class='btn btn-primary w-100 mt-3')
        )

# ====================================================================
# 2. HITL (HUMAN-IN-THE-LOOP) REVIEW FORM (WITH ACCRUALS)
# ====================================================================

class PurchaseReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False, widget=forms.Select(attrs={'class': 'form-select fw-bold'}))
    
    # --- DEBITS ---
    account_id = forms.ChoiceField(
        label="Main Debit Account (Current Month)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    vat_account_id = forms.ChoiceField(
        label="VAT Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    wht_debit_account_id = forms.ChoiceField(
        label="WHT Expense (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    
    # --- CREDITS ---
    credit_account_id = forms.ChoiceField(
        label="Main Credit Account (Payable)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Payable (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )
    
    # --- VISUAL AMOUNT FIELDS (UNLOCKED FOR EDITING) ---
    net_amount = forms.CharField(
        label="Net Amount (Main Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end fw-bold text-primary'})
    )
    wht_amount_dr = forms.CharField(
        label="WHT Amount (Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )
    wht_amount_cr = forms.CharField(
        label="WHT Amount (Cr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )

    class Meta:
        model = Purchase
        fields = [
            'batch', 'date', 'invoice_no', 'company', 'vendor', 'vattin', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id',
            'description', 'description_en', 'instruction',
            'unreg_usd', 'exempt_usd',
            'vat_base_usd', 'vat_usd', 'total_usd', 'page', 'payment_status'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'invoice_no': forms.TextInput(attrs={'class': 'form-control fw-bold'}),
            'vattin': forms.TextInput(attrs={'class': 'form-control'}),
            'company': forms.TextInput(attrs={'class': 'form-control fw-bold'}),
            'page': forms.NumberInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'form-control auto-expand'}),
            'description_en': forms.Textarea(attrs={'rows': 1, 'class': 'form-control auto-expand'}),
            'instruction': forms.Textarea(attrs={'rows': 1, 'placeholder': 'Optional AI or manual notes...', 'class': 'form-control auto-expand text-muted'}), 
            'vendor': forms.HiddenInput(), 
            'unreg_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary fw-bold'}),
            'total_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end text-danger fw-bold'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
        }
        labels = {
            'unreg_usd': 'Unregistered (WHT Base)',
            'exempt_usd': 'Exempt (No VAT)',
            'vat_base_usd': 'VAT Base Amount',
            'vat_usd': 'VAT Amount (Dr)',
            'total_usd': 'Gross Payable (Cr)',
            'payment_status': 'Payment Status',
        }

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        account_choices = kwargs.pop('account_choices', None) 
        super().__init__(*args, **kwargs)
        
        # 1. Populate dynamic dropdowns (Vendors & Accounts)
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

        if account_choices:
            account_fields = [
                'account_id', 'vat_account_id', 'wht_debit_account_id', 
                'credit_account_id', 'wht_account_id'
            ]
            for field in account_fields:
                self.fields[field].choices = account_choices
                if self.initial.get(field):
                    self.fields[field].initial = self.initial.get(field)

        # 2. Numbering the formset rows
        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        # 3. Calculate "Main Net Amount" (Gross - VAT)
        t_val = float(self.initial.get('total_usd') or 0)
        v_val = float(self.initial.get('vat_usd') or 0)
        
        if not self.initial.get('net_amount'):
            calculated_net = t_val - v_val
            self.fields['net_amount'].initial = f"{calculated_net:,.2f}"

        # ==========================================================
        # 4. CRISPY FORMS DYNAMIC UI LAYOUT
        # ==========================================================
        account_rows = []

        # Row 1: Main Debit (Current Month Expense)
        account_rows.append(Row(
            Column('account_id', css_class='form-group col-md-9'),
            Column('net_amount', css_class='form-group col-md-3'),
        ))

        # Row 5: VAT Debit
        has_vat = float(self.initial.get('vat_usd', 0) or 0) > 0
        if has_vat or self.initial.get('vat_account_id'):
            account_rows.append(Row(
                Column('vat_account_id', css_class='form-group col-md-9'),
                Column('vat_usd', css_class='form-group col-md-3'), 
            ))
        else:
            self.fields['vat_account_id'].widget = forms.HiddenInput()

        # Row 6: WHT Expense (Debit)
        if self.initial.get('wht_debit_account_id') or self.initial.get('wht_account_id'):
            account_rows.append(Row(
                Column('wht_debit_account_id', css_class='form-group col-md-9'),
                Column('wht_amount_dr', css_class='form-group col-md-3'),
            ))
        else:
            self.fields['wht_debit_account_id'].widget = forms.HiddenInput()

        # Row 7: Main Credit (Payables)
        account_rows.append(Row(
            Column('credit_account_id', css_class='form-group col-md-9'),
            Column('total_usd', css_class='form-group col-md-3'), 
        ))

        # Row 8: WHT Payable (Credit)
        if self.initial.get('wht_account_id') or self.initial.get('wht_debit_account_id'):
            account_rows.append(Row(
                Column('wht_account_id', css_class='form-group col-md-9'),
                Column('wht_amount_cr', css_class='form-group col-md-3'), 
            ))
        else:
            self.fields['wht_account_id'].widget = forms.HiddenInput()

        # --- ASSEMBLE FULL FORM LAYOUT ---
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Row(
                Column('form_number', css_class='form-group col-md-1'),
                Column('batch', css_class='form-group col-md-3'),
                Column('date', css_class='form-group col-md-2'),
                Column('invoice_no', css_class='form-group col-md-3'),
                Column('vattin', css_class='form-group col-md-3'),                
                css_class='mt-4 border-top pt-3 border-2 border-primary' 
            ),
            Row(
                Column('company', css_class='form-group col-md-4'),
                Column('vendor_choice', css_class='form-group col-md-3'),
                Column('payment_status', css_class='form-group col-md-2'),
                Column('page', css_class='form-group col-md-1'),
                Column('DELETE', css_class='form-group col-md-2 text-center bg-danger bg-opacity-10 text-danger fw-bold rounded pt-2 pb-2'),
            ),
            
            # Inject the perfectly aligned double-entry block (Main, Accruals, VAT, Credits)
            *account_rows,
            
            Row(   
                Column('description', css_class='form-group col-md-6'),
                Column('description_en', css_class='form-group col-md-6'),
            ),
            
            Row(
                Column('unreg_usd', css_class='form-group col-md-4'),
                Column('exempt_usd', css_class='form-group col-md-4'),
                Column('vat_base_usd', css_class='form-group col-md-4'),
                css_class='bg-light p-2 rounded mt-2 mb-2 border' 
            ),
            Row(
                Column('instruction', css_class='form-group col-md-12'),
            ),
            Field('vendor', type="hidden")
        )

    def clean(self):
        """Ensure formatting from visual inputs (like commas or $) are stripped before DB save."""
        cleaned_data = super().clean()
        
        # Clean standard money fields if needed
        for f in ['unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd']:
            val = cleaned_data.get(f)
            if val:
                try:
                    cleaned_data[f] = float(str(val).replace(',', '').replace('$', '').strip())
                except ValueError:
                    cleaned_data[f] = 0.0

        return cleaned_data

# ====================================================================
# 3. FORMSET FACTORY
# ====================================================================
PurchaseFormSet = formset_factory(PurchaseReviewForm, extra=0, can_delete=True)


class ManualPurchaseEntryForm(forms.ModelForm):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    vendor_choice = forms.ChoiceField(label="Vendor Selection", required=True)
    
    # DEBITS
    account_id = forms.ChoiceField(
        label="Main Debit Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    vat_account_id = forms.ChoiceField(
        label="VAT Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    wht_debit_account_id = forms.ChoiceField(
        label="WHT Expense (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    
    # CREDITS
    credit_account_id = forms.ChoiceField(
        label="Main Credit Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Payable (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )

    def __init__(self, *args, **kwargs):
        vendor_choices = kwargs.pop('vendor_choices', [])
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        
        self.fields['vendor_choice'].choices = vendor_choices
        self.fields['account_id'].choices = account_choices
        self.fields['vat_account_id'].choices = account_choices
        self.fields['wht_debit_account_id'].choices = account_choices
        self.fields['credit_account_id'].choices = account_choices
        self.fields['wht_account_id'].choices = account_choices

        # Set default values for manual entry
        self.fields['credit_account_id'].initial = '200000' # Default Trade Payable
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('client', css_class='form-group col-md-3'),
                Column('date', css_class='form-group col-md-3'),
                Column('invoice_no', css_class='form-group col-md-3'),
                Column('vattin', css_class='form-group col-md-3'),
            ),
            Row(
                Column('company', css_class='form-group col-md-5'),
                Column('vendor_choice', css_class='form-group col-md-4'),
                Column('payment_status', css_class='form-group col-md-3'),
                css_class='mb-4 border-bottom pb-3'
            ),
            
            # ACCOUNT ROUTING
            Row(Column('account_id', css_class='form-group col-md-12')),
            Row(
                Column('vat_account_id', css_class='form-group col-md-6'),
                Column('wht_debit_account_id', css_class='form-group col-md-6')
            ),
            Row(
                Column('credit_account_id', css_class='form-group col-md-6'),
                Column('wht_account_id', css_class='form-group col-md-6')
            ),
            
            Row(
                Column('description', css_class='form-group col-md-6'),
                Column('description_en', css_class='form-group col-md-6'),
            ),
            
            # FINANCIAL AMOUNTS
            Row(
                Column('unreg_usd', css_class='form-group col-md-2'),
                Column('exempt_usd', css_class='form-group col-md-2'),
                Column('vat_base_usd', css_class='form-group col-md-3'),
                Column('vat_usd', css_class='form-group col-md-2'),
                Column('total_usd', css_class='form-group col-md-3'),
                css_class='bg-light p-3 rounded mt-3 border border-secondary'
            ),
            Field('vendor', type="hidden")
        )

    class Meta:
        model = Purchase
        fields = [
            'client', 'date', 'invoice_no', 'company', 'vendor', 'vattin', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id',
            'description', 'description_en', 'payment_status',
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'description_en': forms.Textarea(attrs={'rows': 2}),
            'unreg_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'number-format text-end text-primary fw-bold'}),
            'total_usd': forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
        }

class GLMigrationUploadForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Target Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    gl_file = forms.FileField(
        label="Upload General Ledger Extract (CSV/Excel)",
        help_text="Must contain columns: Date, Vendor / Customer / Employee, Description, No., Debit, Credit"
    )
    batch_name = forms.CharField(
        label="Migration Batch Name", 
        max_length=255, 
        initial="HISTORICAL-MIGRATION-JAN2026",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    
class GLHistoricalReviewForm(forms.Form):
    gl_no = forms.CharField(label="Voucher/GL No.", required=False)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    account_id = forms.ChoiceField(label="Account")
    description = forms.CharField(label="Entity / Description")
    debit = forms.FloatField(required=False, label="Debit")
    credit = forms.FloatField(required=False, label="Credit")
    instruction = forms.CharField(
        label="AI Reasoning", required=False, 
        widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'text-muted bg-light border-0'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        # Inject 'title' attribute for tooltips on hover
        if self.initial.get('description'):
            self.fields['description'].widget.attrs['title'] = self.initial.get('description')
        if self.initial.get('instruction'):
            self.fields['instruction'].widget.attrs['title'] = self.initial.get('instruction')
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('gl_no', css_class='col-md-2 fw-bold text-primary'),
                Column('date', css_class='col-md-2'),
                Column('account_id', css_class='col-md-3'),
                Column('debit', css_class='col-md-2 text-success'),
                Column('credit', css_class='col-md-2 text-danger'),
                Column('DELETE', css_class='col-md-1 text-center'),
            ),
            Row(
                Column('description', css_class='col-md-6'),
                Column('instruction', css_class='col-md-6'),
            ),
            HTML("<hr>")
        )

# Factory for the new unified form
from django.forms import formset_factory
GLHistoricalFormSet = formset_factory(GLHistoricalReviewForm, extra=0, can_delete=True)

class OldEntryForm(forms.ModelForm):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    account_id = forms.ChoiceField(
        label="GL Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('client', css_class='form-group col-md-4'),
                Column('date', css_class='form-group col-md-4'),
                Column('account_id', css_class='form-group col-md-4'),
            ),
            Row(
                Column('description', css_class='form-group col-md-6'),
                Column('instruction', css_class='form-group col-md-6'),
            ),
            Row(
                Column('debit', css_class='form-group col-md-6'),
                Column('credit', css_class='form-group col-md-6'),
            )
        )

    class Meta:
        model = Old
        fields = ['client', 'date', 'account_id', 'description', 'instruction', 'debit', 'credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'instruction': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional AI/Manual Reasoning...'}),
        }

class JournalVoucherEntryForm(forms.ModelForm):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    account_id = forms.ChoiceField(
        label="GL Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('client', css_class='form-group col-md-8'),
                Column('date', css_class='form-group col-md-4'),
            ),
            Row(
                Column('account_id', css_class='form-group col-md-5'),
                Column('vendor', css_class='form-group col-md-4'),
                Column('payment_status', css_class='form-group col-md-3'),
            ),
            Row(Column('description', css_class='form-group col-md-6'), 
                Column('instruction', css_class='form-group col-md-6'),
            ),
            Row(Column('debit', css_class='form-group col-md-6'), 
            Column('credit', css_class='form-group col-md-6'),
            ),
        )

    class Meta:
        model = JournalVoucher
        fields = ['client', 'date', 'account_id', 'vendor', 'payment_status', 'description', 'instruction', 'debit', 'credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'instruction': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional Reasoning...'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
        }

class BalancikaExportForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        empty_label="--- Select Client ---",
        widget=forms.Select(attrs={'class': 'form-select fw-bold'})
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="Start Date"
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="End Date"
    )
    purchase_id = forms.IntegerField(
        required=False,
        label="Purchase ID",
        help_text="Optional: Export specific purchase invoice",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    bank_id = forms.IntegerField(
        required=False,
        label="Bank ID",
        help_text="Optional: Export specific bank charge",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    entry_no_start = forms.IntegerField(
        initial=1,
        label="Starting Entry Number",
        help_text="e.g., 1 will generate PIN00001",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

class MultiplePDFUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Upload Masterlist Excel File",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx, .xls'})
    )
    # The widget attrs {'multiple': True} allows selecting multiple files in the browser
    pdf_files = forms.FileField(
        widget=forms.FileInput, # Attributes will be set in __init__
        label="Select Proposal PDFs"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set widget attributes here to avoid potential module-level initialization errors
        self.fields['pdf_files'].widget.attrs.update({
            'multiple': True,
            'class': 'form-control',
            'accept': '.pdf'
        })

class EngagementLetterUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Upload Masterlist Excel File",
        widget=forms.FileInput(attrs={'class': 'form-control border-success', 'accept': '.xlsx, .xls'})
    )
    pdf_files = forms.FileField(
        label="Select Engagement Letter PDFs",
        widget=forms.FileInput
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pdf_files'].widget.attrs.update({
            'multiple': True,
            'class': 'form-control border-success',
            'accept': '.pdf'
        })

class MonthlyClosingForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        empty_label="--- Select Client ---",
        widget=forms.Select(attrs={
            'class': 'form-select fw-bold border-primary',
            'autocomplete': 'off'
        })
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="Voucher Date"
    )
    salary_payable = forms.FloatField(
        required=False, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Total Salary Payable (USD)'})
    )
    staff_meals = forms.FloatField(
        required=False, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Total Staff Meals (USD)'})
    )
    # Unified File Upload
    tax_declaration_pdf = forms.FileField(
        required=False, 
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'}), 
        label="Tax Declaration PDF (TOS & Liabilities)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('client', css_class='form-group col-md-3'),
                Column('date', css_class='form-group col-md-3'),
                Column('salary_payable', css_class='form-group col-md-3'),
                Column('staff_meals', css_class='form-group col-md-3'),
            ),
            Row(Column('tax_declaration_pdf', css_class='form-group col-md-12'))
        )

class AccrualForm(forms.Form):
    account_id = forms.ChoiceField(widget=forms.Select(attrs={'class': 'form-select'}))
    
    # Target class added for JS: 'dynamic-vendor-select'
    vendor = forms.ChoiceField(
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select dynamic-vendor-select', 'autocomplete': 'off'})
    )
    
    description = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description'}))
    debit = forms.FloatField(widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Amount (USD)'}))
    payment_status = forms.ChoiceField(
        choices=JournalVoucher.PAYMENT_STATUS_CHOICES, 
        initial='Open', 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-warning'})
    )

    def __init__(self, *args, **kwargs):
        client_id = kwargs.pop('client_id', None)
        account_choices = kwargs.pop('account_choices', [('', '--- Select Account ---')])
        vendor_choices = kwargs.pop('vendor_choices', [('', '--- No Vendor ---')])
        
        super().__init__(*args, **kwargs)
        
        self.fields['account_id'].choices = account_choices
        self.fields['vendor'].choices = vendor_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('account_id', css_class='form-group col-md-2'),
                Column('vendor', css_class='form-group col-md-2'),
                Column('description', css_class='form-group col-md-3'),
                Column('debit', css_class='form-group col-md-2'),
                Column('payment_status', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 text-center mt-4'),
                css_class='align-items-center mb-2 pb-2 border-bottom'
            )
        )

class FXForm(forms.Form):
    account_id = forms.ChoiceField(
        label="FX Gain/Loss Account", 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    bank_account_id = forms.ChoiceField(
        label="KHR Bank Account", 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    description = forms.CharField(
        max_length=255, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description'})
    )
    openning_balance = forms.FloatField(
        label="Opening Bal (USD)", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    ending_balance = forms.FloatField(
        label="Ending Bal (KHR)", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    exchange_rate = forms.FloatField(
        label="FX Rate", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    payment_status = forms.ChoiceField(
        choices=JournalVoucher.PAYMENT_STATUS_CHOICES, 
        initial='Paid', 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-warning'})
    )

    def __init__(self, *args, **kwargs):
        client_id = kwargs.pop('client_id', None)
        account_choices = kwargs.pop('account_choices', [('', '--- Select Account ---')])
        
        # Remove vendor_choices from kwargs so it doesn't throw a KeyError, 
        # as FX forms no longer use vendors.
        kwargs.pop('vendor_choices', None) 
        
        super().__init__(*args, **kwargs)
        
        # Populate both dropdowns with the Chart of Accounts
        self.fields['account_id'].choices = account_choices
        self.fields['bank_account_id'].choices = account_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('account_id', css_class='form-group col-md-2'),
                Column('bank_account_id', css_class='form-group col-md-2'),
                Column('description', css_class='form-group col-md-2'),
                Column('openning_balance', css_class='form-group col-md-1'),
                Column('ending_balance', css_class='form-group col-md-1'),
                Column('exchange_rate', css_class='form-group col-md-1'),
                Column('payment_status', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 text-center mt-4'),
                css_class='align-items-center mb-2 pb-2 border-bottom'
            )
        )

AccrualFormSet = formset_factory(AccrualForm, extra=3, can_delete=True)
FXFormSet = formset_factory(FXForm, extra=3, can_delete=True)
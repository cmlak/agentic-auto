from django import forms
from django.forms import formset_factory
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Field, Submit
from .models import Purchase, Client

class BatchUploadForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-primary'})
    )
    invoice_pdf = forms.FileField(label="Upload Invoice Batch (PDF)")
    batch_name = forms.CharField(
        label="Batch Name", max_length=255, required=True,
        help_text="e.g., CCKT Batch 1 - 10 March 2026"
    )
    ai_prompt = forms.CharField(
        label="Custom AI Instructions (Optional)",
        widget=forms.Textarea(attrs={'rows': 3}),
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

class PurchaseReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False)
    
    # DEBITS
    account_id = forms.ChoiceField(
        label="Main Debit Account", required=False, 
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
        label="Main Credit Account", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Payable (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )
    
    # VISUAL AMOUNT FIELDS - UNLOCKED FOR MANUAL EDITING
    net_amount = forms.CharField(
        label="Net Amount (Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end'})
    )
    wht_amount_dr = forms.CharField(
        label="WHT Amount (Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )
    wht_amount_cr = forms.CharField(
        label="WHT Amount (Cr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        account_choices = kwargs.pop('account_choices', None) 
        super().__init__(*args, **kwargs)
        
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

        if account_choices:
            self.fields['account_id'].choices = account_choices
            self.fields['vat_account_id'].choices = account_choices
            self.fields['wht_debit_account_id'].choices = account_choices
            self.fields['credit_account_id'].choices = account_choices
            self.fields['wht_account_id'].choices = account_choices
            
        if self.initial.get('account_id'):
            self.fields['account_id'].initial = self.initial.get('account_id')
        if self.initial.get('vat_account_id'):
            self.fields['vat_account_id'].initial = self.initial.get('vat_account_id')
        if self.initial.get('wht_debit_account_id'):
            self.fields['wht_debit_account_id'].initial = self.initial.get('wht_debit_account_id')
        if self.initial.get('credit_account_id'):
            self.fields['credit_account_id'].initial = self.initial.get('credit_account_id')
        if self.initial.get('wht_account_id'):
            self.fields['wht_account_id'].initial = self.initial.get('wht_account_id')

        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        t_val = float(self.initial.get('total_usd') or 0)
        v_val = float(self.initial.get('vat_usd') or 0)
        
        # We only set the initial value if one isn't already provided by POST data
        if not self.initial.get('net_amount'):
            self.fields['net_amount'].initial = f"{t_val - v_val:,.2f}"

        # --- DYNAMIC UI LAYOUT ---
        account_rows = []

        # 1. Main Debit Row
        account_rows.append(Row(
            Column('account_id', css_class='form-group col-md-9'),
            Column('net_amount', css_class='form-group col-md-3'),
        ))

        # 2. VAT Debit Row
        has_vat = float(self.initial.get('vat_usd', 0) or 0) > 0
        if has_vat or self.initial.get('vat_account_id'):
            account_rows.append(Row(
                Column('vat_account_id', css_class='form-group col-md-9'),
                Column('vat_usd', css_class='form-group col-md-3'), 
            ))
        else:
            self.fields['vat_account_id'].widget = forms.HiddenInput()

        # 3. WHT Expense Debit Row
        if self.initial.get('wht_debit_account_id') or self.initial.get('wht_account_id'):
            account_rows.append(Row(
                Column('wht_debit_account_id', css_class='form-group col-md-9'),
                Column('wht_amount_dr', css_class='form-group col-md-3'),
            ))
        else:
            self.fields['wht_debit_account_id'].widget = forms.HiddenInput()

        # 4. Main Credit Row
        account_rows.append(Row(
            Column('credit_account_id', css_class='form-group col-md-9'),
            Column('total_usd', css_class='form-group col-md-3'), 
        ))

        # 5. WHT Payable Credit Row
        if self.initial.get('wht_account_id') or self.initial.get('wht_debit_account_id'):
            account_rows.append(Row(
                Column('wht_account_id', css_class='form-group col-md-6'),
                Column('wht_amount_cr', css_class='form-group col-md-3'), 
            ))
        else:
            self.fields['wht_account_id'].widget = forms.HiddenInput()

        # --- CRISPY FORMS LAYOUT ---
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
                Column('company', css_class='form-group col-md-5'),
                Column('vendor_choice', css_class='form-group col-md-4'),
                Column('page', css_class='form-group col-md-1'),
                Column('DELETE', css_class='form-group col-md-1 text-center bg-light rounded'),
            ),
            
            # Inject perfectly aligned double-entry block
            *account_rows,
            
            Row(   
                Column('description', css_class='form-group col-md-6'),
                Column('description_en', css_class='form-group col-md-6'),
            ),
            
            Row(
                Column('unreg_usd', css_class='form-group col-md-4'),
                Column('exempt_usd', css_class='form-group col-md-4'),
                Column('vat_base_usd', css_class='form-group col-md-4'),
                css_class='bg-light p-2 rounded mt-2 mb-2' 
            ),
            Row(
                Column('instruction', css_class='form-group col-md-12'),
            ),
            Field('vendor', type="hidden")
        )

    class Meta:
        model = Purchase
        fields = [
            'batch', 'date', 'invoice_no', 'company', 'vendor', 'vattin', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id',
            'description', 'description_en', 'instruction',
            'unreg_usd', 'exempt_usd',
            'vat_base_usd', 'vat_usd', 'total_usd', 'page'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'description_en': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'instruction': forms.Textarea(attrs={'rows': 1, 'placeholder': 'Optional notes...', 'class': 'auto-expand'}), 
            'vendor': forms.HiddenInput(), 
            'unreg_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'number-format text-end text-primary fw-bold'}),
            'total_usd': forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}),
        }
        labels = {
            'unreg_usd': 'Unregistered (WHT Base)',
            'exempt_usd': 'Exempt (No VAT)',
            'vat_base_usd': 'VAT Base Amount',
            'vat_usd': 'VAT Amount (Dr)',
            'total_usd': 'Gross Payable (Cr)',
        }

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
                Column('company', css_class='form-group col-md-12'),
            ),
            Row(
                Column('vendor_choice', css_class='form-group col-md-12'),
                css_class='mb-4 border-bottom pb-3'
            ),
            
            # ACCOUNT ROUTING
            Row(Column('account_id', css_class='form-group col-md-12')),
            Row(Column('vat_account_id', css_class='form-group col-md-12')),
            Row(Column('wht_debit_account_id', css_class='form-group col-md-12')),
            Row(Column('credit_account_id', css_class='form-group col-md-12')),
            Row(Column('wht_account_id', css_class='form-group col-md-12')),
            
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
            'description', 'description_en',
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

# --- 1. PURCHASE REVIEW FORM ---
class GLPurchaseReviewForm(forms.Form):
    gl_no = forms.CharField(label="ID / Ref", required=False)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    company = forms.CharField(label="Vendor Name")
    description = forms.CharField()
    account_id = forms.ChoiceField(label="Expense Account")
    vat_usd = forms.FloatField(required=False)
    total_usd = forms.FloatField(label="Total (AP)")
    
    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('gl_no', css_class='col-md-1 fw-bold text-primary'),
                Column('date', css_class='col-md-3'),
                Column('company', css_class='col-md-4 text-truncate'),
                Column('description', css_class='col-md-4 text-truncate'),
            ),
            Row(    
                Column('account_id', css_class='col-md-6'),
                Column('vat_usd', css_class='col-md-3'),
                Column('total_usd', css_class='col-md-3 fw-bold text-danger'),
                css_class='mb-4 border-bottom pb-3'
            )
        )

# --- 2. BANK REVIEW FORM ---
class GLBankReviewForm(forms.Form):
    gl_no = forms.CharField(label="ID / Ref", required=False)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    counterparty = forms.CharField(label="Entity / Description")
    ledger_account_id = forms.ChoiceField(label="Target Bank Account")
    debit = forms.FloatField(required=False, label="Money In")
    credit = forms.FloatField(required=False, label="Money Out")

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['ledger_account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('gl_no', css_class='col-md-1 fw-bold text-primary'),
                Column('date', css_class='col-md-3'),
                Column('counterparty', css_class='col-md-8'),
            ),
            Row(
                Column('ledger_account_id', css_class='col-md-6'),
                Column('debit', css_class='col-md-3 text-success'),
                Column('credit', css_class='col-md-3 text-danger'),
                css_class='mb-4 border-bottom pb-3'
            )
        )

# --- 3. CASH REVIEW FORM ---
class GLCashReviewForm(forms.Form):
    gl_no = forms.CharField(label="ID / Ref", required=False)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    counterparty = forms.CharField(label="Entity / Description")
    ledger_account_id = forms.ChoiceField(label="Target Cash Account")
    debit = forms.FloatField(required=False, label="Money In")
    credit = forms.FloatField(required=False, label="Money Out")

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['ledger_account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('gl_no', css_class='col-md-1 fw-bold text-primary'),
                Column('date', css_class='col-md-3'),
                Column('counterparty', css_class='col-md-8'),
            ),
            Row(
                Column('ledger_account_id', css_class='col-md-6'),
                Column('debit', css_class='col-md-3 text-success'),
                Column('credit', css_class='col-md-3 text-danger'),
                css_class='mb-4 border-bottom pb-3'
            )
        )

# Create the Factories
GLPurchaseFormSet = formset_factory(GLPurchaseReviewForm, extra=0, can_delete=True)
GLBankFormSet = formset_factory(GLBankReviewForm, extra=0, can_delete=True)
GLCashFormSet = formset_factory(GLCashReviewForm, extra=0, can_delete=True)
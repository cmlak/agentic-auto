from django import forms
from django.forms import formset_factory
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Field
from tools.models import Client 
from .models import Bank, Cash

# ====================================================================
# --- 1. BANK STATEMENT FORMS ---
# ====================================================================

BANK_PROCESSOR_CHOICES = [
    ('aba_standard', 'Standard ABA Bank Rules'),
    ('canadia_standard', 'Standard Canadia Bank Rules'),
    ('client_b_custom', 'Client B Custom Rules'),
]

class BankBatchUploadForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-success'})
    )
    processor_config = forms.ChoiceField(
        choices=BANK_PROCESSOR_CHOICES, 
        label="Select Bank Configuration",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    bank_pdf = forms.FileField(label="Upload Bank Statement (PDF)")
    batch_name = forms.CharField(
        label="Batch Name", max_length=255, required=True,
        help_text="e.g., ABA Bank - Jan 2026"
    )
    ai_prompt = forms.CharField(
        label="Custom Rules for AI (Optional)",
        widget=forms.Textarea(attrs={'rows': 3}), required=False
    )

class BankReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    
    # --- DOUBLE ENTRY ACCOUNTING FIELDS ---
    debit_account_id = forms.ChoiceField(
        label="Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-success'})
    )
    credit_account_id = forms.ChoiceField(
        label="Account (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-danger'})
    )
    matched_purchase_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    
    # Readonly ensures the user cannot edit it, but the data still submits and saves to the DB
    instruction = forms.CharField(
        label="AI Reasoning", required=False, 
        widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'text-muted bg-light border-0'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        
        # Populate dynamic accounts
        if account_choices:
            self.fields['debit_account_id'].choices = account_choices
            self.fields['credit_account_id'].choices = account_choices
            
        # Bind initial values from the AI's prediction
        if self.initial.get('debit_account_id'): 
            self.fields['debit_account_id'].initial = self.initial.get('debit_account_id')
        if self.initial.get('credit_account_id'): 
            self.fields['credit_account_id'].initial = self.initial.get('credit_account_id')
        if self.initial.get('instruction'): 
            self.fields['instruction'].initial = self.initial.get('instruction')

        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        
        # UI REARRANGED: Side-by-Side Double Entry View
        self.helper.layout = Layout(
            Row(
                Column('form_number', css_class='form-group col-md-1'),
                Column('batch', css_class='form-group col-md-2'),
                Column('date', css_class='form-group col-md-2'),
                Column('bank_ref_id', css_class='form-group col-md-2'),
                Column('sys_id', css_class='form-group col-md-2'),
                Column('trans_type', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 text-center bg-light rounded'),
                css_class='mt-4 border-top pt-3 border-2 border-success'
            ),
            Row(
                Column('counterparty', css_class='form-group col-md-4'),
                Column('purpose', css_class='form-group col-md-8'),
            ),
            Row(
                Column('remark', css_class='form-group col-md-4'),
                Column('raw_remark', css_class='form-group col-md-8'),
            ),
            # --- THE SIDE-BY-SIDE ACCOUNTING GRID ---
            Row(
                Column('debit_account_id', css_class='form-group col-md-4'),
                Column('debit', css_class='form-group col-md-2'),
                Column('credit_account_id', css_class='form-group col-md-4'),
                Column('credit', css_class='form-group col-md-2'),
                css_class='bg-light p-2 rounded mt-2 border border-info align-items-end'
            ),
            Row(
                Column('instruction', css_class='form-group col-md-8'),
                Column('balance', css_class='form-group col-md-4 fw-bold'),
                css_class='mt-2'
            ),
            Field('matched_purchase_id')
        )

    class Meta:
        model = Bank
        exclude = ['client', 'matched_purchase']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'purpose': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'raw_remark': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'debit': forms.TextInput(attrs={'class': 'number-format text-end text-success fw-bold'}),
            'credit': forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}),
            'balance': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }

BankFormSet = formset_factory(BankReviewForm, extra=0, can_delete=True)


# ====================================================================
# --- 2. CASH BOOK FORMS ---
# ====================================================================

CASH_PROCESSOR_CHOICES = [
    ('standard_excel', 'Standard Excel/CSV Parser'),
]

class CashBatchUploadForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), 
        empty_label="--- Select Client ---",
        label="Client / Company",
        widget=forms.Select(attrs={'class': 'form-select fw-bold border-warning'})
    )
    processor_config = forms.ChoiceField(
        choices=CASH_PROCESSOR_CHOICES, 
        label="Select Processor Rules",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    cash_file = forms.FileField(label="Upload Cash Book (Excel / CSV)")
    batch_name = forms.CharField(
        label="Batch Name", max_length=255, required=True,
        help_text="e.g., CCKT Cash Book - Feb 2026"
    )

class CashReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False)
    
    # --- DOUBLE ENTRY ACCOUNTING FIELDS ---
    debit_account_id = forms.ChoiceField(
        label="Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-success'})
    )
    credit_account_id = forms.ChoiceField(
        label="Account (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-danger'})
    )
    matched_purchase_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    
    # Readonly ensures the user cannot edit it, but the data still submits and saves to the DB
    instruction = forms.CharField(
        label="AI Reasoning", required=False, 
        widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'text-muted bg-light border-0'})
    )

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        account_choices = kwargs.pop('account_choices', [])
        start_sequence = kwargs.pop('start_sequence', 0)
        super().__init__(*args, **kwargs)
        
        # Populate dynamic vendors
        if dynamic_choices: 
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'): 
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')
            
        # Populate dynamic accounts
        if account_choices:
            self.fields['debit_account_id'].choices = account_choices
            self.fields['credit_account_id'].choices = account_choices
            
        # Bind initial values from the AI's prediction
        if self.initial.get('debit_account_id'): 
            self.fields['debit_account_id'].initial = self.initial.get('debit_account_id')
        if self.initial.get('credit_account_id'): 
            self.fields['credit_account_id'].initial = self.initial.get('credit_account_id')
        if self.initial.get('instruction'): 
            self.fields['instruction'].initial = self.initial.get('instruction')

        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1])
                self.fields['form_number'].initial = str(start_sequence + form_index + 1)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        
        # UI REARRANGED: Side-by-Side Double Entry View
        self.helper.layout = Layout(
            Row(
                Column('form_number', css_class='form-group col-md-1'),
                Column('batch', css_class='form-group col-md-2'),
                Column('date', css_class='form-group col-md-2'),
                Column('voucher_no', css_class='form-group col-md-2'),
                Column('invoice_no', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 offset-md-2 text-center bg-light rounded'),
                css_class='mt-4 border-top pt-3 border-2 border-warning'
            ),
            Row(
                Column('vendor_choice', css_class='form-group col-md-4'),
                Column('description', css_class='form-group col-md-8'),
            ),
            Row(
                Column('note', css_class='form-group col-md-12'),
            ),
            # --- THE SIDE-BY-SIDE ACCOUNTING GRID ---
            Row(
                Column('debit_account_id', css_class='form-group col-md-4'),
                Column('debit', css_class='form-group col-md-2'),
                Column('credit_account_id', css_class='form-group col-md-4'),
                Column('credit', css_class='form-group col-md-2'),
                css_class='bg-light p-2 rounded mt-2 border border-info align-items-end'
            ),
            Row(
                Column('instruction', css_class='form-group col-md-8'),
                Column('balance', css_class='form-group col-md-4 fw-bold'),
                css_class='mt-2'
            ),
            Field('vendor', type="hidden"),
            Field('matched_purchase_id')
        )

    class Meta:
        model = Cash
        exclude = ['client', 'matched_purchase']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'note': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'debit': forms.TextInput(attrs={'class': 'number-format text-end text-success fw-bold'}),
            'credit': forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}),
            'balance': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }

CashFormSet = formset_factory(CashReviewForm, extra=0, can_delete=True)
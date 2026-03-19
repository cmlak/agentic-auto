from django import forms
from django.forms import formset_factory
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Field
from tools.models import Client
from .models import Bank, Cash

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
        help_text="e.g., ABA Bank - Feb 2026"
    )
    ai_prompt = forms.CharField(
        label="Custom Rules for AI (Optional)",
        widget=forms.Textarea(attrs={'rows': 3}), required=False
    )

    # --- ENHANCEMENT: Dual File Uploads ---
    custom_rules_file = forms.FileField(
        label="Bank Payment Explanation (Optional Excel/CSV)", 
        required=False,
        help_text="Upload client's bank payment explanation file with vendor/invoice mappings."
    )
    historical_gl_file = forms.FileField(
        label="Historical General Ledger (Optional Excel/CSV)", 
        required=False,
        help_text="Upload previous month's GL to allow AI to search for established payables."
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
    matched_purchase_ids = forms.CharField(required=False, widget=forms.HiddenInput())
    
    debit_amount = forms.CharField(label="Debit", required=False, widget=forms.TextInput(attrs={'class': 'number-format text-end text-success fw-bold'}))
    credit_amount = forms.CharField(label="Credit", required=False, widget=forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}))
    
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
        if self.initial.get('debit_amount'): 
            self.fields['debit_amount'].initial = self.initial.get('debit_amount')
        if self.initial.get('credit_amount'): 
            self.fields['credit_amount'].initial = self.initial.get('credit_amount')

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
                # DEBIT SIDE (Takes up 5 columns total)
                # 'pe-2' adds padding-end (right margin) to keep it away from the amount
                Column('debit_account_id', css_class='form-group col-md-3 pe-2'),
                Column('debit_amount', css_class='form-group col-md-2'),
                
                # THE GAP (Takes up 2 empty columns in the middle)
                # 'offset-md-2' creates a massive empty space between Debit Amount and Credit Account
                
                # CREDIT SIDE (Takes up 5 columns total)
                Column('credit_account_id', css_class='form-group col-md-3 offset-md-2 pe-2'),
                Column('credit_amount', css_class='form-group col-md-2'),
                
                # 'gx-3' ensures standard Bootstrap gutters are applied between all items
                css_class='bg-light p-3 rounded mt-2 border border-info align-items-end gx-3'
            ),
            Row(
                Column('instruction', css_class='form-group col-md-8'),
                Column('balance', css_class='form-group col-md-4 fw-bold'),
                css_class='mt-2'
            ),
            Field('debit', type="hidden"),
            Field('credit', type="hidden"),
            Field('matched_purchase_ids')
        )

    class Meta:
        model = Bank
        exclude = ['client', 'matched_purchase']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'purpose': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'raw_remark': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'debit': forms.HiddenInput(),
            'credit': forms.HiddenInput(),
            'balance': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }
        
    def clean(self):
        cleaned_data = super().clean()
        # Sync the edited balanced amounts back to the directional fields for the DB
        d_amt_raw = cleaned_data.get('debit_amount')
        if d_amt_raw is not None and str(d_amt_raw).strip() != "":
            try: d_amt = float(str(d_amt_raw).replace(',', '').replace('$', '').strip())
            except ValueError: d_amt = 0.0
            
            # Preserves Money In / Money Out direction for views.py
            orig_cr = float(cleaned_data.get('credit') or self.initial.get('credit') or 0.0)
            if orig_cr > 0:
                cleaned_data['credit'] = d_amt
                cleaned_data['debit'] = 0.0
            else:
                cleaned_data['debit'] = d_amt
                cleaned_data['credit'] = 0.0
        return cleaned_data

BankFormSet = formset_factory(BankReviewForm, extra=0, can_delete=True)


class ManualBankEntryForm(forms.ModelForm):
    debit_account_id = forms.ChoiceField(label="Debit Account (Dr)", widget=forms.Select(attrs={'class': 'form-select text-success'}))
    credit_account_id = forms.ChoiceField(label="Credit Account (Cr)", widget=forms.Select(attrs={'class': 'form-select text-danger'}))
    
    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['debit_account_id'].choices = account_choices
        self.fields['credit_account_id'].choices = account_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-4'),
                Column('bank_ref_id', css_class='form-group col-md-4'),
                Column('trans_type', css_class='form-group col-md-4'),
            ),
            Row(
                Column('counterparty', css_class='form-group col-md-6'),
                Column('purpose', css_class='form-group col-md-6'),
            ),
            Row(
                Column('remark', css_class='form-group col-md-12'),
            ),
            Row(
                Column('debit_account_id', css_class='form-group col-md-6'),
                Column('credit_account_id', css_class='form-group col-md-6'),
            ),
            Row(
                Column('debit', css_class='form-group col-md-6'),
                Column('credit', css_class='form-group col-md-6'),
                css_class='bg-light p-3 rounded mt-3 border border-secondary'
            )
        )

    class Meta:
        model = Bank
        fields = [
            'date', 'bank_ref_id', 'trans_type', 'counterparty', 'purpose', 'remark', 
            'debit_account_id', 'credit_account_id', 'debit', 'credit'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'purpose': forms.Textarea(attrs={'rows': 2}),
            'debit': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'credit': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        debit = cleaned_data.get('debit') or 0.0
        credit = cleaned_data.get('credit') or 0.0
        if debit > 0 and credit > 0:
            raise forms.ValidationError("A transaction cannot have both Debit and Credit amounts. Choose one.")
        return cleaned_data


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
    matched_purchase_ids = forms.CharField(required=False, widget=forms.HiddenInput())
    
    debit_amount = forms.CharField(label="Debit", required=False, widget=forms.TextInput(attrs={'class': 'number-format text-end text-success fw-bold'}))
    credit_amount = forms.CharField(label="Credit", required=False, widget=forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}))
    
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
        if self.initial.get('debit_amount'): 
            self.fields['debit_amount'].initial = self.initial.get('debit_amount')
        if self.initial.get('credit_amount'): 
            self.fields['credit_amount'].initial = self.initial.get('credit_amount')

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
                Column('debit_account_id', css_class='form-group col-md-3 pe-3'),
                Column('debit_amount', css_class='form-group col-md-2'),
                Column('credit_account_id', css_class='form-group col-md-3 offset-md-1 pe-3'),
                Column('credit_amount', css_class='form-group col-md-2'),
                css_class='bg-light p-3 rounded mt-2 border border-info align-items-end'
            ),
            Row(
                Column('instruction', css_class='form-group col-md-8'),
                Column('balance', css_class='form-group col-md-4 fw-bold'),
                css_class='mt-2'
            ),
            Field('debit', type="hidden"),
            Field('credit', type="hidden"),
            Field('vendor', type="hidden"),
            Field('matched_purchase_ids')
        )

    class Meta:
        model = Cash
        exclude = ['client', 'matched_purchase']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'note': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'debit': forms.HiddenInput(),
            'credit': forms.HiddenInput(),
            'balance': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }
        
    def clean(self):
        cleaned_data = super().clean()
        d_amt_raw = cleaned_data.get('debit_amount')
        if d_amt_raw is not None and str(d_amt_raw).strip() != "":
            try: d_amt = float(str(d_amt_raw).replace(',', '').replace('$', '').strip())
            except ValueError: d_amt = 0.0
            
            orig_cr = float(cleaned_data.get('credit') or self.initial.get('credit') or 0.0)
            if orig_cr > 0:
                cleaned_data['credit'] = d_amt
                cleaned_data['debit'] = 0.0
            else:
                cleaned_data['debit'] = d_amt
                cleaned_data['credit'] = 0.0
        return cleaned_data

CashFormSet = formset_factory(CashReviewForm, extra=0, can_delete=True)


class ManualCashEntryForm(forms.ModelForm):
    vendor_choice = forms.ChoiceField(label="Vendor Selection", required=False)
    debit_account_id = forms.ChoiceField(label="Debit Account (Dr)", widget=forms.Select(attrs={'class': 'form-select text-success'}))
    credit_account_id = forms.ChoiceField(label="Credit Account (Cr)", widget=forms.Select(attrs={'class': 'form-select text-danger'}))
    
    def __init__(self, *args, **kwargs):
        vendor_choices = kwargs.pop('vendor_choices', [])
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['vendor_choice'].choices = vendor_choices
        self.fields['debit_account_id'].choices = account_choices
        self.fields['credit_account_id'].choices = account_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-4'),
                Column('voucher_no', css_class='form-group col-md-4'),
                Column('invoice_no', css_class='form-group col-md-4'),
            ),
            Row(
                Column('vendor_choice', css_class='form-group col-md-12'),
            ),
            Row(
                Column('description', css_class='form-group col-md-12'),
            ),
            Row(
                Column('debit_account_id', css_class='form-group col-md-6'),
                Column('credit_account_id', css_class='form-group col-md-6'),
            ),
            Row(
                Column('debit', css_class='form-group col-md-6'),
                Column('credit', css_class='form-group col-md-6'),
                css_class='bg-light p-3 rounded mt-3 border border-secondary'
            )
        )

    class Meta:
        model = Cash
        fields = [
            'date', 'voucher_no', 'invoice_no', 'description', 
            'debit_account_id', 'credit_account_id', 'debit', 'credit'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'debit': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'credit': forms.TextInput(attrs={'class': 'number-format text-end'}),
        }
        
    def clean(self):
        cleaned_data = super().clean()
        debit = cleaned_data.get('debit') or 0.0
        credit = cleaned_data.get('credit') or 0.0
        if debit > 0 and credit > 0:
            raise forms.ValidationError("A transaction cannot have both Debit and Credit amounts. Choose one.")
        if not debit and not credit:
            raise forms.ValidationError("You must enter either a Debit or a Credit amount.")
        return cleaned_data
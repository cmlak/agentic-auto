from django import forms
from django.forms import formset_factory
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Field
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

class PurchaseReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False)
    
    account_id = forms.ChoiceField(
        label="Main Debit Account", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    vat_account_id = forms.ChoiceField(
        label="VAT Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    credit_account_id = forms.ChoiceField(
        label="Main Credit Account", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Account (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )
    
    # Read-only field to visually display the Net Amount next to the Main Debit account
    net_amount = forms.CharField(
        label="Net Amount (Dr)", required=False, disabled=True, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end text-muted', 'readonly': 'readonly'})
    )

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        account_choices = kwargs.pop('account_choices', None) 
        super().__init__(*args, **kwargs)
        
        # Populate dynamic choices
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

        if account_choices:
            self.fields['account_id'].choices = account_choices
            self.fields['vat_account_id'].choices = account_choices
            self.fields['credit_account_id'].choices = account_choices
            self.fields['wht_account_id'].choices = account_choices
            
        if self.initial.get('account_id'):
            self.fields['account_id'].initial = self.initial.get('account_id')
        if self.initial.get('vat_account_id'):
            self.fields['vat_account_id'].initial = self.initial.get('vat_account_id')
        if self.initial.get('credit_account_id'):
            self.fields['credit_account_id'].initial = self.initial.get('credit_account_id')
        if self.initial.get('wht_account_id'):
            self.fields['wht_account_id'].initial = self.initial.get('wht_account_id')

        # Form numbering based on Formset Index
        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        # ==========================================================
        # --- CALCULATE INITIAL NET AMOUNT ---
        # ==========================================================
        # This helps the user see that the Double Entry balances
        t_val = float(self.initial.get('total_usd') or 0)
        v_val = float(self.initial.get('vat_usd') or 0)
        self.fields['net_amount'].initial = f"{t_val - v_val:,.2f}"

        # ==========================================================
        # --- DYNAMIC LAYOUT LOGIC (Account + Amount Pairing) ---
        # ==========================================================
        
        # Stack account rows vertically for clarity
        account_rows = []

        # 1. Main Debit Row (Account + Net Amount)
        account_rows.append(Row(
            Column('account_id', css_class='form-group col-md-9'),
            Column('net_amount', css_class='form-group col-md-3'),
        ))

        # 2. VAT Row (if applicable)
        has_vat = float(self.initial.get('vat_usd', 0) or 0) > 0
        if has_vat or self.initial.get('vat_account_id'):
            account_rows.append(Row(
                Column('vat_account_id', css_class='form-group col-md-9'),
                Column('vat_usd', css_class='form-group col-md-3'),
            ))
        else:
            self.fields['vat_account_id'].widget = forms.HiddenInput()

        # 3. WHT Row (if applicable)
        if self.initial.get('wht_account_id'):
            account_rows.append(Row(
                Column('wht_account_id', css_class='form-group col-md-9'),
            ))
        else:
            self.fields['wht_account_id'].widget = forms.HiddenInput()

        # 4. Main Credit Row (Account + Total Amount)
        account_rows.append(Row(
            Column('credit_account_id', css_class='form-group col-md-9'),
            Column('total_usd', css_class='form-group col-md-3'),
        ))

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
                Column('vendor_choice', css_class='form-group col-md-5'),
                Column('page', css_class='form-group col-md-1'),
                Column('DELETE', css_class='form-group col-md-1 text-center bg-light rounded'),
            ),
            
            # Inject the stacked account rows (Row-by-Row rendering)
            *account_rows,
            
            Row(   
                Column('description', css_class='form-group col-md-6'),
                Column('description_en', css_class='form-group col-md-6'),
            ),
            
            # ALL 5 AMOUNT COLUMNS AT THE BOTTOM FOR QUICK REVIEW
            Row(
                Column('unreg_usd', css_class='form-group col-md-2'),
                Column('exempt_usd', css_class='form-group col-md-2'),
                Column('vat_base_usd', css_class='form-group col-md-3'),
                Column('vat_usd', css_class='form-group col-md-2'),
                Column('total_usd', css_class='form-group col-md-3'),
                css_class='bg-light p-2 rounded' 
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
            'account_id', 'vat_account_id', 'credit_account_id', 'wht_account_id',
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
            
            # Right-aligned text boxes for amounts to look like a proper ledger
            'unreg_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'total_usd': forms.TextInput(attrs={'class': 'number-format text-end fw-bold text-primary'}),
        }
        labels = {
            'unreg_usd': 'Unregistered (WHT Base)',
            'exempt_usd': 'Exempt (No VAT)',
            'vat_base_usd': 'VAT Base Amount',
            'vat_usd': '10% VAT Amount',
            'total_usd': 'Gross Total',
        }

# Create the formset for batch rendering
PurchaseFormSet = formset_factory(PurchaseReviewForm, extra=0, can_delete=True)
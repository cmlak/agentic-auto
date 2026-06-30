from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError
from assets.models import Asset, AssetDisposal, DepreciationEntry
from tools.models import Purchase
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, Field, Div, HTML

class PurchaseChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        desc = obj.description_en if obj.description_en else (obj.description or "Asset Purchase")
        return f"{obj.invoice_no or 'N/A'} - {desc}"

class AssetRegistrationForm(forms.ModelForm):
    purchase = PurchaseChoiceField(
        queryset=Purchase.objects.none(),
        required=False,
        empty_label="--- Select Purchase ---",
        widget=forms.Select(attrs={'class': 'form-select', 'onchange': 'updatePurchaseCost(this)'})
    )

    class Meta:
        model = Asset
        exclude = ['status', 'salvage_value']
        widgets = {
            'depreciation_start_date': forms.DateInput(attrs={'type': 'date'}),
        }
        help_texts = {
            'depreciation_start_date': "Select the date according to the 'placed in service' principle. A full month's depreciation is charged in this month.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('asset_code', css_class='form-group col-md-4'),
                Column('asset_type', css_class='form-group col-md-4'),
                Column('purchase', css_class='form-group col-md-4'),
            ),
            Row(
                Column('purchase_cost', css_class='form-group col-md-6'),
                Column('depreciation_start_date', css_class='form-group col-md-6'),
            ),
            Row(
                Column('depreciation_method', css_class='form-group col-md-6'),
                Column('useful_life_months', css_class='form-group col-md-6'),
            ),
            Row(
                Column('asset_account', css_class='form-group col-md-4'),
                Column('acc_dep_account', css_class='form-group col-md-4'),
                Column('dep_expense_account', css_class='form-group col-md-4'),
            ),
        )

class DepreciationEntryForm(forms.ModelForm):
    class Meta:
        model = DepreciationEntry
        fields = ['asset', 'date', 'amount']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('asset', css_class='form-group col-md-4'),
                Column('date', css_class='form-group col-md-4'),
                Column('amount', css_class='form-group col-md-4'),
            ),
        )

class RunDepreciationForm(forms.Form):
    run_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text="End of month date for the depreciation run."
    )

class AssetDepreciationSelectForm(forms.Form):
    asset_id = forms.IntegerField(widget=forms.HiddenInput())
    asset_code = forms.CharField(required=False, widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control-plaintext fw-bold'}))
    asset_type = forms.CharField(required=False, widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control-plaintext'}))
    purchase_cost = forms.DecimalField(required=False, max_digits=12, decimal_places=2, widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control-plaintext'}))
    depreciation_start_date = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'readonly': 'readonly', 'class': 'form-control-plaintext text-muted'}))
    select = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={'class': 'form-check-input mt-2'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('select', css_class='form-group col-md-1 d-flex justify-content-center'),
                Column('asset_code', css_class='form-group col-md-3'),
                Column('asset_type', css_class='form-group col-md-3'),
                Column('purchase_cost', css_class='form-group col-md-2'),
                Column('depreciation_start_date', css_class='form-group col-md-3'),
                css_class='align-items-center mb-0 border-bottom py-1'
            ),
            Field('asset_id', type="hidden")
        )

AssetDepreciationFormSet = formset_factory(AssetDepreciationSelectForm, extra=0)

class AssetDisposalForm(forms.ModelForm):
    class Meta:
        model = AssetDisposal
        fields = ['disposal_date', 'proceeds', 'disposal_income_account']
        widgets = {'disposal_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        # Define the layout strictly in Python
        self.helper.layout = Layout(
            Row(
                Column('disposal_date', css_class='form-group col-md-6 mb-0'),
                Column('proceeds', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            'disposal_income_account',
            Submit('submit', 'Confirm Asset Disposal', css_class='btn btn-danger mt-3')
        )

class AssetForm(forms.ModelForm):
    purchase_date = forms.DateField(
        label="Purchase Date",
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'readonly': 'readonly', 'class': 'form-control bg-light'})
    )

    class Meta:
        model = Asset
        fields = '__all__'
        widgets = {
            'depreciation_start_date': forms.DateInput(attrs={'type': 'date'}),
        }
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Pre-populate purchase date on load if instance exists
        if self.instance and self.instance.pk and self.instance.purchase:
            self.fields['purchase_date'].initial = self.instance.purchase.date

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('asset_code', css_class='form-group col-md-4'),
                Column('asset_type', css_class='form-group col-md-4'),
                Column('status', css_class='form-group col-md-4'),
            ),
            Row(
                Column('purchase', css_class='form-group col-md-4'),
                Column('purchase_date', css_class='form-group col-md-4'),
                Column('purchase_cost', css_class='form-group col-md-4'),
            ),
            Row(
                Column('depreciation_start_date', css_class='form-group col-md-6'),
                Column('depreciation_method', css_class='form-group col-md-6'),
            ),
            Row(
                Column('useful_life_months', css_class='form-group col-md-6'),
                Column('salvage_value', css_class='form-group col-md-6'),
            ),
            Row(
                Column('asset_account', css_class='form-group col-md-4'),
                Column('acc_dep_account', css_class='form-group col-md-4'),
                Column('dep_expense_account', css_class='form-group col-md-4'),
            ),
        )

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={'multiple': True, 'class': 'form-control'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            if not data and self.required:
                raise ValidationError(self.error_messages['empty'], code='empty')
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [single_file_clean(data, initial)]
        return result

class CapitalizationUploadForm(forms.Form):
    commercial_invoices = MultipleFileField(
        label='1. Commercial Invoices',
        required=True,
        help_text='Upload the main commercial invoices.'
    )
    customs_declarations = MultipleFileField(
        label='2. Customs Declarations',
        required=False,
        help_text='Upload related customs declarations (CDC).'
    )
    freight_insurance = MultipleFileField(
        label='3. Freight & Insurance',
        required=False,
        help_text='Upload freight and insurance invoices.'
    )
    auxiliary_documents = MultipleFileField(
        label='4. Other Auxiliary Documents',
        required=False,
        help_text='Upload any other supporting documents (e.g., THC, port charges, trucking, reimbursement).'
    )
    batch_name = forms.CharField(
        max_length=100, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional custom batch name'})
    )

from assets.models import Capitalization, AssetBatch

class CapitalizationForm(forms.ModelForm):
    form_number = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control form-control-sm text-center fw-bold', 'readonly': 'readonly'}))

    class Meta:
        model = Capitalization
        exclude = ['user', 'created_at']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'batch': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'readonly': 'readonly'}),
            'company': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'invoice_no': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'vattin': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'debit_account_id': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'wht_debit_account_id': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'vat_debit_account_id': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'credit_account_id': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'description': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 2, 'title': 'Description'}),
            'description_en': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 2}),
            'instruction': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 2}),
            'capitalization': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 2, 'title': 'Basis of Capitalization'}),
            'payment_status': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'unreg_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'exempt_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'vat_base_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'vat_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'wht_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'total_usd': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01'}),
            'page': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'vendor': forms.Select(attrs={'class': 'form-select select2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Determine sequence number for the UI
        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        
        self.helper.layout = Layout(
            Div(
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
                    Column('vendor', css_class='form-group col-md-3'),
                    Column('payment_status', css_class='form-group col-md-2'),
                    Column('page', css_class='form-group col-md-1'),
                    Column('DELETE', css_class='form-group col-md-2 text-center bg-danger bg-opacity-10 text-danger fw-bold rounded pt-2 pb-2'),
                ),
                Row(
                    Column('debit_account_id', css_class='form-group col-md-3'),
                    Column('credit_account_id', css_class='form-group col-md-3'),
                    Column('vat_debit_account_id', css_class='form-group col-md-3'),
                    Column('wht_debit_account_id', css_class='form-group col-md-3'),
                ),
                Row(
                    Column('unreg_usd', css_class='form-group col-md-2'),
                    Column('exempt_usd', css_class='form-group col-md-2'),
                    Column('vat_base_usd', css_class='form-group col-md-2'),
                    Column('vat_usd', css_class='form-group col-md-2'),
                    Column('wht_usd', css_class='form-group col-md-2'),
                    Column('total_usd', css_class='form-group col-md-2'),
                ),
                Row(   
                    Column('description', css_class='form-group col-md-4'),
                    Column('description_en', css_class='form-group col-md-4'),
                    Column('capitalization', css_class='form-group col-md-4'),
                ),
                Row(
                    Column('instruction', css_class='form-group col-md-12'),
                ),
                css_class='p-3 bg-white mb-3 shadow-sm rounded border'
            )
        )

class AssetBatchForm(forms.ModelForm):
    class Meta:
        model = AssetBatch
        exclude = ['user', 'created_at']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'batch_id': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'readonly': 'readonly'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if field not in ['date', 'batch_id']:
                self.fields[field].widget.attrs.update({'class': 'form-control form-control-sm'})
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
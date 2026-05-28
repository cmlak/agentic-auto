from django import forms
from django.forms import formset_factory
from assets.models import Asset, AssetDisposal
from tools.models import Purchase
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, Field

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
from django import forms
from assets.models import Asset, AssetDisposal
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit

class AssetRegistrationForm(forms.ModelForm):
    class Meta:
        model = Asset
        exclude = ['status']
        widgets = {
            'depreciation_start_date': forms.DateInput(attrs={'type': 'date'}),
        }

class RunDepreciationForm(forms.Form):
    run_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text="End of month date for the depreciation run."
    )

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
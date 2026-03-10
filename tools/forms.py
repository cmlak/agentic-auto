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

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        super().__init__(*args, **kwargs)
        
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Row(
                Column('form_number', css_class='form-group col-md-1'),
                Column('batch', css_class='form-group col-md-2'),
                Column('date', css_class='form-group col-md-2'),
                Column('invoice_no', css_class='form-group col-md-2'),
                Column('vattin', css_class='form-group col-md-3'),
                Column('page', css_class='form-group col-md-1'),
                Column('DELETE', css_class='form-group col-md-1 text-center bg-light rounded'),
                css_class='mt-4 border-top pt-3 border-2 border-primary' 
            ),
            Row(
                Column('company', css_class='form-group col-md-3'),
                Column('vendor_choice', css_class='form-group col-md-3'),
                Column('description', css_class='form-group col-md-3'),
                Column('description_en', css_class='form-group col-md-3'),
            ),
            Row(
                Column('non_vat_non_tax_payer_usd', css_class='form-group col-md-2'),
                Column('non_vat_tax_payer_usd', css_class='form-group col-md-2'),
                Column('local_purchase_usd', css_class='form-group col-md-2'),
                Column('local_purchase_vat_usd', css_class='form-group col-md-3'),
                Column('total_usd', css_class='form-group col-md-3'),
            ),
            Row(
                Column('instruction', css_class='form-group col-md-12'),
            ),
            Field('vendor', type="hidden")
        )

    class Meta:
        model = Purchase
        fields = ['batch', 'date', 'invoice_no', 'company', 'vendor', 'vattin', 
                  'description', 'description_en', 'instruction', 
                  'non_vat_non_tax_payer_usd', 'non_vat_tax_payer_usd', 
                  'local_purchase_usd', 'local_purchase_vat_usd', 'total_usd', 'page']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            # Add 'auto-expand' class to text areas
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'description_en': forms.Textarea(attrs={'rows': 1, 'class': 'auto-expand'}),
            'instruction': forms.Textarea(attrs={'rows': 1, 'placeholder': 'Optional notes...', 'class': 'auto-expand'}), 
            'vendor': forms.HiddenInput(), 
            # Change to TextInput and add 'number-format' class
            'non_vat_non_tax_payer_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'non_vat_tax_payer_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'local_purchase_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'local_purchase_vat_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'total_usd': forms.TextInput(attrs={'class': 'number-format text-end fw-bold text-primary'}),
        }

PurchaseFormSet = formset_factory(PurchaseReviewForm, extra=0, can_delete=True)

###


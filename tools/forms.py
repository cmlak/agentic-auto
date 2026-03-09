from django import forms
from django.forms import formset_factory
from .models import Purchase

class BatchUploadForm(forms.Form):
    invoice_pdf = forms.FileField(label="Upload Invoice Batch (PDF)")

class PurchaseReviewForm(forms.ModelForm):
    # This field is for the dynamic dropdown. It's not part of the model.
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False)

    def __init__(self, *args, **kwargs):
        # Pop the custom kwarg before calling super().__init__ to avoid the error
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        super().__init__(*args, **kwargs)
        
        # If choices were passed from the view, update the field
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        
        # Set the initial value for the choice field from the initial data dict
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

    class Meta:
        model = Purchase
        fields = ['date', 'invoice_no', 'company', 'vendor', 'vattin', 'description', 'description_en', 'non_vat_non_tax_payer_usd', 'non_vat_tax_payer_usd', 'local_purchase_usd', 'local_purchase_vat_usd', 'total_usd', 'page']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'vendor': forms.HiddenInput(), # Hide the actual model FK field from the user
        }

PurchaseFormSet = formset_factory(PurchaseReviewForm, extra=0, can_delete=True)
from django import forms

class VendorTaxUploadForm(forms.Form):
    vendor_file = forms.FileField(
        label='Upload Vendor CSV',
        help_text='Upload the existing vendor list (e.g., vendor_old_1.csv)'
    )
    tax_file = forms.FileField(
        label='Upload Tax CSV',
        help_text='Upload the cleaned tax data (e.g., tax_clean_1.csv)'
    )